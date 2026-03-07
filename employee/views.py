from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from datetime import timedelta

from accounts.models import GuestUser
from subscriptions.models import Subscription
from matches.models import CreateMatch
from scoring.models import Innings
from .decorators import employee_required


# ─────────────────────────────────────────────────────────────
# EMPLOYEE DASHBOARD
# ─────────────────────────────────────────────────────────────

@employee_required
def dashboard(request):
    now = timezone.now()

    # ── Plan counts ──
    total_users  = GuestUser.objects.count()
    free_count   = GuestUser.objects.filter(plan='free').count()
    pro_count    = GuestUser.objects.filter(plan='pro').exclude(plan_expires_at__lt=now).count()
    proplus_count = GuestUser.objects.filter(plan='pro_plus').exclude(plan_expires_at__lt=now).count()

    # ── Revenue estimate (paise → rupees) ──
    estimated_revenue = (pro_count * 199) + (proplus_count * 499)

    # ── Recently upgraded (last 7 days, non-free) ──
    recently_upgraded = GuestUser.objects.exclude(
        plan='free'
    ).filter(
        plan_expires_at__gte=now,
        plan_expires_at__lte=now + timedelta(days=37),  # upgraded in last ~7 days
    ).order_by('-plan_expires_at')[:10]

    # ── Expiring soon (next 7 days) ──
    expiring_soon = GuestUser.objects.exclude(
        plan='free'
    ).filter(
        plan_expires_at__gte=now,
        plan_expires_at__lte=now + timedelta(days=7),
    ).order_by('plan_expires_at')[:10]

    # ── Most active tournaments (by match count) ──
    from tournaments.models import TournamentDetails
    tournaments = TournamentDetails.objects.all()
    tournament_activity = []
    for t in tournaments:
        match_count = CreateMatch.objects.filter(tournament=t).count()
        live_count  = Innings.objects.filter(match__tournament=t, status='IN_PROGRESS').count()
        tournament_activity.append({
            'tournament':   t,
            'match_count':  match_count,
            'is_live':      live_count > 0,
        })
    tournament_activity.sort(key=lambda x: -x['match_count'])
    tournament_activity = tournament_activity[:5]

    return render(request, 'employee/dashboard.html', {
        'total_users':         total_users,
        'free_count':          free_count,
        'pro_count':           pro_count,
        'proplus_count':       proplus_count,
        'estimated_revenue':   estimated_revenue,
        'recently_upgraded':   recently_upgraded,
        'expiring_soon':       expiring_soon,
        'tournament_activity': tournament_activity,
    })


# ─────────────────────────────────────────────────────────────
# USER MANAGEMENT — view & change plans
# ─────────────────────────────────────────────────────────────

@employee_required
def user_list(request):
    query  = request.GET.get('q', '').strip()
    filter_plan = request.GET.get('plan', '')

    users = GuestUser.objects.all().order_by('-created_at')

    if query:
        users = users.filter(mobile_number__icontains=query)
    if filter_plan in ('free', 'pro', 'pro_plus'):
        users = users.filter(plan=filter_plan)

    return render(request, 'employee/user_list.html', {
        'users':       users,
        'query':       query,
        'filter_plan': filter_plan,
    })


@employee_required
def change_user_plan(request, user_id):
    """Employee can manually change any player's plan (no payment needed)."""
    if request.method != 'POST':
        return redirect('employee_users')

    guest    = get_object_or_404(GuestUser, id=user_id)
    new_plan = request.POST.get('plan')

    # Employees cannot touch other employees or their own account
    mobile = request.session.get('player_mobile')
    if guest.mobile_number == mobile:
        messages.error(request, "You cannot change your own plan.")
        return redirect('employee_users')
    if guest.role == GuestUser.ROLE_EMPLOYEE:
        messages.error(request, "You cannot change another employee's plan.")
        return redirect('employee_users')

    if new_plan in ('free', 'pro', 'pro_plus'):
        guest.plan = new_plan
        if new_plan == 'free':
            guest.plan_expires_at = None
        else:
            guest.plan_expires_at = timezone.now() + timedelta(days=30)
        guest.save(update_fields=['plan', 'plan_expires_at'])
        messages.success(request, f"{guest.mobile_number} plan changed to {new_plan}.")
    else:
        messages.error(request, "Invalid plan.")

    return redirect('employee_users')
