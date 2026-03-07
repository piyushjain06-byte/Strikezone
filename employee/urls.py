from django.urls import path
from . import views

urlpatterns = [
    path('dashboard/',               views.dashboard,        name='employee_dashboard'),
    path('users/',                   views.user_list,        name='employee_users'),
    path('users/<int:user_id>/plan/', views.change_user_plan, name='employee_change_plan'),
]
