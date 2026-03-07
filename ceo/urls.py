from django.urls import path
from . import views

urlpatterns = [
    path('dashboard/',                        views.dashboard,       name='ceo_dashboard'),
    path('users/',                            views.user_list,       name='ceo_users'),
    path('users/<int:user_id>/plan/',         views.change_user_plan, name='ceo_change_plan'),
    path('users/<int:user_id>/promote/',      views.promote_employee, name='ceo_promote'),
    path('users/<int:user_id>/demote/',       views.demote_employee,  name='ceo_demote'),
]
