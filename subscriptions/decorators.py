from functools import wraps
from django.shortcuts import redirect
from django.http import JsonResponse
from django.contrib import messages


def _get_guest(request):
    """Return the GuestUser for the current session, or None."""
    mobile = request.session.get('player_mobile')
    if not mobile:
        return None
    try:
        from accounts.models import GuestUser
        return GuestUser.objects.filter(mobile_number=mobile).first()
    except Exception:
        return None


def _is_privileged(request):
    """
    Returns True if the user should bypass plan checks entirely:
    - Django admin / staff / superuser (CEO)
    - GuestUser with role = 'employee'
    """
    if request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser):
        return True
    guest = _get_guest(request)
    if guest and guest.role == 'employee':
        return True
    return False


def _get_effective_plan(request):
    """
    Returns the effective plan for whoever is making this request.
    Admins and employees return 'pro_plus' automatically.
    """
    if _is_privileged(request):
        return 'pro_plus'

    # Session-based player
    guest = _get_guest(request)
    if guest:
        return guest.effective_plan()

    return 'free'


def require_plan(*plans):
    """
    Decorator that checks the current user's subscription plan.

    Employees and admins (CEO) ALWAYS bypass this check — they get
    full access to all features regardless of subscription.

    Usage:
        @require_plan('pro', 'pro_plus')   <- allows Pro AND Pro Plus
        @require_plan('pro_plus')          <- allows Pro Plus only
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            # Must be logged in as someone
            is_admin  = request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser)
            is_player = bool(request.session.get('player_mobile'))

            if not is_admin and not is_player:
                return redirect('admin_login')

            # Employees and admins bypass all plan checks
            if _is_privileged(request):
                return view_func(request, *args, **kwargs)

            effective = _get_effective_plan(request)

            if effective not in plans:
                if 'pro_plus' in plans:
                    required = 'Pro Plus'
                else:
                    required = 'Pro'

                is_ajax = (
                    request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                    or request.content_type == 'application/json'
                )
                if is_ajax:
                    return JsonResponse({
                        'error': f'This feature requires the {required} plan.',
                        'upgrade_required': True,
                        'required_plan': required,
                    }, status=403)

                messages.warning(
                    request,
                    f'This feature requires the {required} plan. Please upgrade to continue.'
                )
                return redirect('upgrade_plan')

            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator