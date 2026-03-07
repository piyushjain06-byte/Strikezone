import json
import logging
from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils import timezone
from datetime import timedelta
from django.conf import settings

from .models import Subscription
from .razorpay_utils import (
    create_order, verify_payment_signature,
    verify_webhook_signature, PLAN_PRICES, PLAN_NAMES
)
from accounts.models import GuestUser

logger = logging.getLogger(__name__)


def _get_guest(request):
    """Returns GuestUser for session player, or None."""
    mobile = request.session.get('player_mobile')
    if mobile:
        return GuestUser.objects.filter(mobile_number=mobile).first()
    return None


def _is_admin(request):
    return request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser)


# ─────────────────────────────────────────────────────────────
# UPGRADE PAGE — shows current plan and options
# ─────────────────────────────────────────────────────────────

def upgrade_plan(request):
    """Shows pricing page. Works for both admins and players."""
    is_admin_user = _is_admin(request)
    is_player     = bool(request.session.get('player_mobile'))

    if not is_admin_user and not is_player:
        return redirect('player_login')

    if is_admin_user:
        sub, _       = Subscription.objects.get_or_create(user=request.user)
        current_plan = sub.effective_plan()
        expires_at   = sub.expires_at
    else:
        guest = _get_guest(request)
        if not guest:
            return redirect('player_login')
        current_plan = guest.effective_plan()
        expires_at   = guest.plan_expires_at

    current_label = {'free': 'Free', 'pro': 'Pro', 'pro_plus': 'Pro Plus'}.get(current_plan, 'Free')

    return render(request, 'subscriptions/upgrade_plan.html', {
        'current_plan':  current_plan,
        'current_label': current_label,
        'expires_at':    expires_at,
        'is_admin':      is_admin_user,
        'is_player':     is_player,
    })


# ─────────────────────────────────────────────────────────────
# CHECKOUT — Terms & Conditions before paying
# ─────────────────────────────────────────────────────────────

def checkout(request, plan):
    """Shows T&C and payment button for the selected plan."""
    is_admin_user = _is_admin(request)
    is_player     = bool(request.session.get('player_mobile'))

    if not is_admin_user and not is_player:
        return redirect('player_login')

    if plan not in ('pro', 'pro_plus'):
        return redirect('upgrade_plan')

    # Get current plan — cannot downgrade via payment
    if is_admin_user:
        sub, _       = Subscription.objects.get_or_create(user=request.user)
        current_plan = sub.effective_plan()
    else:
        guest = _get_guest(request)
        if not guest:
            return redirect('player_login')
        current_plan = guest.effective_plan()

    if current_plan == 'pro_plus':
        messages.info(request, "You already have the highest plan.")
        return redirect('upgrade_plan')

    if current_plan == plan:
        messages.info(request, f"You are already on the {PLAN_NAMES[plan]} plan.")
        return redirect('upgrade_plan')

    plan_features = {
        'pro': [
            'Everything in Free',
            'Player ML Analysis',
            'Team ML Analysis',
            'Match ML Analysis',
            'CrickBot AI Assistant',
        ],
        'pro_plus': [
            'Everything in Pro',
            'Create & manage tournaments',
            'Create & manage teams',
            'Add & edit players',
            'Start & score matches',
            'Full cricket management access',
        ],
    }

    price_inr = PLAN_PRICES[plan] // 100  # convert paise to rupees

    return render(request, 'subscriptions/checkout.html', {
        'plan':         plan,
        'plan_name':    PLAN_NAMES[plan],
        'price_inr':    price_inr,
        'features':     plan_features[plan],
        'razorpay_key': settings.RAZORPAY_KEY_ID,
    })


# ─────────────────────────────────────────────────────────────
# CREATE ORDER — called by JS before opening Razorpay modal
# ─────────────────────────────────────────────────────────────

