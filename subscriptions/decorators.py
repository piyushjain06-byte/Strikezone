from functools import wraps
from django.shortcuts import redirect
from django.http import JsonResponse
from django.contrib import messages


def _get_effective_plan(request):
    """
    Returns the effective plan for whoever is making this request.
    Checks Django auth users first, then session-based players.
    Always returns 'free', 'pro', or 'pro_plus'.
    """
    # Django admin/staff user
    if request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser):
        sub = getattr(request.user, 'subscription', None)
        if sub:
            return sub.effective_plan()
        return 'free'

    # Session-based player
    player_mobile = request.session.get('player_mobile')
    if player_mobile:
        from accounts.models import GuestUser
        guest = GuestUser.objects.filter(mobile_number=player_mobile).first()
        if guest:
            return guest.plan

    return 'free'


def require_plan(*plans):
    """
    Decorator that checks the current user's (admin OR player) subscription plan.

    Usage:
        @require_plan('pro', 'pro_plus')   <- allows Pro AND Pro Plus
        @require_plan('pro_plus')          <- allows Pro Plus only

    Works for both:
        - Django admin users (checks Subscription model)
        - Session-based players (checks GuestUser.plan)
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            # Must be logged in as someone
            is_admin = request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser)
            is_player = bool(request.session.get('player_mobile'))

            if not is_admin and not is_player:
                return redirect('admin_login')

            effective = _get_effective_plan(request)

            if effective not in plans:
                if 'pro_plus' in plans:
                    required = 'Pro Plus'
                else:
                    required = 'Pro'

                # AJAX / fetch call - return JSON
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

                # Normal page - redirect to upgrade page
                messages.warning(
                    request,
                    f'This feature requires the {required} plan. Please upgrade to continue.'
                )
                return redirect('upgrade_plan')

            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator