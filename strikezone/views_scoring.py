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
from subscriptions.decorators import require_plan
from .views_awards import award_man_of_the_match, _is_tournament_complete, award_tournament_awards
from .views_knockout import auto_advance_knockout
from strikezone.ws_push import push_ball, push_undo, push_innings_complete, push_match_complete, push_match_started, push_new_batsman, push_new_over

@admin_required
@require_plan('pro_plus')
def match_start(request):
    # Only show matches that have NOT been started yet (no MatchStart record)
    started_match_ids = MatchStart.objects.values_list('match_id', flat=True)
    matches = CreateMatch.objects.exclude(id__in=started_match_ids).select_related('team1', 'team2', 'tournament').order_by('tournament__tournament_name', 'match_date')
    # Pro Plus players only see matches from their own tournaments
    from subscriptions.decorators import _is_privileged
    if not _is_privileged(request):
        pid = request.session.get('player_id')
        if pid and pid != 'guest':
            from tournaments.models import TournamentDetails
            owned_ids = TournamentDetails.objects.filter(
                created_by_player_id=pid
            ).values_list('id', flat=True)
            matches = matches.filter(tournament_id__in=owned_ids)
    if request.method == "POST":
        match_id = request.POST.get("match_id")
        toss_winner_id = request.POST.get("toss_winner")
        decision = request.POST.get("decision")
        match = get_object_or_404(CreateMatch, id=match_id)
        # Ownership check for pro_plus players
        from subscriptions.decorators import _is_privileged, _player_owns_tournament
        if not _is_privileged(request):
            if not _player_owns_tournament(request, match.tournament_id):
                messages.warning(request, 'You can only manage tournaments you have created.')
                return redirect('upgrade_plan')
        toss_winner = get_object_or_404(TeamDetails, id=toss_winner_id)
        try:
            _ = match.match_start
            messages.error(request, "Match already started!")
            return redirect("match_start")
        except MatchStart.DoesNotExist:
            pass
        try:
            ms = MatchStart(match=match, toss_winner=toss_winner, decision=decision, is_match_started=True)
            ms.full_clean()
            ms.save()
            messages.success(request, "Match started successfully!")
            return redirect("start_innings", match_id=match.id)
        except ValidationError as e:
            messages.error(request, e)

    preselected_match_id = request.GET.get('match_id')
    try:
        preselected_match_id = int(preselected_match_id) if preselected_match_id else None
    except ValueError:
        preselected_match_id = None

    return render(request, "match_start.html", {
        "matches": matches,
        "preselected_match_id": preselected_match_id,
    })


# ── STEP 1: Start Innings ──

@admin_required
@require_plan('pro_plus')
def start_innings_view(request, match_id):
    match = get_object_or_404(CreateMatch, id=match_id)
    match_start = get_object_or_404(MatchStart, match=match)

    innings = Innings.objects.filter(match=match).order_by('-innings_number').first()
    if innings and innings.status == "IN_PROGRESS":
        return redirect('scoring', match_id=match_id)

    completed_innings = Innings.objects.filter(match=match, status="COMPLETED").count()
    next_innings_number = completed_innings + 1

    if next_innings_number > 2:
        messages.error(request, "Both innings are complete.")
        return redirect('match_result', match_id=match_id)

    if next_innings_number == 1:
        batting_team = match_start.batting_team
        bowling_team = match_start.bowling_team
    else:
        batting_team = match_start.bowling_team
        bowling_team = match_start.batting_team

    batting_tt = TournamentTeam.objects.filter(tournament=match.tournament, team=batting_team).first()
    bowling_tt = TournamentTeam.objects.filter(tournament=match.tournament, team=bowling_team).first()

    batsmen = (
        PlayerDetails.objects.filter(tournament_rosters__tournament_team=batting_tt)
        .distinct()
        .order_by('player_name')
    ) if batting_tt else PlayerDetails.objects.none()

    bowlers = (
        PlayerDetails.objects.filter(tournament_rosters__tournament_team=bowling_tt)
        .distinct()
        .order_by('player_name')
    ) if bowling_tt else PlayerDetails.objects.none()

    if request.method == "POST":
        striker_id = request.POST.get("striker")
        non_striker_id = request.POST.get("non_striker")
        bowler_id = request.POST.get("bowler")

        ctx = {
            'match': match, 'match_start': match_start,
            'batting_team': batting_team, 'bowling_team': bowling_team,
            'batsmen': batsmen, 'bowlers': bowlers,
            'innings_number': next_innings_number,
        }

        if not all([striker_id, non_striker_id, bowler_id]):
            messages.error(request, "Please select striker, non-striker, and bowler.")
            return render(request, 'start_innings.html', ctx)

        if striker_id == non_striker_id:
            messages.error(request, "Striker and non-striker cannot be the same player.")
            return render(request, 'start_innings.html', ctx)

        new_innings = begin_innings(match_start, innings_number=next_innings_number)
        new_innings.status = "IN_PROGRESS"
        if next_innings_number == 2:
            innings1 = Innings.objects.filter(match=match, innings_number=1).first()
            if innings1:
                new_innings.target = innings1.total_runs + 1
        new_innings.save()

        bowler = get_object_or_404(PlayerDetails, id=bowler_id)
        over = start_over(new_innings, over_number=1, bowler=bowler)

        request.session['striker_id'] = int(striker_id)
        request.session['non_striker_id'] = int(non_striker_id)
        request.session['innings_id'] = new_innings.id
        request.session['over_id'] = over.id

        # ── WebSocket: notify home page this match just went live ──
        if next_innings_number == 1:
            push_match_started(match)
        else:
            # 2nd innings starting — push full score so viewers get inn2 DOM created
            from strikezone.ws_push import _full_push
            _full_push(match)

        return redirect('scoring', match_id=match_id)

    return render(request, 'start_innings.html', {
        'match': match, 'match_start': match_start,
        'batting_team': batting_team, 'bowling_team': bowling_team,
        'batsmen': batsmen, 'bowlers': bowlers,
        'innings_number': next_innings_number,
    })