@require_POST
def create_payment_order(request):
    """Creates a Razorpay order and returns order_id to frontend."""
    is_admin_user = _is_admin(request)
    is_player     = bool(request.session.get('player_mobile'))

    if not is_admin_user and not is_player:
        return JsonResponse({'error': 'Not authenticated'}, status=401)

    try:
        data = json.loads(request.body)
        plan = data.get('plan')
    except Exception:
        plan = request.POST.get('plan')

    if plan not in ('pro', 'pro_plus'):
        return JsonResponse({'error': 'Invalid plan'}, status=400)

    # User identifier for Razorpay notes
    if is_admin_user:
        identifier = request.user.username
    else:
        identifier = request.session.get('player_mobile', 'unknown')

    try:
        order = create_order(plan, identifier)
        return JsonResponse({
            'order_id':  order['id'],
            'amount':    order['amount'],
            'currency':  order['currency'],
            'plan':      plan,
            'plan_name': PLAN_NAMES[plan],
        })
    except Exception as e:
        logger.error(f"Razorpay order creation failed: {e}")
        return JsonResponse({'error': 'Payment gateway error. Please try again.'}, status=500)


# ─────────────────────────────────────────────────────────────
# VERIFY PAYMENT — called after Razorpay payment completes
# ─────────────────────────────────────────────────────────────

@require_POST
def verify_payment(request):
    """Verifies Razorpay signature and upgrades the user's plan."""
    is_admin_user = _is_admin(request)
    is_player     = bool(request.session.get('player_mobile'))

    if not is_admin_user and not is_player:
        return JsonResponse({'error': 'Not authenticated'}, status=401)

    try:
        data       = json.loads(request.body)
        order_id   = data.get('razorpay_order_id')
        payment_id = data.get('razorpay_payment_id')
        signature  = data.get('razorpay_signature')
        plan       = data.get('plan')
    except Exception:
        return JsonResponse({'error': 'Invalid request'}, status=400)

    if not all([order_id, payment_id, signature, plan]):
        return JsonResponse({'error': 'Missing payment details'}, status=400)

    if plan not in ('pro', 'pro_plus'):
        return JsonResponse({'error': 'Invalid plan'}, status=400)

    # Verify signature
    if not verify_payment_signature(order_id, payment_id, signature):
        logger.warning(f"Invalid Razorpay signature. order={order_id} payment={payment_id}")
        return JsonResponse({'error': 'Payment verification failed. Please contact support.'}, status=400)

    # Upgrade the plan
    expires_at = timezone.now() + timedelta(days=30)

    try:
        if is_admin_user:
            sub, _       = Subscription.objects.get_or_create(user=request.user)
            sub.plan      = plan
            sub.is_active = True
            sub.expires_at = expires_at
            sub.save()
        else:
            guest = _get_guest(request)
            if not guest:
                return JsonResponse({'error': 'User not found'}, status=404)
            guest.plan           = plan
            guest.plan_expires_at = expires_at
            guest.save(update_fields=['plan', 'plan_expires_at'])

        logger.info(f"Plan upgraded to {plan} for {order_id}")
        return JsonResponse({
            'success':    True,
            'plan':       plan,
            'plan_name':  PLAN_NAMES[plan],
            'expires_at': expires_at.strftime('%d %b %Y'),
        })

    except Exception as e:
        logger.error(f"Plan upgrade failed after verified payment: {e}")
        return JsonResponse({'error': 'Plan upgrade failed. Please contact support.'}, status=500)


# ─────────────────────────────────────────────────────────────
# WEBHOOK — background verification from Razorpay servers
# ─────────────────────────────────────────────────────────────

@csrf_exempt
def razorpay_webhook(request):
    """
    Razorpay sends this after every payment event.
    Acts as a backup to verify_payment in case the user closes the tab.
    """
    if request.method != 'POST':
        return HttpResponse(status=405)

    signature = request.headers.get('X-Razorpay-Signature', '')
    if not verify_webhook_signature(request.body, signature):
        return HttpResponse(status=400)

    try:
        payload = json.loads(request.body)
        event   = payload.get('event')

        if event == 'payment.captured':
            payment = payload['payload']['payment']['entity']
            notes   = payment.get('notes', {})
            plan    = notes.get('plan')
            mobile  = notes.get('user_identifier')

            if plan and mobile:
                expires_at = timezone.now() + timedelta(days=30)
                GuestUser.objects.filter(mobile_number=mobile).update(
                    plan=plan,
                    plan_expires_at=expires_at,
                )
                logger.info(f"Webhook: upgraded {mobile} to {plan}")

    except Exception as e:
        logger.error(f"Webhook error: {e}")

    return HttpResponse(status=200)