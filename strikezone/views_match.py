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
from .views_awards import _is_tournament_complete, award_tournament_awards, award_man_of_the_match

def match_result(request, match_id):
    match = get_object_or_404(CreateMatch, id=match_id)

    inn1 = Innings.objects.filter(match=match, innings_number=1).first()
    inn2 = Innings.objects.filter(match=match, innings_number=2).first()

    if not inn1 or not inn2 or inn2.status != "COMPLETED":
        try:
            _ = match.match_start
            return redirect('scoring', match_id=match_id)
        except MatchStart.DoesNotExist:
            messages.error(request, "Match has not been started yet.")
            return redirect('home')

    if inn2.total_runs > inn1.total_runs:
        winner = inn2.batting_team
        margin = f"{10 - inn2.total_wickets} wickets"
    elif inn1.total_runs > inn2.total_runs:
        winner = inn1.batting_team
        margin = f"{inn1.total_runs - inn2.total_runs} runs"
    else:
        winner = None
        margin = None

    return render(request, 'match_result.html', {
        'match': match,
        'inn1': inn1,
        'inn2': inn2,
        'winner': winner,
        'margin': margin,
    })


# ── RESTART MATCH ──

@admin_required
@require_plan('pro_plus')
@require_POST
def restart_match(request, match_id):
    match = get_object_or_404(CreateMatch, id=match_id)

    innings_list = Innings.objects.filter(match=match)
    for innings in innings_list:
        for over in innings.overs.all():
            over.balls.all().delete()
        innings.overs.all().delete()
        BattingScorecard.objects.filter(innings=innings).delete()
        BowlingScorecard.objects.filter(innings=innings).delete()
    innings_list.delete()

    MatchStart.objects.filter(match=match).delete()

    for key in ('innings_id', 'over_id', 'striker_id', 'non_striker_id'):
        request.session.pop(key, None)

    messages.success(request, "Match has been reset. You can now restart.")
    return redirect('match_start')


# ── TOURNAMENT HISTORY ──


# ── TOURNAMENT AWARDS PAGE ──

def tournament_awards(request, tournament_id):
    tournament = get_object_or_404(TournamentDetails, id=tournament_id)

    # Try to compute awards if not yet done (covers already-completed tournaments)
    award_tournament_awards(tournament_id)

    awards = TournamentAward.objects.filter(tournament=tournament).select_related('player')
    awards_dict = {a.award_type: a for a in awards}

    mot  = awards_dict.get('MOT')
    bbat = awards_dict.get('BBAT')
    bbol = awards_dict.get('BBOL')

    # Tournament champion (Final winner)
    from knockout.models import KnockoutStage, KnockoutMatch
    champion = None
    final_stage = KnockoutStage.objects.filter(tournament=tournament, stage='F').first()
    if final_stage:
        final_match = KnockoutMatch.objects.filter(stage=final_stage, is_completed=True).first()
        if final_match:
            champion = final_match.winner

    # Runner up
    runner_up = None
    if final_stage and champion:
        fm = KnockoutMatch.objects.filter(stage=final_stage, is_completed=True).first()
        if fm and fm.match:
            runner_up = fm.match.team2 if fm.match.team1 == champion else fm.match.team1

    # Top 5 run scorers
    from django.db.models import Sum
    top_batsmen = []
    all_matches = CreateMatch.objects.filter(tournament=tournament)
    completed_innings = Innings.objects.filter(
        match__in=all_matches, innings_number__in=[1, 2]
    ).filter(status='COMPLETED')

    bat_agg = (
        BattingScorecard.objects
        .filter(innings__in=completed_innings)
        .values('batsman__id', 'batsman__player_name')
        .annotate(total_runs=Sum('runs'), total_balls=Sum('balls_faced'))
        .order_by('-total_runs')[:5]
    )
    top_batsmen = list(bat_agg)

    # Top 5 wicket takers
    bowl_agg = (
        BowlingScorecard.objects
        .filter(innings__in=completed_innings)
        .values('bowler__id', 'bowler__player_name')
        .annotate(total_wickets=Sum('wickets'), total_runs=Sum('runs_given'))
        .order_by('-total_wickets', 'total_runs')[:5]
    )
    top_bowlers = list(bowl_agg)

    # All completed matches for the tournament
    final_matches = []
    for m in all_matches.order_by('match_date'):
        inn2 = Innings.objects.filter(match=m, innings_number=2).first()
        if inn2 and inn2.status == 'COMPLETED':
            try:
                mr = m.result
                final_matches.append({'match': m, 'result': mr.result_summary})
            except Exception:
                pass

    is_complete = _is_tournament_complete(tournament)

    # Tournament intensity analysis
    try:
        from .views_awards import get_tournament_intensity
        tournament_intensity = get_tournament_intensity(tournament)
    except Exception:
        tournament_intensity = None

    return render(request, 'tournament_awards.html', {
        'tournament': tournament,
        'mot': mot,
        'bbat': bbat,
        'bbol': bbol,
        'champion': champion,
        'runner_up': runner_up,
        'top_batsmen': top_batsmen,
        'top_bowlers': top_bowlers,
        'final_matches': final_matches,
        'is_complete': is_complete,
        'awards_count': awards.count(),
        'tournament_intensity': tournament_intensity,
    })