# ── STEP 2: Live Scoring Page ──

@admin_required
@require_plan('pro_plus')
def scoring_view(request, match_id):
    match = get_object_or_404(CreateMatch, id=match_id)

    innings_id = request.session.get('innings_id')
    innings = None
    if innings_id:
        innings = Innings.objects.filter(id=innings_id, match=match).first()
    if not innings:
        innings = Innings.objects.filter(match=match, status="IN_PROGRESS").first()
    if not innings:
        return redirect('start_innings', match_id=match_id)

    over = innings.overs.filter(is_completed=False).first()

    striker_id = request.session.get('striker_id')
    non_striker_id = request.session.get('non_striker_id')
    striker = PlayerDetails.objects.filter(id=striker_id).first()
    non_striker = PlayerDetails.objects.filter(id=non_striker_id).first()

    current_over_balls = over.balls.all() if over else []
    legal_ball_count = over.balls.filter(is_legal_ball=True).count() if over else 0

    batting_scorecard = BattingScorecard.objects.filter(innings=innings).order_by('batting_position')
    bowling_scorecard = BowlingScorecard.objects.filter(innings=innings)
    bowling_tt = TournamentTeam.objects.filter(tournament=match.tournament, team=innings.bowling_team).first()
    bowling_team_players = (
        PlayerDetails.objects.filter(tournament_rosters__tournament_team=bowling_tt)
        .distinct()
        .order_by('player_name')
    ) if bowling_tt else PlayerDetails.objects.none()

    dismissed_ids = list(
        BattingScorecard.objects.filter(innings=innings)
        .exclude(status='NOT_OUT')
        .values_list('batsman_id', flat=True)
    )
    currently_in = [int(striker_id), int(non_striker_id)] if striker_id and non_striker_id else []
    excluded_ids = list(set(dismissed_ids + currently_in))
    batting_tt = TournamentTeam.objects.filter(tournament=match.tournament, team=innings.batting_team).first()
    available_batsmen = (
        PlayerDetails.objects.filter(tournament_rosters__tournament_team=batting_tt)
        .exclude(id__in=excluded_ids)
        .distinct()
        .order_by('player_name')
    ) if batting_tt else PlayerDetails.objects.none()

    innings1 = Innings.objects.filter(match=match, innings_number=1).first()
    target = (innings1.total_runs + 1) if innings1 and innings.innings_number == 2 else None

    return render(request, 'scoring.html', {
        'match': match,
        'innings': innings,
        'over': over,
        'striker': striker,
        'non_striker': non_striker,
        'current_over_balls': current_over_balls,
        'legal_ball_count': legal_ball_count,
        'batting_scorecard': batting_scorecard,
        'bowling_scorecard': bowling_scorecard,
        'bowling_team_players': bowling_team_players,
        'available_batsmen': available_batsmen,
        'max_overs': innings.max_overs,  # uses custom_overs if set, else tournament default
        'target': target,
    })



