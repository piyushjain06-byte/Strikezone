"""
Razorpay payment utilities.
Uses requests directly (no razorpay SDK needed).
"""

import hmac
import hashlib
import json
import requests
from django.conf import settings

RAZORPAY_API = "https://api.razorpay.com/v1"

PLAN_PRICES = {
    'pro':      19900,  # ₹199 in paise
    'pro_plus': 49900,  # ₹499 in paise
}

PLAN_NAMES = {
    'pro':      'Pro',
    'pro_plus': 'Pro Plus',
}


def get_client():
    return (settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)


def create_order(plan, user_identifier):
    """
    Creates a Razorpay order and returns the order dict.
    plan: 'pro' or 'pro_plus'
    user_identifier: mobile number or username for notes
    """
    key_id, key_secret = get_client()
    amount = PLAN_PRICES.get(plan)
    if not amount:
        raise ValueError(f"Unknown plan: {plan}")

    payload = {
        "amount":   amount,
        "currency": "INR",
        "notes": {
            "plan":            plan,
            "user_identifier": str(user_identifier),
        }
    }

    response = requests.post(
        f"{RAZORPAY_API}/orders",
        auth=(key_id, key_secret),
        json=payload,
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def verify_payment_signature(order_id, payment_id, signature):
    """
    Verifies the payment signature from Razorpay.
    Returns True if signature is valid, False otherwise.
    """
    _, key_secret = get_client()
    message = f"{order_id}|{payment_id}"
    expected = hmac.new(
        key_secret.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_webhook_signature(payload_body, signature):
    """
    Verifies a Razorpay webhook signature.
    payload_body: raw bytes of request body
    """
    webhook_secret = getattr(settings, 'RAZORPAY_WEBHOOK_SECRET', '')
    if not webhook_secret:
        return True  # skip verification if not configured

    expected = hmac.new(
        webhook_secret.encode('utf-8'),
        payload_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)