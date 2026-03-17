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
from .views_awards import _is_tournament_complete

def get_tournament_leaderboard(tournament):
    max_overs = tournament.number_of_overs
    teams = TeamDetails.objects.filter(tournament_entries__tournament=tournament).distinct()
    leaderboard = []

    for team in teams:
        wins = losses = 0
        total_runs_scored = total_overs_faced = 0.0
        total_runs_conceded = total_overs_bowled = 0.0

        matches = CreateMatch.objects.filter(
            tournament=tournament
        ).filter(
            django_models.Q(team1=team) | django_models.Q(team2=team)
        )

        for match in matches:
            inn1 = Innings.objects.filter(match=match, innings_number=1).first()
            inn2 = Innings.objects.filter(match=match, innings_number=2).first()

            if not inn1 or not inn2 or inn2.status != 'COMPLETED':
                continue

            if hasattr(match, 'knockout_match'):
                continue

            if inn1.batting_team == team:
                batting_inn, bowling_inn = inn1, inn2
            else:
                batting_inn, bowling_inn = inn2, inn1

            def balls_to_overs(balls):
                completed = balls // 6
                partial = balls % 6
                return completed + (partial / 6)

            batting_overs = balls_to_overs(batting_inn.total_balls) or max_overs
            bowling_overs = balls_to_overs(bowling_inn.total_balls) or max_overs

            total_runs_scored   += batting_inn.total_runs
            total_overs_faced   += batting_overs
            total_runs_conceded += bowling_inn.total_runs
            total_overs_bowled  += bowling_overs

            result = MatchResult.objects.filter(match=match).first()
            if result:
                if result.winner == team:
                    wins += 1
                elif result.winner is not None:
                    losses += 1

        if total_overs_faced > 0 and total_overs_bowled > 0:
            nrr = round(
                (total_runs_scored / total_overs_faced) -
                (total_runs_conceded / total_overs_bowled), 3
            )
        else:
            nrr = 0.000

        leaderboard.append({
            'team': team,
            'wins': wins,
            'losses': losses,
            'played': wins + losses,
            'points': wins * 2,
            'nrr': nrr,
        })

    leaderboard.sort(key=lambda x: (-x['points'], -x['nrr']))
    for i, entry in enumerate(leaderboard):
        entry['rank'] = i + 1

    return leaderboard


def all_league_matches_completed(tournament):
    matches = CreateMatch.objects.filter(tournament=tournament)
    league_matches = [m for m in matches if not hasattr(m, 'knockout_match')]
    if not league_matches:
        return False
    for match in league_matches:
        inn2 = Innings.objects.filter(match=match, innings_number=2).first()
        if not inn2 or inn2.status != 'COMPLETED':
            return False
    return True


STAGE_ORDER = {'PQF': 1, 'QF': 2, 'SF': 3, 'F': 4}
NEXT_STAGE  = {'PQF': 'QF', 'QF': 'SF', 'SF': 'F', 'F': None}


@admin_required
@require_plan('pro_plus')
def knockout_bracket(request, tournament_id):
    tournament = get_object_or_404(TournamentDetails, id=tournament_id)
    from subscriptions.decorators import _is_privileged, _player_owns_tournament
    if not _is_privileged(request) and not _player_owns_tournament(request, tournament.id):
        from django.contrib import messages
        messages.warning(request, 'You can only manage tournaments you have created.')
        return redirect('upgrade_plan')

    leaderboard = get_tournament_leaderboard(tournament)
    league_done = all_league_matches_completed(tournament)

    stages = KnockoutStage.objects.filter(
        tournament=tournament
    ).prefetch_related('matches__team1', 'matches__team2', 'matches__winner')

    bracket_exists = stages.exists()

    all_matches = CreateMatch.objects.filter(tournament=tournament)
    league_matches = [m for m in all_matches if not hasattr(m, 'knockout_match')]
    pending_league = []
    for m in league_matches:
        inn2 = Innings.objects.filter(match=m, innings_number=2).first()
        if not inn2 or inn2.status != 'COMPLETED':
            pending_league.append(m)

    return render(request, 'knockout_bracket.html', {
        'tournament': tournament,
        'leaderboard': leaderboard,
        'league_done': league_done,
        'stages': stages,
        'bracket_exists': bracket_exists,
        'pending_league_count': len(pending_league),
    })


