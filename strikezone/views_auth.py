from django.shortcuts import redirect, render, get_object_or_404
from django.http import JsonResponse
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.views.decorators.http import require_POST
from django.db import models as django_models
from django.db import IntegrityError
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.contrib.auth.hashers import check_password, identify_hasher, make_password
from functools import wraps
from urllib.parse import quote

from tournaments.models import TournamentDetails, StartTournament, TournamentAward
from teams.models import TeamDetails, PlayerDetails, TournamentTeam, TournamentRoster
from matches.models import CreateMatch, MatchStart, MatchResult, ManOfTheMatch
from scoring.models import Innings, Over, Ball, BattingScorecard, BowlingScorecard
from knockout.models import KnockoutStage, KnockoutMatch
from accounts.models import GuestUser

from strikezone.forms import MatchForm, TournamentForm, TeamForm, PlayerForm
from strikezone.services import begin_innings, start_over, record_ball, undo_last_ball

import json
import random
import os
from datetime import date, datetime, timedelta
from groq import Groq as GroqClient

from .views_core import admin_required


def ensure_player_for_mobile(mobile, guest=None):
    """
    Return a real PlayerDetails record for this mobile number, creating one
    if it doesn't exist yet.

    Every logged-in account needs a PlayerDetails row so that ownership
    checks (tournament creator, hired staff, etc.) — which are keyed off
    session['player_id'] pointing at a real PlayerDetails id — work
    correctly. Without this, a pro_plus subscriber who creates a
    tournament before ever being added to a team's roster would fall back
    to the 'guest' session marker and be silently locked out of managing
    their own tournament.
    """
    player = PlayerDetails.objects.filter(mobile_number=mobile).first()
    if player:
        return player

    if guest is None:
        guest = GuestUser.objects.filter(mobile_number=mobile).first()

    player_name = (guest.display_name if guest and guest.display_name else None) or f"Player {mobile}"
    player = PlayerDetails.objects.create(
        player_name=player_name,
        mobile_number=mobile,
    )
    if guest and guest.photo:
        from django.core.files.base import ContentFile
        guest.photo.open('rb')
        player.photo.save(guest.photo.name.split('/')[-1], ContentFile(guest.photo.read()), save=True)
        guest.photo.close()
    return player


def admin_login(request):
    next_param = request.GET.get("next") or request.POST.get("next") or ""

    if request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser):
        if next_param and url_has_allowed_host_and_scheme(next_param, allowed_hosts={request.get_host()}):
            return redirect(next_param)
        return redirect('manage_cricket')

    error = None
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        user = authenticate(request, username=username, password=password)
        if user is not None and (user.is_staff or user.is_superuser):
            # When admin logs in, force any existing player/guest session to log out
            for key in ('player_id', 'player_name', 'player_mobile'):
                request.session.pop(key, None)
            auth_login(request, user)
            if next_param and url_has_allowed_host_and_scheme(next_param, allowed_hosts={request.get_host()}):
                return redirect(next_param)
            return redirect('manage_cricket')
        elif user is not None:
            error = "Your account does not have admin privileges."
        else:
            error = "Invalid username or password."

    return render(request, 'admin_login.html', {'error': error, 'next': next_param})


@require_POST
def admin_logout(request):
    auth_logout(request)
    return redirect('admin_login')


# ── PLAYER LOGIN ──