# ── Match Settings: Update Overs ──────────────────────────────────────────────
@admin_required
@require_plan('pro_plus')
@require_POST
def update_match_overs(request, match_id):
    """Change the overs for this match — only allowed before the 1st ball is bowled."""
    match = get_object_or_404(CreateMatch, id=match_id)
    try:
        match_start = match.match_start
    except MatchStart.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Match not started yet.'}, status=400)

    # Block if any ball has been bowled in any innings
    any_ball = Ball.objects.filter(over__innings__match=match).exists()
    if any_ball:
        return JsonResponse({
            'success': False,
            'error': 'Cannot change overs after the first ball has been bowled.'
        }, status=400)

    try:
        data = json.loads(request.body)
    except Exception:
        data = request.POST

    try:
        new_overs = int(data.get('overs', 0))
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid overs value.'}, status=400)

    if new_overs < 1 or new_overs > 50:
        return JsonResponse({'success': False, 'error': 'Overs must be between 1 and 50.'}, status=400)

    match_start.custom_overs = new_overs
    match_start.save(update_fields=['custom_overs'])

    return JsonResponse({
        'success': True,
        'overs': new_overs,
        'message': f'Match overs updated to {new_overs}.'
    })


# ── STEP 3: AJAX - Record Ball ──

@admin_required
@require_plan('pro_plus')
@require_POST
def record_ball_view(request, match_id):
    match = get_object_or_404(CreateMatch, id=match_id)

    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'Invalid JSON data'}, status=400)

    over_id = request.session.get('over_id')
    striker_id = request.session.get('striker_id')
    non_striker_id = request.session.get('non_striker_id')

    if not over_id or not striker_id or not non_striker_id:
        return JsonResponse({'error': 'Session expired. Please refresh the page.'}, status=400)

    over = get_object_or_404(Over, id=over_id)
    innings = over.innings

    if innings.status == "COMPLETED":
        return JsonResponse({'error': 'Innings already completed.'}, status=400)

    runs = int(data.get('runs', 0))
    ball_type = data.get('ball_type', 'NORMAL')
    scoring_batsman_id = data.get('scoring_batsman_id')

    if not scoring_batsman_id:
        return JsonResponse({'error': 'No batsman selected.'}, status=400)

    batsman = get_object_or_404(PlayerDetails, id=scoring_batsman_id)

    extra_runs = 0
    runs_off_bat = runs
    if ball_type == 'WIDE':
        extra_runs = 1 + runs
        runs_off_bat = 0
    elif ball_type == 'NO_BALL':
        extra_runs = 1
        runs_off_bat = runs

    is_wicket = bool(data.get('is_wicket', False))
    wicket_type = data.get('wicket_type', 'NONE') if is_wicket else 'NONE'
    player_dismissed = batsman if is_wicket else None

    # For run-out: dismissed player could be the non-striker
    dismissed_id = data.get('dismissed_batsman_id')
    if is_wicket and dismissed_id:
        try:
            player_dismissed = PlayerDetails.objects.get(id=int(dismissed_id))
        except PlayerDetails.DoesNotExist:
            player_dismissed = batsman

    # Fielder (catcher / run-out thrower / stumper)
    fielder = None
    fielder_id = data.get('fielder_id')
    if fielder_id:
        fielder = PlayerDetails.objects.filter(id=int(fielder_id)).first()

    # Shot direction (wagon wheel — stored for ML, not shown publicly)
    shot_direction = data.get('shot_direction') or None

    ball = record_ball(
        over=over,
        batsman=batsman,
        runs_off_bat=runs_off_bat,
        extra_runs=extra_runs,
        ball_type=ball_type,
        is_wicket=is_wicket,
        wicket_type=wicket_type,
        player_dismissed=player_dismissed,
        fielder=fielder,
        shot_direction=shot_direction,
    )

    # ── STRIKE ROTATION ──
    # Step 1: On a legal delivery, if odd runs were scored off the bat,
    # batsmen crossed — swap striker and non-striker.
    # (For wides: no bat contact, no crossing regardless of extra runs)
    if ball.is_legal_ball and ball_type != 'WIDE' and (runs_off_bat % 2 == 1):
        striker_id, non_striker_id = int(non_striker_id), int(striker_id)
        request.session['striker_id']    = striker_id
        request.session['non_striker_id'] = non_striker_id

    # Step 2: Handle wicket — place the dismissed player's end correctly
    # so select_new_batsman knows which session slot to fill.
    #
    # After any run-crossing above, striker_id/non_striker_id hold the
    # CURRENT positions (who is physically at each end RIGHT NOW).
    #
    # The dismissed player is at one of those two ends.
    # The new batsman always walks in and FACES the next ball
    # (i.e. goes to the striker end) UNLESS the over also ends,
    # in which case the surviving batsman faces.
    #
    # Convention used here:
    #   session['striker_id'] = whoever needs replacing (the dismissed player's end)
    #   session['non_striker_id'] = the surviving batsman
    #
    # This ensures select_new_batsman simply does:
    #   session['striker_id'] = new_batsman  (always)
    if is_wicket:
        dismissed_player_id = player_dismissed.id if player_dismissed else int(batsman.id)

        cur_s  = int(request.session.get('striker_id'))
        cur_ns = int(request.session.get('non_striker_id'))

        if dismissed_player_id == cur_s:
            # Dismissed player is already in striker slot — new batsman goes to striker end ✓
            pass
        elif dismissed_player_id == cur_ns:
            # Dismissed player is in non-striker slot (e.g. run-out of non-striker,
            # or striker got out after odd-run crossing moved them to non-striker end)
            # Swap so dismissed player is in striker slot, survivor in non-striker slot
            request.session['striker_id']    = cur_ns
            request.session['non_striker_id'] = cur_s
        else:
            # Fallback: put dismissed player as striker
            request.session['striker_id'] = dismissed_player_id

    over.refresh_from_db()
    innings.refresh_from_db()

    # ── TARGET CHASE CHECK (2nd innings) ──
    if innings.innings_number == 2 and innings.status != "COMPLETED":
        innings1 = Innings.objects.filter(match=match, innings_number=1).first()
        if innings1 and innings.total_runs > innings1.total_runs:
            innings.status = "COMPLETED"
            innings.save()
            if not over.is_completed:
                over.is_completed = True
                over.save()
            innings.refresh_from_db()
            over.refresh_from_db()

    # ── AUTO-CREATE MATCH RESULT when 2nd innings completes ──
    innings.refresh_from_db()
    innings_complete = innings.status == "COMPLETED"

    if innings_complete and innings.innings_number == 2:
        if not MatchResult.objects.filter(match=match).exists():
            innings1 = Innings.objects.filter(match=match, innings_number=1).first()
            innings2 = innings

            if innings2.total_runs > innings1.total_runs:
                winner_team    = innings2.batting_team
                result_type    = "WIN_BY_WICKETS"
                win_margin     = 10 - innings2.total_wickets
                result_summary = f"{winner_team.team_name} won by {win_margin} wickets"
            elif innings1.total_runs > innings2.total_runs:
                winner_team    = innings1.batting_team
                result_type    = "WIN_BY_RUNS"
                win_margin     = innings1.total_runs - innings2.total_runs
                result_summary = f"{winner_team.team_name} won by {win_margin} runs"
            else:
                winner_team    = None
                result_type    = "TIE"
                win_margin     = None
                result_summary = "Match Tied"

            MatchResult.objects.create(
                match=match,
                winner=winner_team,
                result_type=result_type,
                win_margin=win_margin,
                result_summary=result_summary,
            )
            # Auto-advance knockout winner if this is a knockout match
            auto_advance_knockout(match.id)
            # Award Man of the Match
            award_man_of_the_match(match.id)
            # Award Tournament Honours if tournament is now complete
            award_tournament_awards(match.tournament_id)

    legal_balls = over.balls.filter(is_legal_ball=True).count()
    over_complete = over.is_completed
    innings_complete = innings.status == "COMPLETED"

    # End-of-over: batsmen swap ends (non-striker faces next over)
    # But NOT if a wicket just fell — the new batsman selection handles positioning
    if over_complete and not innings_complete and not is_wicket:
        s  = request.session.get('striker_id')
        ns = request.session.get('non_striker_id')
        request.session['striker_id']     = ns
        request.session['non_striker_id'] = s

    needs_new_batsman = is_wicket and not innings_complete

    # ── Batsman stats for instant UI update ──
    # When wicket fell and new batsman is needed:
    #   session['striker_id']    = dismissed player's end (placeholder — new batsman fills this)
    #   session['non_striker_id'] = the surviving batsman
    # Don't return dismissed player's name as 'striker_name' — JS will leave striker card
    # alone and wait for select_new_batsman to provide the real new batsman's details.
    surviving_id = request.session.get('non_striker_id')
    current_non_striker = PlayerDetails.objects.filter(id=surviving_id).first()

    if needs_new_batsman:
        # Only return the surviving batsman — JS skips striker-card update on wicket
        current_striker = None
        display_striker_name = ''
        display_striker_id   = None
    else:
        current_striker = PlayerDetails.objects.filter(id=request.session.get('striker_id')).first()
        display_striker_name = current_striker.player_name if current_striker else ''
        display_striker_id   = request.session.get('striker_id')

    def get_bat_stats(player):
        if not player:
            return {'runs': 0, 'balls': 0}
        sc = BattingScorecard.objects.filter(innings=innings, batsman=player).first()
        return {'runs': sc.runs if sc else 0, 'balls': sc.balls_faced if sc else 0}

    striker_stats    = get_bat_stats(current_striker)
    nonstriker_stats = get_bat_stats(current_non_striker)

    # ── Bowler stats for instant UI update ──
    current_bowler = over.bowler if over else None
    def get_bowl_stats(player):
        if not player:
            return {'overs': '0', 'runs': 0, 'wickets': 0}
        sc = BowlingScorecard.objects.filter(innings=innings, bowler=player).first()
        return {
            'overs': str(sc.overs_bowled) if sc else '0',
            'runs': sc.runs_given if sc else 0,
            'wickets': sc.wickets if sc else 0,
        }
    bowler_stats = get_bowl_stats(current_bowler)
    bowler_name  = current_bowler.player_name if current_bowler else ''

    # ── WebSocket push — after all DB writes are done ──
    # When a wicket just fell, session['striker_id'] = dismissed player's slot (placeholder).
    # For the public scorecard, we should show the SURVIVING batsman as the active player,
    # not the dismissed one. Send the non_striker (survivor) as the live striker until
    # the new batsman is confirmed.
    if needs_new_batsman:
        ws_striker_id    = request.session.get('non_striker_id')   # surviving batsman
        ws_nonstriker_id = request.session.get('non_striker_id')   # same (only 1 active)
    else:
        ws_striker_id    = request.session.get('striker_id')
        ws_nonstriker_id = request.session.get('non_striker_id')

    push_ball(
        match, innings, ball,
        striker_id=ws_striker_id,
        non_striker_id=ws_nonstriker_id,
    )
    if innings_complete:
        push_innings_complete(match, innings)
    # Check if match is fully complete (2nd innings just ended)
    try:
        if innings_complete and innings.innings_number == 2:
            push_match_complete(match, match.result.result_summary)
    except Exception:
        pass

    return JsonResponse({
        'success': True,
        'total_runs': innings.total_runs,
        'total_wickets': innings.total_wickets,
        'overs': innings.overs_completed,
        'total_balls': innings.total_balls,
        'ball_runs': ball.total_runs,
        'ball_type': ball_type,
        'legal_ball_count': legal_balls,
        'over_complete': over_complete,
        'innings_complete': innings_complete,
        'innings_number': innings.innings_number,
        'striker_id': display_striker_id,
        'non_striker_id': request.session.get('non_striker_id'),
        'striker_name': display_striker_name,
        'non_striker_name': current_non_striker.player_name if current_non_striker else '',
        'dismissed_name': player_dismissed.player_name if (is_wicket and player_dismissed) else '',
        'needs_new_batsman': needs_new_batsman,
        'striker_runs': striker_stats['runs'],
        'striker_balls': striker_stats['balls'],
        'nonstriker_runs': nonstriker_stats['runs'],
        'nonstriker_balls': nonstriker_stats['balls'],
        'bowler_name': bowler_name,
        'bowler_overs': bowler_stats['overs'],
        'bowler_runs': bowler_stats['runs'],
        'bowler_wickets': bowler_stats['wickets'],
    })


