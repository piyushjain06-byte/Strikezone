def subscription_context(request):
    plan = 'free'
    is_employee = False

    if request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser):
        # Admin/CEO always gets full access regardless of subscription
        sub = getattr(request.user, 'subscription', None)
        if sub:
            plan = sub.effective_plan()
        else:
            plan = 'pro_plus'  # Admin with no subscription still gets full access

    elif request.session.get('player_mobile'):
        try:
            from accounts.models import GuestUser
            mobile = request.session['player_mobile']
            guest  = GuestUser.objects.filter(mobile_number=mobile).first()
            if guest:
                plan        = guest.effective_plan()
                is_employee = guest.role == GuestUser.ROLE_EMPLOYEE
        except Exception:
            pass

    plan_label = {'free': 'Free', 'pro': 'Pro', 'pro_plus': 'Pro Plus'}.get(plan, 'Free')

    # Employees get full pro_plus access regardless of their subscription plan
    is_privileged = is_employee or (
        request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser)
    )

    return {
        'user_plan':        plan,
        'user_plan_label':  plan_label,
        'can_manage':       plan == 'pro_plus' or is_privileged,
        'can_use_ml':       plan in ('pro', 'pro_plus') or is_privileged,
        'can_use_crickbot': plan in ('pro', 'pro_plus') or is_privileged,
        'is_employee':      is_employee,
    }