@admin_required
@require_plan('pro_plus')
def setup_knockout_stage(request, tournament_id):
    tournament = get_object_or_404(TournamentDetails, id=tournament_id)
    from subscriptions.decorators import _is_privileged, _player_owns_tournament
    if not _is_privileged(request) and not _player_owns_tournament(request, tournament.id):
        from django.contrib import messages
        messages.warning(request, 'You can only manage tournaments you have created.')
        return redirect('upgrade_plan')

    leaderboard = get_tournament_leaderboard(tournament)

    existing_stages = KnockoutStage.objects.filter(tournament=tournament).order_by('stage_order')

    if not existing_stages.exists():
        next_stage_code = None
    else:
        last_stage = existing_stages.last()
        next_stage_code = NEXT_STAGE.get(last_stage.stage)

    # ALL tournament teams — always available for selection in any stage
    all_tournament_teams = TeamDetails.objects.filter(
        tournament_entries__tournament=tournament
    ).distinct().order_by('team_name')

    # Suggested teams = winners from last stage (used as default pre-selection hints)
    if not existing_stages.exists():
        suggested_teams = [entry['team'] for entry in leaderboard]
        suggested_labels = [f"TOP {entry['rank']} - {entry['team'].team_name}" for entry in leaderboard]
    else:
        last_stage = existing_stages.last()
        last_stage_matches = KnockoutMatch.objects.filter(stage=last_stage).order_by('match_number')
        suggested_teams = []
        suggested_labels = []
        for km in last_stage_matches:
            if km.winner:
                suggested_teams.append(km.winner)
                suggested_labels.append(
                    f"{last_stage.get_stage_display()} M{km.match_number} Winner - {km.winner.team_name}"
                )
            else:
                suggested_teams.append(None)
                suggested_labels.append(f"{last_stage.get_stage_display()} M{km.match_number} Winner - TBD")

    # Keep available_teams as zipped list for template (suggested pre-fills)
    available_teams = list(zip(suggested_teams, suggested_labels))

    if request.method == 'POST':
        stage_code = request.POST.get('stage_code')
        num_matches = int(request.POST.get('num_matches', 1))

        if KnockoutStage.objects.filter(tournament=tournament, stage=stage_code).exists():
            messages.error(request, f"{stage_code} stage already exists for this tournament.")
            return redirect('knockout_bracket', tournament_id=tournament_id)

        stage = KnockoutStage.objects.create(
            tournament=tournament,
            stage=stage_code,
            stage_order=STAGE_ORDER[stage_code],
        )

        for i in range(1, num_matches + 1):
            team1_id = request.POST.get(f'match_{i}_team1')
            team2_id = request.POST.get(f'match_{i}_team2')
            team1_label = request.POST.get(f'match_{i}_team1_label', '')
            team2_label = request.POST.get(f'match_{i}_team2_label', '')
            venue = request.POST.get(f'match_{i}_venue', '')
            match_date_str = request.POST.get(f'match_{i}_date', '')

            team1 = TeamDetails.objects.filter(id=team1_id).first() if team1_id else None
            team2 = TeamDetails.objects.filter(id=team2_id).first() if team2_id else None

            match_date_val = None
            if match_date_str:
                try:
                    match_date_val = datetime.strptime(match_date_str, '%Y-%m-%d').date()
                except ValueError:
                    pass

            KnockoutMatch.objects.create(
                stage=stage,
                match_number=i,
                team1=team1,
                team2=team2,
                team1_label=team1_label,
                team2_label=team2_label,
                venue=venue,
                match_date=match_date_val,
            )

        messages.success(request, f"{stage.get_stage_display()} matches created successfully!")
        return redirect('knockout_bracket', tournament_id=tournament_id)

    stage_choices = [
        ('PQF', 'Pre Quarter Final'),
        ('QF',  'Quarter Final'),
        ('SF',  'Semi Final'),
        ('F',   'Final'),
    ]
    existing_stage_codes = list(existing_stages.values_list('stage', flat=True))
    stage_choices = [(code, label) for code, label in stage_choices if code not in existing_stage_codes]

    return render(request, 'setup_knockout_stage.html', {
        'tournament': tournament,
        'leaderboard': leaderboard,
        'available_teams': available_teams,  # zipped (team, label) suggested pre-fills
        'all_tournament_teams': all_tournament_teams,
        'next_stage_code': next_stage_code,
        'stage_choices': stage_choices,
        'existing_stages': existing_stages,
    })


