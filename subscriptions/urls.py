from django.urls import path
from . import views

urlpatterns = [
    path('upgrade/',                 views.upgrade_plan,         name='upgrade_plan'),
    path('checkout/<str:plan>/',     views.checkout,             name='checkout'),
    path('create-order/',            views.create_payment_order, name='create_payment_order'),
    path('verify-payment/',          views.verify_payment,       name='verify_payment'),
    path('webhook/',                 views.razorpay_webhook,     name='razorpay_webhook'),
]