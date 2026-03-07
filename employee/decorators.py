from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages
from accounts.models import GuestUser


def employee_required(view_func):
    """Allows only employees AND superusers (CEO)."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        # CEO / superuser always allowed
        if request.user.is_authenticated and request.user.is_superuser:
            return view_func(request, *args, **kwargs)

        # Session player who is an employee
        mobile = request.session.get('player_mobile')
        if mobile:
            guest = GuestUser.objects.filter(mobile_number=mobile).first()
            if guest and guest.role == GuestUser.ROLE_EMPLOYEE:
                return view_func(request, *args, **kwargs)

        messages.error(request, "Employee access required.")
        return redirect('home')
    return wrapper