def player_login(request):
    next_param = request.GET.get("next") or request.POST.get("next") or ""

    if request.session.get('player_id'):
        if next_param and url_has_allowed_host_and_scheme(next_param, allowed_hosts={request.get_host()}):
            return redirect(next_param)
        return redirect('player_stats')

    error = None
    if request.method == 'POST':
        mobile = (request.POST.get('mobile', '') or '').strip()
        password = (request.POST.get('password', '') or '').strip()

        if not mobile or len(mobile) < 10 or not mobile.replace("+", "").isdigit():
            error = "Enter a valid mobile number."
        elif not password:
            error = "Password is required."
        else:
            guest = GuestUser.objects.filter(mobile_number=mobile).first()
            player = PlayerDetails.objects.filter(mobile_number=mobile).first()

            if not guest and not player:
                error = "Account not found. Please register first."
            else:
                password_ok = False

                if guest:
                    try:
                        identify_hasher(guest.password)
                        password_ok = check_password(password, guest.password)
                    except Exception:
                        password_ok = (guest.password == password)
                        if password_ok:
                            guest.password = make_password(password)
                            guest.save(update_fields=["password"])
                else:
                    if len(password) < 6:
                        error = "Password must be at least 6 characters."
                    else:
                        guest = GuestUser(mobile_number=mobile)
                        guest.password = make_password(password)
                        guest.is_mobile_verified = False
                        guest.save()
                        password_ok = True

                if password_ok and not error:
                    # When player/guest logs in, force any existing Django user (admin) to log out
                    if request.user.is_authenticated:
                        auth_logout(request)

                    request.session.cycle_key()

                    # Every logged-in account gets a real PlayerDetails record so
                    # ownership checks (tournament creator, hired staff) work —
                    # a bare 'guest' session id can never own anything.
                    player = ensure_player_for_mobile(mobile, guest)
                    request.session['player_id'] = player.id
                    request.session['player_name'] = player.player_name

                    request.session['player_mobile'] = mobile

                    if next_param and url_has_allowed_host_and_scheme(next_param, allowed_hosts={request.get_host()}):
                        return redirect(next_param)
                    return redirect('home')

                if not password_ok and not error:
                    error = "Invalid mobile number or password."

    return render(request, 'player_login.html', {'error': error, 'next': next_param})


def start_otp_verification(mobile):
    """
    Start an OTP verification using Twilio Verify (works on trial accounts,
    unlike raw custom SMS which trial accounts can no longer send).
    Returns (True, None) on success or (False, error_message) on failure.
    """
    import requests as http_requests
    from django.conf import settings
    import logging
    logger = logging.getLogger(__name__)

    account_sid = getattr(settings, 'TWILIO_ACCOUNT_SID', None)
    auth_token  = getattr(settings, 'TWILIO_AUTH_TOKEN', None)
    service_sid = getattr(settings, 'TWILIO_VERIFY_SERVICE_SID', None)

    if not all([account_sid, auth_token, service_sid]):
        return False, "Twilio Verify credentials not configured"

    try:
        number = mobile.strip().replace(' ', '')
        if not number.startswith('+'):
            number = '+91' + number  # default to India country code

        url = f"https://verify.twilio.com/v2/Services/{service_sid}/Verifications"
        payload = {"To": number, "Channel": "sms"}

        response = http_requests.post(
            url,
            data=payload,
            auth=(account_sid, auth_token),
            timeout=10
        )

        logger.info(f"[Twilio Verify] Start status: {response.status_code} | Response: {response.text}")
        data = response.json()

        if response.status_code == 201:
            return True, None
        else:
            error_msg = data.get('message', str(data))
            logger.error(f"[Twilio Verify] Start failed: {data}")
            return False, error_msg

    except Exception as e:
        logger.error(f"[Twilio Verify] Start exception: {e}")
        return False, str(e)


def check_otp_verification(mobile, code):
    """
    Check a submitted OTP code against Twilio Verify.
    Returns (True, None) if approved, (False, error_message) otherwise.
    """
    import requests as http_requests
    from django.conf import settings
    import logging
    logger = logging.getLogger(__name__)

    account_sid = getattr(settings, 'TWILIO_ACCOUNT_SID', None)
    auth_token  = getattr(settings, 'TWILIO_AUTH_TOKEN', None)
    service_sid = getattr(settings, 'TWILIO_VERIFY_SERVICE_SID', None)

    if not all([account_sid, auth_token, service_sid]):
        return False, "Twilio Verify credentials not configured"

    try:
        number = mobile.strip().replace(' ', '')
        if not number.startswith('+'):
            number = '+91' + number

        url = f"https://verify.twilio.com/v2/Services/{service_sid}/VerificationCheck"
        payload = {"To": number, "Code": code}

        response = http_requests.post(
            url,
            data=payload,
            auth=(account_sid, auth_token),
            timeout=10
        )

        logger.info(f"[Twilio Verify] Check status: {response.status_code} | Response: {response.text}")
        data = response.json()

        if response.status_code == 200 and data.get('status') == 'approved':
            return True, None
        else:
            error_msg = data.get('message', data.get('status', str(data)))
            return False, error_msg

    except Exception as e:
        logger.error(f"[Twilio Verify] Check exception: {e}")
        return False, str(e)