def tournament_history(request, tournament_id):
    tournament = get_object_or_404(TournamentDetails, id=tournament_id)
    matches = CreateMatch.objects.filter(tournament=tournament).order_by('match_date')

    match_data = []
    for match in matches:
        try:
            ms = match.match_start
            match_started = True
        except MatchStart.DoesNotExist:
            match_started = False
            match_data.append({'match': match, 'status': 'PENDING', 'result': None})
            continue

        inn1 = Innings.objects.filter(match=match, innings_number=1).first()
        inn2 = Innings.objects.filter(match=match, innings_number=2).first()

        if inn2 and inn2.status == 'COMPLETED':
            # Try MatchResult first
            try:
                mr = match.result
                winner = mr.winner
                margin = mr.result_summary.split(' won by ')[-1] if ' won by ' in mr.result_summary else mr.result_summary
            except Exception:
                if inn2.total_runs > inn1.total_runs:
                    winner = inn2.batting_team
                    margin = f"{10 - inn2.total_wickets} wickets"
                elif inn1.total_runs > inn2.total_runs:
                    winner = inn1.batting_team
                    margin = f"{inn1.total_runs - inn2.total_runs} runs"
                else:
                    winner = None
                    margin = "Tied"

            match_data.append({
                'match': match,
                'status': 'COMPLETED',
                'inn1': inn1,
                'inn2': inn2,
                'winner': winner,
                'margin': margin,
            })
        elif inn1 and inn1.status == 'IN_PROGRESS':
            match_data.append({'match': match, 'status': 'LIVE', 'inn1': inn1, 'result': None})
        elif inn2 and inn2.status == 'IN_PROGRESS':
            match_data.append({'match': match, 'status': 'LIVE', 'inn1': inn1, 'inn2': inn2, 'result': None})
        elif inn1 and inn1.status == 'COMPLETED':
            # Between innings
            match_data.append({'match': match, 'status': 'IN_PROGRESS', 'inn1': inn1, 'result': None})
        else:
            # Toss done, no innings started yet
            match_data.append({'match': match, 'status': 'IN_PROGRESS', 'result': None})

    # ── LEADERBOARD WITH NRR ──
    max_overs = tournament.number_of_overs
    teams = TeamDetails.objects.filter(tournament_entries__tournament=tournament).distinct()
    leaderboard = []

    for team in teams:
        wins = 0
        losses = 0
        total_runs_scored = 0
        total_overs_faced = 0.0
        total_runs_conceded = 0
        total_overs_bowled = 0.0

        for md in match_data:
            if md['status'] != 'COMPLETED':
                continue

            match_teams = [md['match'].team1, md['match'].team2]
            if team not in match_teams:
                continue

            inn1 = md.get('inn1')
            inn2 = md.get('inn2')
            if not inn1 or not inn2:
                continue

            if inn1.batting_team == team:
                batting_inn = inn1
                bowling_inn = inn2
            else:
                batting_inn = inn2
                bowling_inn = inn1

            def balls_to_overs(balls, max_o):
                completed = balls // 6
                partial = balls % 6
                return completed + (partial / 6)

            batting_overs = balls_to_overs(batting_inn.total_balls, max_overs)
            bowling_overs = balls_to_overs(bowling_inn.total_balls, max_overs)

            if batting_inn.status == 'COMPLETED' and batting_inn.total_wickets < 10:
                batting_overs = max_overs if batting_inn.total_balls >= max_overs * 6 else balls_to_overs(batting_inn.total_balls, max_overs)

            if bowling_inn.status == 'COMPLETED' and bowling_inn.total_wickets < 10:
                bowling_overs = max_overs if bowling_inn.total_balls >= max_overs * 6 else balls_to_overs(bowling_inn.total_balls, max_overs)

            if batting_overs == 0:
                batting_overs = max_overs
            if bowling_overs == 0:
                bowling_overs = max_overs

            total_runs_scored   += batting_inn.total_runs
            total_overs_faced   += batting_overs
            total_runs_conceded += bowling_inn.total_runs
            total_overs_bowled  += bowling_overs

            if md.get('winner') == team:
                wins += 1
            elif md.get('winner') is not None:
                losses += 1

        if total_overs_faced > 0 and total_overs_bowled > 0:
            nrr = (total_runs_scored / total_overs_faced) - (total_runs_conceded / total_overs_bowled)
            nrr = round(nrr, 3)
        else:
            nrr = 0.000

        leaderboard.append({
            'team': team,
            'wins': wins,
            'losses': losses,
            'played': wins + losses,
            'points': wins * 2,
            'nrr': nrr,
            'nrr_display': f"+{nrr:.3f}" if nrr >= 0 else f"{nrr:.3f}",
        })

    leaderboard.sort(key=lambda x: (-x['points'], -x['nrr']))
    for i, entry in enumerate(leaderboard):
        entry['rank'] = i + 1

    tournament_complete = _is_tournament_complete(tournament)
    awards_exist = TournamentAward.objects.filter(tournament=tournament).exists()

    return render(request, 'tournament_history.html', {
        'tournament': tournament,
        'match_data': match_data,
        'leaderboard': leaderboard,
        'tournament_complete': tournament_complete,
        'awards_exist': awards_exist,
    })


