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


def _player_owns_tournament(request, tournament_id):
    """
    Returns True if the current pro_plus player created this tournament,
    OR is hired staff for it, OR if the user is privileged (admin/employee).
    """
    if _is_privileged(request):
        return True
    pid = request.session.get('player_id')
    if not pid or pid == 'guest':
        return False
    try:
        from tournaments.models import TournamentDetails, TournamentHire
        # Creator check
        if TournamentDetails.objects.filter(id=tournament_id, created_by_player_id=pid).exists():
            return True
        # Hired staff check
        if TournamentHire.objects.filter(tournament_id=tournament_id, hired_player_id=pid).exists():
            return True
        return False
    except Exception:
        return False


def _player_is_creator(request, tournament_id):
    """
    Returns True ONLY if the player is the original creator (not just hired staff).
    Used to restrict hire/remove buttons to creator only.
    """
    if _is_privileged(request):
        return True
    pid = request.session.get('player_id')
    if not pid or pid == 'guest':
        return False
    try:
        from tournaments.models import TournamentDetails
        return TournamentDetails.objects.filter(
            id=tournament_id, created_by_player_id=pid
        ).exists()
    except Exception:
        return False


def _get_tournament_id_from_kwargs(kwargs):
    """Extract tournament_id from view kwargs — directly or via match_id."""
    # Direct tournament_id kwarg
    if 'tournament_id' in kwargs:
        return kwargs['tournament_id']
    # Via match_id → look up tournament
    if 'match_id' in kwargs:
        try:
            from matches.models import CreateMatch
            return CreateMatch.objects.filter(
                id=kwargs['match_id']
            ).values_list('tournament_id', flat=True).first()
        except Exception:
            pass
    return None


def require_plan(*plans):
    """
    Decorator that checks the current user's subscription plan.

    Employees and admins (CEO) ALWAYS bypass this check.

    For pro_plus players: if the view involves a specific tournament or match,
    the player must have CREATED that tournament to get pro_plus access.
    For tournaments they did NOT create, they are treated as pro-level only.

    Usage:
        @require_plan('pro', 'pro_plus')   <- allows Pro AND Pro Plus
        @require_plan('pro_plus')          <- allows Pro Plus only
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            is_admin  = request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser)
            is_player = bool(request.session.get('player_mobile'))

            if not is_admin and not is_player:
                return redirect('admin_login')

            # Employees and admins bypass all plan checks
            if _is_privileged(request):
                return view_func(request, *args, **kwargs)

            effective = _get_effective_plan(request)

            # Pro Plus player — check tournament ownership if a tournament/match is involved
            if effective == 'pro_plus' and 'pro_plus' in plans:
                tournament_id = _get_tournament_id_from_kwargs(kwargs)
                if tournament_id:
                    # Must own this tournament to get pro_plus powers
                    if not _player_owns_tournament(request, tournament_id):
                        # Treat as pro for this tournament
                        is_ajax = (
                            request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                            or request.content_type == 'application/json'
                        )
                        if is_ajax:
                            return JsonResponse({
                                'error': 'You can only manage tournaments you created.',
                                'upgrade_required': False,
                            }, status=403)
                        messages.warning(
                            request,
                            'You can only manage tournaments you have created.'
                        )
                        return redirect('upgrade_plan')
                # No tournament_id in kwargs (e.g. manage_cricket listing page) — allow
                return view_func(request, *args, **kwargs)

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