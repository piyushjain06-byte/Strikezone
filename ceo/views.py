from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.db.models import Count
from datetime import timedelta

from accounts.models import GuestUser
from subscriptions.models import Subscription


def ceo_required(view_func):
    """Only Django superusers (CEO) can access."""
    from functools import wraps
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated and request.user.is_superuser:
            return view_func(request, *args, **kwargs)
        messages.error(request, "CEO access only.")
        return redirect('home')
    return wrapper


# ─────────────────────────────────────────────────────────────
# CEO DASHBOARD
# ─────────────────────────────────────────────────────────────

@ceo_required
def dashboard(request):
    now = timezone.now()

    total_users   = GuestUser.objects.count()
    free_count    = GuestUser.objects.filter(plan='free').count()
    pro_count     = GuestUser.objects.filter(plan='pro').exclude(plan_expires_at__lt=now).count()
    proplus_count = GuestUser.objects.filter(plan='pro_plus').exclude(plan_expires_at__lt=now).count()
    employee_count = GuestUser.objects.filter(role='employee').count()

    estimated_revenue = (pro_count * 199) + (proplus_count * 499)

    expiring_soon = GuestUser.objects.exclude(plan='free').filter(
        plan_expires_at__gte=now,
        plan_expires_at__lte=now + timedelta(days=7),
    ).order_by('plan_expires_at')[:10]

    recently_upgraded = GuestUser.objects.exclude(plan='free').filter(
        plan_expires_at__gte=now,
    ).order_by('-plan_expires_at')[:10]

    employees = GuestUser.objects.filter(role='employee').order_by('mobile_number')

    # Django admin subscriptions
    admin_subs = Subscription.objects.select_related('user').all()

    return render(request, 'ceo/dashboard.html', {
        'total_users':        total_users,
        'free_count':         free_count,
        'pro_count':          pro_count,
        'proplus_count':      proplus_count,
        'employee_count':     employee_count,
        'estimated_revenue':  estimated_revenue,
        'expiring_soon':      expiring_soon,
        'recently_upgraded':  recently_upgraded,
        'employees':          employees,
        'admin_subs':         admin_subs,
    })


# ─────────────────────────────────────────────────────────────
# PROMOTE / DEMOTE EMPLOYEE
# ─────────────────────────────────────────────────────────────

@ceo_required
def promote_employee(request, user_id):
    guest = get_object_or_404(GuestUser, id=user_id)

    # CEO cannot promote themselves (they're a Django superuser, not a GuestUser)
    guest.role = GuestUser.ROLE_EMPLOYEE
    guest.save(update_fields=['role'])
    messages.success(request, f"{guest.mobile_number} promoted to Employee.")
    return redirect('ceo_dashboard')


@ceo_required
def demote_employee(request, user_id):
    guest = get_object_or_404(GuestUser, id=user_id)
    guest.role = GuestUser.ROLE_USER
    guest.save(update_fields=['role'])
    messages.success(request, f"{guest.mobile_number} demoted to User.")
    return redirect('ceo_dashboard')


# ─────────────────────────────────────────────────────────────
# ALL USERS (CEO view — includes promote button)
# ─────────────────────────────────────────────────────────────

@ceo_required
def user_list(request):
    query       = request.GET.get('q', '').strip()
    filter_plan = request.GET.get('plan', '')
    filter_role = request.GET.get('role', '')

    users = GuestUser.objects.all().order_by('-created_at')
    if query:
        users = users.filter(mobile_number__icontains=query)
    if filter_plan in ('free', 'pro', 'pro_plus'):
        users = users.filter(plan=filter_plan)
    if filter_role in ('user', 'employee'):
        users = users.filter(role=filter_role)

    return render(request, 'ceo/user_list.html', {
        'users':       users,
        'query':       query,
        'filter_plan': filter_plan,
        'filter_role': filter_role,
    })


@ceo_required
def change_user_plan(request, user_id):
    if request.method != 'POST':
        return redirect('ceo_users')
    guest    = get_object_or_404(GuestUser, id=user_id)
    new_plan = request.POST.get('plan')
    if new_plan in ('free', 'pro', 'pro_plus'):
        guest.plan = new_plan
        guest.plan_expires_at = None if new_plan == 'free' else timezone.now() + timedelta(days=30)
        guest.save(update_fields=['plan', 'plan_expires_at'])
        messages.success(request, f"{guest.mobile_number} → {new_plan}")
    return redirect('ceo_users')