@admin_required
@require_plan('pro_plus')
def start_knockout_match(request, knockout_match_id):
    km = get_object_or_404(KnockoutMatch, id=knockout_match_id)
    tournament = km.stage.tournament
    from subscriptions.decorators import _is_privileged, _player_owns_tournament
    if not _is_privileged(request) and not _player_owns_tournament(request, tournament.id):
        from django.contrib import messages
        messages.warning(request, 'You can only manage tournaments you have created.')
        return redirect('upgrade_plan')


    if km.is_completed:
        messages.error(request, "This knockout match is already completed.")
        return redirect('knockout_bracket', tournament_id=tournament.id)

    if not km.team1 or not km.team2:
        messages.error(request, "Both teams must be confirmed before starting this match.")
        return redirect('knockout_bracket', tournament_id=tournament.id)

    if not km.match:
        real_match = CreateMatch.objects.create(
            tournament=tournament,
            team1=km.team1,
            team2=km.team2,
            match_date=km.match_date or date.today(),
            venue=km.venue or 'TBD',
        )
        km.match = real_match
        km.save()

    # If match already has a toss/start, go directly to innings
    try:
        _ = km.match.match_start
        return redirect('start_innings', match_id=km.match.id)
    except MatchStart.DoesNotExist:
        return redirect(f"{reverse('match_start')}?match_id={km.match.id}")


def auto_advance_knockout(match_id):
    try:
        km = KnockoutMatch.objects.get(match_id=match_id)
    except KnockoutMatch.DoesNotExist:
        return

    result = MatchResult.objects.filter(match_id=match_id).first()
    if not result or not result.winner:
        return

    km.winner = result.winner
    km.is_completed = True
    km.save()

    stage = km.stage
    all_stage_matches = stage.matches.all()
    if all(m.is_completed for m in all_stage_matches):
        stage.is_completed = True
        stage.save()

    if km.next_match:
        next_km = km.next_match
        if not next_km.team1:
            next_km.team1 = result.winner
            next_km.team1_label = f"{stage.get_stage_display()} M{km.match_number} Winner"
        elif not next_km.team2:
            next_km.team2 = result.winner
            next_km.team2_label = f"{stage.get_stage_display()} M{km.match_number} Winner"
        next_km.save()


@admin_required
@require_plan('pro_plus')
def link_knockout_matches(request, tournament_id):
    tournament = get_object_or_404(TournamentDetails, id=tournament_id)
    from subscriptions.decorators import _is_privileged, _player_owns_tournament
    if not _is_privileged(request) and not _player_owns_tournament(request, tournament.id):
        from django.contrib import messages
        messages.warning(request, 'You can only manage tournaments you have created.')
        return redirect('upgrade_plan')


    if request.method == 'POST':
        for key, value in request.POST.items():
            if key.startswith('next_match_'):
                km_id = int(key.replace('next_match_', ''))
                next_km_id = int(value) if value else None
                km = KnockoutMatch.objects.filter(id=km_id).first()
                if km:
                    km.next_match_id = next_km_id
                    km.save()

        messages.success(request, "Match links updated successfully!")
        return redirect('knockout_bracket', tournament_id=tournament_id)

    stages = KnockoutStage.objects.filter(
        tournament=tournament
    ).prefetch_related('matches')

    return render(request, 'link_knockout_matches.html', {
        'tournament': tournament,
        'stages': stages,
    })


def public_knockout_bracket(request, tournament_id):
    tournament = get_object_or_404(TournamentDetails, id=tournament_id)
    stages = KnockoutStage.objects.filter(
        tournament=tournament
    ).prefetch_related(
        'matches__team1',
        'matches__team2',
        'matches__winner',
        'matches__match'
    ).order_by('stage_order')

    return render(request, 'public_knockout_bracket.html', {
        'tournament': tournament,
        'stages': stages,
    })



# ══════════════════════════════════════════════
# TOURNAMENT AWARDS ENGINE
# Best Batsman · Best Bowler · Man of the Tournament
# ══════════════════════════════════════════════