# ── STEP 3b: AJAX - Select New Batsman after Wicket ──

@admin_required
@require_plan('pro_plus')
@require_POST
def select_new_batsman(request, match_id):
    match = get_object_or_404(CreateMatch, id=match_id)
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'Invalid JSON data'}, status=400)

    new_batsman_id = data.get('new_batsman_id')
    if not new_batsman_id:
        return JsonResponse({'error': 'No batsman selected.'}, status=400)

    new_batsman = get_object_or_404(PlayerDetails, id=new_batsman_id)

    # The session currently has:
    #   striker_id    = dismissed player's end  → replace with new batsman
    #   non_striker_id = surviving batsman
    # New batsman always comes in at the striker end (faces next ball)
    request.session['striker_id'] = int(new_batsman_id)

    innings_id = request.session.get('innings_id')
    over_id    = request.session.get('over_id')
    innings    = Innings.objects.filter(id=innings_id).first()
    over       = Over.objects.filter(id=over_id).first()

    over_complete    = bool(over and over.is_completed)
    innings_complete = bool(innings and innings.status == 'COMPLETED')

    # If the over ended on the wicket ball, the surviving batsman faces the next over.
    # That means: new batsman → non-striker end, surviving batsman → striker end.
    # We do the swap NOW server-side so session is authoritative.
    if over_complete and not innings_complete:
        # Swap: surviving non-striker becomes striker, new batsman becomes non-striker
        surviving_id = request.session.get('non_striker_id')
        request.session['striker_id']    = surviving_id
        request.session['non_striker_id'] = int(new_batsman_id)

    final_striker_id    = request.session.get('striker_id')
    final_nonstriker_id = request.session.get('non_striker_id')

    final_striker    = PlayerDetails.objects.filter(id=final_striker_id).first()
    final_nonstriker = PlayerDetails.objects.filter(id=final_nonstriker_id).first()

    def get_bat_stats(player):
        if not player or not innings:
            return {'runs': 0, 'balls': 0}
        sc = BattingScorecard.objects.filter(innings=innings, batsman=player).first()
        return {'runs': sc.runs if sc else 0, 'balls': sc.balls_faced if sc else 0}

    striker_stats    = get_bat_stats(final_striker)
    nonstriker_stats = get_bat_stats(final_nonstriker)

    # Build fresh available_batsmen list (server-authoritative)
    fresh_available = []
    if innings:
        dismissed_ids = list(
            BattingScorecard.objects.filter(innings=innings)
            .exclude(status='NOT_OUT')
            .values_list('batsman_id', flat=True)
        )
        match_obj  = innings.match
        batting_tt = TournamentTeam.objects.filter(
            tournament=match_obj.tournament, team=innings.batting_team
        ).first()
        currently_in = [final_striker_id, final_nonstriker_id]
        excluded = list(set(dismissed_ids + [x for x in currently_in if x]))
        if batting_tt:
            qs = (
                PlayerDetails.objects.filter(tournament_rosters__tournament_team=batting_tt)
                .exclude(id__in=excluded)
                .distinct()
                .order_by('player_name')
            )
            fresh_available = [{'id': p.id, 'name': p.player_name} for p in qs]

    # ── WebSocket: show new batsman card on live scorecard ──
    push_new_batsman(match, new_batsman)

    return JsonResponse({
        'success': True,
        # Full authoritative state — JS should sync entirely from this
        'striker_id':       final_striker_id,
        'striker_name':     final_striker.player_name if final_striker else '',
        'striker_runs':     striker_stats['runs'],
        'striker_balls':    striker_stats['balls'],
        'non_striker_id':   final_nonstriker_id,
        'non_striker_name': final_nonstriker.player_name if final_nonstriker else '',
        'nonstriker_runs':  nonstriker_stats['runs'],
        'nonstriker_balls': nonstriker_stats['balls'],
        # Keep legacy fields so existing JS still works
        'new_striker_id':   int(new_batsman_id),
        'new_striker_name': new_batsman.player_name,
        'over_complete':    over_complete,
        'innings_complete': innings_complete,
        'available_batsmen': fresh_available,
    })