def player_request_otp(request):
    if request.method == 'POST':
        mobile = (request.POST.get('mobile', '') or '').strip()
        if not mobile or len(mobile) < 10 or not mobile.replace("+", "").isdigit():
            messages.error(request, "Enter a valid mobile number.")
        else:
            guest = GuestUser.objects.filter(mobile_number=mobile).first()
            player = PlayerDetails.objects.filter(mobile_number=mobile).first()
            if not guest and not player:
                messages.error(request, "No account found with this mobile. Please ask admin to link your profile or register.")
            else:
                request.session['otp_mobile'] = mobile
                request.session['otp_expires_at'] = (timezone.now() + timedelta(minutes=10)).isoformat()
                request.session['otp_attempts'] = 0

                sms_sent, sms_error = start_otp_verification(mobile)

                if sms_sent:
                    messages.success(request, f"OTP sent to {mobile}. Valid for 10 minutes.")
                else:
                    messages.error(request, f"Could not send OTP. Reason: {sms_error}")
                    for key in ('otp_mobile', 'otp_expires_at', 'otp_attempts'):
                        request.session.pop(key, None)
                    return render(request, 'player_request_otp.html')

                return redirect('player_verify_otp')

    return render(request, 'player_request_otp.html')


def player_verify_otp(request):
    mobile = request.session.get('otp_mobile')
    expires_raw = request.session.get('otp_expires_at')
    attempts = request.session.get('otp_attempts', 0)

    if not mobile or not expires_raw:
        messages.error(request, "OTP session expired. Please request a new code.")
        return redirect('player_request_otp')

    try:
        expires_at = datetime.fromisoformat(expires_raw)
    except Exception:
        expires_at = timezone.now() - timedelta(seconds=1)

    if timezone.now() > expires_at:
        for key in ('otp_mobile', 'otp_expires_at', 'otp_attempts'):
            request.session.pop(key, None)
        messages.error(request, "OTP expired. Please request a new code.")
        return redirect('player_request_otp')

    if request.method == 'POST':
        user_code = (request.POST.get('otp', '') or '').strip()
        attempts += 1
        request.session['otp_attempts'] = attempts

        if attempts > 5:
            for key in ('otp_mobile', 'otp_expires_at', 'otp_attempts'):
                request.session.pop(key, None)
            messages.error(request, "Too many incorrect attempts. Please request a new code.")
            return redirect('player_request_otp')

        approved, check_error = check_otp_verification(mobile, user_code)

        if not approved:
            messages.error(request, "Incorrect OTP. Please try again.")
        else:
            for key in ('otp_mobile', 'otp_expires_at', 'otp_attempts'):
                request.session.pop(key, None)

            guest, _ = GuestUser.objects.get_or_create(mobile_number=mobile)
            if guest.password == "":
                guest.password = make_password(f"otp-{mobile}")
            guest.is_mobile_verified = True
            guest.save()

            if request.user.is_authenticated:
                auth_logout(request)

            request.session.cycle_key()

            player = ensure_player_for_mobile(mobile, guest)
            request.session['player_id'] = player.id
            request.session['player_name'] = player.player_name

            request.session['player_mobile'] = mobile

            messages.success(request, "Logged in successfully with OTP.")
            return redirect('home')

    return render(request, 'player_verify_otp.html', {'mobile': mobile})


def player_register(request):
    if request.session.get('player_id'):
        return redirect('player_stats')

    error   = None
    success = None

    if request.method == 'POST':
        mobile   = (request.POST.get('mobile', '') or '').strip()
        password = (request.POST.get('password', '') or '').strip()
        confirm  = (request.POST.get('confirm_password', '') or '').strip()

        if not mobile or not password or not confirm:
            error = "All fields are required."
        elif len(mobile) < 10 or not mobile.replace("+", "").isdigit():
            error = "Enter a valid mobile number (at least 10 digits)."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        elif password != confirm:
            error = "Passwords do not match."
        elif GuestUser.objects.filter(mobile_number=mobile).exists():
            error = "An account with this mobile number already exists. Please login."
        else:
            guest = GuestUser(mobile_number=mobile)
            guest.password = make_password(password)
            guest.is_mobile_verified = False
            guest.save()
            success = "Account created! You can now login with your password."

    return render(request, 'player_register.html', {'error': error, 'success': success})


@require_POST
def player_logout(request):
    for key in ('player_id', 'player_name', 'player_mobile'):
        request.session.pop(key, None)
    request.session.cycle_key()
    return redirect('player_login')


# ── PLAYER STATS ──