# ── MATCH SCORECARD ──

def match_scorecard(request, match_id):
    match = get_object_or_404(CreateMatch, id=match_id)

    inn1 = Innings.objects.filter(match=match, innings_number=1).first()
    inn2 = Innings.objects.filter(match=match, innings_number=2).first()

    if not inn1:
        messages.error(request, "Match has not started yet.")
        return redirect('tournament_history', tournament_id=match.tournament.id)

    def get_scorecard(innings):
        if not innings:
            return None
        batting = BattingScorecard.objects.filter(innings=innings).order_by('batting_position').select_related('batsman')
        bowling = BowlingScorecard.objects.filter(innings=innings).select_related('bowler')
        return {'innings': innings, 'batting': batting, 'bowling': bowling}

    sc1 = get_scorecard(inn1)
    sc2 = get_scorecard(inn2)

    winner = margin = None
    if inn2 and inn2.status == 'COMPLETED':
        try:
            mr = match.result
            winner = mr.winner
            margin = mr.result_summary.split(' won by ')[-1] if ' won by ' in mr.result_summary else mr.result_summary
        except Exception:
            if inn2.total_runs > inn1.total_runs:
                winner = inn2.batting_team
                margin = f"{10 - inn2.total_wickets} wickets"
            elif inn1.total_runs > inn2.total_runs:
                winner = inn1.batting_team
                margin = f"{inn1.total_runs - inn2.total_runs} runs"
            else:
                margin = "Tied"

    # Ensure MOM is always computed for completed matches — no reload needed
    if inn2 and inn2.status == 'COMPLETED':
        try:
            mom = match.man_of_the_match
        except Exception:
            # MOM not awarded yet — compute it now
            award_man_of_the_match(match.id)
            # Re-fetch from DB so we get the freshly created record
            match.refresh_from_db()
            try:
                mom = match.man_of_the_match
            except Exception:
                mom = None
    else:
        mom = None

    # Match intensity analysis
    match_intensity = None
    if inn2 and inn2.status == 'COMPLETED':
        try:
            from .views_awards import get_match_intensity
            match_intensity = get_match_intensity(match)
        except Exception:
            pass

    return render(request, 'match_scorecard.html', {
        'match': match,
        'sc1': sc1,
        'sc2': sc2,
        'winner': winner,
        'margin': margin,
        'mom': mom,
        'match_intensity': match_intensity,
    })


# ── ADMIN LOGIN / LOGOUT ──

# ── Delete Match ──────────────────────────────────────────────────────────────

@admin_required
@require_POST
def delete_match(request, match_id):
    """Delete a match and all related data. Only for pro_plus users, employees and CEO."""
    from subscriptions.decorators import _is_privileged
    from subscriptions.decorators import _get_effective_plan

    # Permission: must be privileged (employee/CEO) OR pro_plus plan
    if not (_is_privileged(request) or _get_effective_plan(request) == 'pro_plus'):
        return JsonResponse({'success': False, 'error': 'Permission denied.'}, status=403)

    match = get_object_or_404(CreateMatch, id=match_id)
    tournament_id = match.tournament_id
    match_name = f"{match.team1.team_name} vs {match.team2.team_name}"

    # CASCADE delete — Django handles Ball→Over→Innings, MatchStart, MatchResult,
    # ManOfTheMatch, BattingScorecard, BowlingScorecard via on_delete=CASCADE
    match.delete()

    return JsonResponse({
        'success': True,
        'message': f'Match "{match_name}" has been deleted.',
        'tournament_id': tournament_id,
    })