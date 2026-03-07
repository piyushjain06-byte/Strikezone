def subscription_context(request):
    plan = 'free'
    is_employee = False

    if request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser):
        sub = getattr(request.user, 'subscription', None)
        if sub:
            plan = sub.effective_plan()

    elif request.session.get('player_mobile'):
        try:
            from accounts.models import GuestUser
            mobile = request.session['player_mobile']
            guest  = GuestUser.objects.filter(mobile_number=mobile).first()
            if guest:
                plan = guest.effective_plan()
                is_employee = guest.role == GuestUser.ROLE_EMPLOYEE
        except Exception:
            pass

    plan_label = {'free': 'Free', 'pro': 'Pro', 'pro_plus': 'Pro Plus'}.get(plan, 'Free')

    return {
        'user_plan':        plan,
        'user_plan_label':  plan_label,
        'can_manage':       plan == 'pro_plus',
        'can_use_ml':       plan in ('pro', 'pro_plus'),
        'can_use_crickbot': plan in ('pro', 'pro_plus'),
        'is_employee':      is_employee,
    }