# ── UNDO LAST BALL ──

@admin_required
@require_plan('pro_plus')
@require_POST
def undo_ball_view(request, match_id):
    match  = get_object_or_404(CreateMatch, id=match_id)
    innings_id = request.session.get('innings_id')
    if not innings_id:
        return JsonResponse({'error': 'Session expired.'}, status=400)

    innings = get_object_or_404(Innings, id=innings_id)
    if innings.status == 'COMPLETED':
        return JsonResponse({'error': 'Cannot undo — innings already completed.'}, status=400)

    # Capture current session state BEFORE undo
    cur_striker_id    = request.session.get('striker_id')
    cur_nonstriker_id = request.session.get('non_striker_id')

    result = undo_last_ball(innings)
    if result is None:
        return JsonResponse({'error': 'No balls to undo.'}, status=400)

    # ────────────────────────────────────────────────
    # RESTORE CORRECT STRIKER / NON-STRIKER IN SESSION
    # ────────────────────────────────────────────────
    # result['pre_ball_striker_id'] = whoever was ON STRIKE for the undone ball.
    # After undo, that person must be on strike again.
    #
    # To find the non-striker we use: everyone currently batting except the striker.
    # "Currently batting" = the two people who were at the crease during that ball.
    #
    # Strategy:
    #   pre_striker = result['pre_ball_striker_id']
    #   The non-striker at that ball time = whoever is NOT the pre_striker
    #   among the two current session players AFTER accounting for any swaps.

    pre_striker_id = result['pre_ball_striker_id']

    if result['was_wicket']:
        # Wicket of striker: dismissed player returns as striker
        # non-striker stays as non-striker (current session non-striker is correct
        # UNLESS the wicket ball had odd runs that caused a crossing first)
        dismissed_id = result['dismissed_player_id']
        if dismissed_id:
            new_striker_id = dismissed_id
        else:
            new_striker_id = pre_striker_id

        # The non-striker: if odd runs caused crossing before wicket fell,
        # the non-striker in session right now is actually the pre-ball striker's end
        if result['runs_caused_crossing']:
            # batsmen crossed before wicket — so current_striker (post-ball) was actually
            # the pre-ball NON-striker who ended up at striker end
            new_nonstriker_id = cur_striker_id
        else:
            new_nonstriker_id = cur_nonstriker_id

        # For run-out of non-striker: striker stays the same
        if result['runout_nonstriker']:
            new_striker_id    = cur_striker_id   # striker didn't change
            new_nonstriker_id = result['runout_nonstriker_id']  # dismissed ns returns

    else:
        # No wicket — purely about run-crossing and end-of-over swap
        # pre_ball_striker = ball.batsman (always correct)
        new_striker_id = pre_striker_id

        # The non-striker = whoever is NOT the pre_striker in the current pair
        # Current pair is (cur_striker_id, cur_nonstriker_id)
        if result['runs_caused_crossing']:
            # Odd runs → batsmen crossed → current_striker is pre_ball NON-striker
            # So non-striker after undo = current_striker
            new_nonstriker_id = cur_striker_id
        else:
            # No crossing — non-striker unchanged
            # current_nonstriker is still the non-striker
            new_nonstriker_id = cur_nonstriker_id

        # End-of-over swap: if the undone ball completed the over,
        # the over-end swap also happened → un-swap on top of the above
        if result['was_last_ball_of_over']:
            new_striker_id, new_nonstriker_id = new_nonstriker_id, new_striker_id

    request.session['striker_id']    = new_striker_id
    request.session['non_striker_id'] = new_nonstriker_id
    request.session.modified = True

    # Sync over_id — re-open over if needed
    innings.refresh_from_db()
    current_over = innings.overs.filter(is_completed=False).order_by('-over_number').first()
    if current_over:
        request.session['over_id'] = current_over.id

    # Build over-balls display list
    current_over_balls_data = []
    if current_over:
        for b in current_over.balls.order_by('ball_number'):
            if b.ball_type == 'WIDE':
                current_over_balls_data.append({'type': 'Wd', 'css': 'ball-wide'})
            elif b.ball_type == 'NO_BALL':
                current_over_balls_data.append({'type': 'Nb', 'css': 'ball-nb'})
            elif b.is_wicket:
                current_over_balls_data.append({'type': 'W',  'css': 'ball-w'})
            elif b.total_runs == 4:
                current_over_balls_data.append({'type': '4',  'css': 'ball-4'})
            elif b.total_runs == 6:
                current_over_balls_data.append({'type': '6',  'css': 'ball-6'})
            else:
                current_over_balls_data.append({'type': str(b.total_runs), 'css': 'ball-runs'})

    legal_count = current_over.balls.filter(is_legal_ball=True).count() if current_over else 0

    striker     = PlayerDetails.objects.filter(id=new_striker_id).first()
    non_striker = PlayerDetails.objects.filter(id=new_nonstriker_id).first()

    def get_bat_stats(player):
        if not player: return {'runs': 0, 'balls': 0}
        sc = BattingScorecard.objects.filter(innings=innings, batsman=player).first()
        return {'runs': sc.runs if sc else 0, 'balls': sc.balls_faced if sc else 0}

    s_stats  = get_bat_stats(striker)
    ns_stats = get_bat_stats(non_striker)

    # ── WebSocket push after undo ──
    push_undo(match, innings)

    return JsonResponse({
        'success': True,
        'total_runs':    result['total_runs'],
        'total_wickets': result['total_wickets'],
        'overs':         result['overs'],
        'total_balls':   result['total_balls'],
        'legal_ball_count': legal_count,
        'over_number':   result['over_number'],
        'over_balls':    current_over_balls_data,
        'striker_id':    new_striker_id,
        'non_striker_id': new_nonstriker_id,
        'striker_name':     striker.player_name    if striker     else '',
        'non_striker_name': non_striker.player_name if non_striker else '',
        'striker_runs':    s_stats['runs'],
        'striker_balls':   s_stats['balls'],
        'nonstriker_runs':  ns_stats['runs'],
        'nonstriker_balls': ns_stats['balls'],
        'was_wicket': result['was_wicket'],
    })


# ── STEP 4: AJAX - Start Next Over ──

@admin_required
@require_plan('pro_plus')
@require_POST
def next_over_view(request, match_id):
    match = get_object_or_404(CreateMatch, id=match_id)

    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'Invalid JSON data'}, status=400)

    innings_id = request.session.get('innings_id')
    if not innings_id:
        return JsonResponse({'error': 'Session expired. Please refresh the page.'}, status=400)

    innings = get_object_or_404(Innings, id=innings_id)

    if innings.status == "COMPLETED":
        return JsonResponse({'error': 'Innings already completed.'}, status=400)

    bowler_id = data.get('bowler_id')
    if not bowler_id:
        return JsonResponse({'error': 'No bowler selected.'}, status=400)

    bowler = get_object_or_404(PlayerDetails, id=bowler_id)

    # Prevent same bowler bowling back-to-back overs
    last_completed_over = innings.overs.filter(is_completed=True).order_by('-over_number').first()
    if last_completed_over and last_completed_over.bowler_id == int(bowler_id):
        return JsonResponse({'error': f'{bowler.player_name} just bowled the previous over. Choose a different bowler.'}, status=400)

    current_incomplete_over = innings.overs.filter(is_completed=False).first()
    if current_incomplete_over:
        balls_bowled = current_incomplete_over.balls.count()
        if balls_bowled > 0:
            return JsonResponse({
                'error': f'Cannot change bowler mid-over. {6 - current_incomplete_over.balls.filter(is_legal_ball=True).count()} legal ball(s) remaining in this over.'
            }, status=400)
        current_incomplete_over.bowler = bowler
        current_incomplete_over.save()
        request.session['over_id'] = current_incomplete_over.id
        # ── WebSocket: show new over card ──
        push_new_over(match, current_incomplete_over.over_number, bowler)
        return JsonResponse({
            'success': True,
            'over_number': current_incomplete_over.over_number,
            'bowler': bowler.player_name,
        })

    completed_overs = innings.overs.filter(is_completed=True).count()
    next_over_number = completed_overs + 1

    if next_over_number > innings.max_overs:
        return JsonResponse({'error': 'All overs completed.'}, status=400)

    over = start_over(innings, over_number=next_over_number, bowler=bowler)
    request.session['over_id'] = over.id

    # ── WebSocket: show new over card on live scorecard ──
    push_new_over(match, over.over_number, bowler)

    return JsonResponse({
        'success': True,
        'over_number': over.over_number,
        'bowler': bowler.player_name,
    })


# ── STEP 5: Start 2nd Innings ──

@admin_required
@require_plan('pro_plus')
def start_second_innings(request, match_id):
    for key in ('innings_id', 'over_id', 'striker_id', 'non_striker_id'):
        request.session.pop(key, None)
    return redirect('start_innings', match_id=match_id)


# ── STEP 6: Match Result ──