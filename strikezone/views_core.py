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

from .views_awards import _is_tournament_complete, award_tournament_awards
from strikezone.forms import MatchForm, TournamentForm, TeamForm, PlayerForm
from strikezone.services import begin_innings, start_over, record_ball, undo_last_ball

import json
import random
import os
from datetime import date, datetime, timedelta
from groq import Groq as GroqClient

def admin_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        # Django admin/staff user → always allowed
        if request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser):
            return view_func(request, *args, **kwargs)

        # Session-based player with Pro Plus → also allowed
        player_mobile = request.session.get('player_mobile')
        if player_mobile:
            guest = GuestUser.objects.filter(mobile_number=player_mobile).first()
            if guest and guest.plan == GuestUser.PLAN_PRO_PLUS:
                return view_func(request, *args, **kwargs)

        messages.error(request, "You must be an admin or have a Pro Plus plan to access this page.")
        next_url = request.get_full_path()
        login_url = f"{reverse('admin_login')}?next={quote(next_url, safe='')}"
        return redirect(login_url)
    return wrapper


def home(request):
    from django.db.models import Sum

    all_tournaments = TournamentDetails.objects.all().order_by('id')

    selected_id = request.GET.get('t')
    if selected_id:
        try:
            selected_tournament = TournamentDetails.objects.get(id=int(selected_id))
        except (TournamentDetails.DoesNotExist, ValueError):
            selected_tournament = all_tournaments.first()
    else:
        selected_tournament = all_tournaments.first()

    completed_matches = []
    pending_matches = []
    live_matches = []
    top_batsmen = []
    top_bowlers = []

    if selected_tournament:
        all_tournament_matches = CreateMatch.objects.filter(
            tournament=selected_tournament
        ).select_related('team1', 'team2').order_by('-match_date')

        for m in all_tournament_matches:
            inn1 = Innings.objects.filter(match=m, innings_number=1).first()
            inn2 = Innings.objects.filter(match=m, innings_number=2).first()
            winner = margin = None
            status = 'SCHEDULED'

            if inn2 and inn2.status == 'COMPLETED':
                status = 'COMPLETED'
                # Try to get result from MatchResult table first
                try:
                    mr = m.result
                    winner = mr.winner
                    margin = mr.result_summary.split(' won by ')[-1] if ' won by ' in mr.result_summary else mr.result_summary
                except Exception:
                    # Fallback: calculate from innings
                    if inn2.total_runs > inn1.total_runs:
                        winner = inn2.batting_team
                        margin = f"{10 - inn2.total_wickets} wickets"
                    elif inn1.total_runs > inn2.total_runs:
                        winner = inn1.batting_team
                        margin = f"{inn1.total_runs - inn2.total_runs} runs"
                    else:
                        winner = None
                        margin = "Tied"
            elif (inn1 and inn1.status == 'IN_PROGRESS') or (inn2 and inn2.status == 'IN_PROGRESS'):
                status = 'LIVE'
            elif inn1 and inn1.status == 'COMPLETED':
                # 1st innings done, 2nd not started yet
                status = 'IN_PROGRESS'
            elif inn1:
                status = 'LIVE'
            else:
                # No innings at all - check if toss done
                try:
                    _ = m.match_start
                    status = 'TOSS_DONE'
                except Exception:
                    status = 'SCHEDULED'

            # ── Detect if knockout match ──
            is_knockout = hasattr(m, 'knockout_match')
            knockout_label = None
            match_number = None

            if is_knockout:
                km = m.knockout_match
                knockout_label = f"{km.stage.get_stage_display()} · Match {km.match_number}"
            else:
                league_matches_qs = [
                    x for x in all_tournament_matches
                    if not hasattr(x, 'knockout_match')
                ]
                try:
                    match_number = league_matches_qs.index(m) + 1
                except ValueError:
                    match_number = None

            batting_sc1 = BattingScorecard.objects.filter(innings=inn1).order_by('batting_position').select_related('batsman') if inn1 else []
            bowling_sc1 = BowlingScorecard.objects.filter(innings=inn1).select_related('bowler') if inn1 else []
            batting_sc2 = BattingScorecard.objects.filter(innings=inn2).order_by('batting_position').select_related('batsman') if inn2 else []
            bowling_sc2 = BowlingScorecard.objects.filter(innings=inn2).select_related('bowler') if inn2 else []

            card = {
                'match': m,
                'inn1': inn1,
                'inn2': inn2,
                'winner': winner,
                'margin': margin,
                'status': status,
                'batting_sc1': batting_sc1,
                'bowling_sc1': bowling_sc1,
                'batting_sc2': batting_sc2,
                'bowling_sc2': bowling_sc2,
                'is_knockout': is_knockout,
                'knockout_label': knockout_label,
                'match_number': match_number,
                'mom': ManOfTheMatch.objects.filter(match=m).select_related('player').first(),
            }

            if status == 'COMPLETED':
                completed_matches.append(card)
            elif status == 'LIVE':
                live_matches.append(card)
            else:
                # TOSS_DONE, IN_PROGRESS (between innings), SCHEDULED all go to pending
                pending_matches.append(card)

        all_innings_ids = Innings.objects.filter(
            match__tournament=selected_tournament
        ).values_list('id', flat=True)

        # Map player -> team name for this tournament (rule: one team per tournament)
        roster_team_map = {
            r.player_id: r.tournament_team.team.team_name
            for r in TournamentRoster.objects.filter(tournament=selected_tournament)
            .select_related('tournament_team__team')
        }

        top_batsmen = list(
            BattingScorecard.objects
            .filter(innings_id__in=all_innings_ids)
            .values('batsman__id', 'batsman__player_name', 'batsman__photo')
            .annotate(
                total_runs=Sum('runs'),
                total_balls=Sum('balls_faced'),
                total_fours=Sum('fours'),
                total_sixes=Sum('sixes'),
            )
            .order_by('-total_runs')[:8]
        )
        for row in top_batsmen:
            row['batsman__team__team_name'] = roster_team_map.get(row.get('batsman__id'), '')

        top_bowlers = list(
            BowlingScorecard.objects
            .filter(innings_id__in=all_innings_ids)
            .values('bowler__id', 'bowler__player_name', 'bowler__photo')
            .annotate(
                total_wickets=Sum('wickets'),
                total_runs_given=Sum('runs_given'),
            )
            .order_by('-total_wickets', 'total_runs_given')[:8]
        )
        for row in top_bowlers:
            row['bowler__team__team_name'] = roster_team_map.get(row.get('bowler__id'), '')

    return render(request, 'home.html', {
        'all_tournaments': all_tournaments,
        'selected_tournament': selected_tournament,
        'completed_matches': completed_matches,
        'pending_matches': pending_matches,
        'live_matches': live_matches,
        'top_batsmen': top_batsmen,
        'top_bowlers': top_bowlers,
    })


def tournaments(request):
    from django.db.models import Count
    all_tournaments = TournamentDetails.objects.all().order_by('-id')

    # Compute real status for each tournament
    tournament_list = []
    for t in all_tournaments:
        # Check for any live innings
        has_live = Innings.objects.filter(
            match__tournament=t, status='IN_PROGRESS'
        ).exists()

        # Check if tournament is completed (uses existing helper)
        is_complete = _is_tournament_complete(t)

        if has_live:
            status = 'LIVE'
        elif is_complete:
            status = 'COMPLETED'
        else:
            status = 'UPCOMING'

        tournament_list.append({
            'tournament': t,
            'status': status,
        })

    return render(request, 'tournaments.html', {'tournament_list': tournament_list})


def tournamentdetails(request, id):
    tournament = get_object_or_404(TournamentDetails, id=id)
    teams = (
        TeamDetails.objects
        .filter(tournament_entries__tournament=tournament)
        .distinct()
        .order_by('team_name')
    )
    tournament_complete = _is_tournament_complete(tournament)
    if tournament_complete:
        award_tournament_awards(id)

    is_admin = request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser)

    # Categorise all matches
    live_matches = []
    upcoming_matches = []
    completed_matches = []

    started_match_ids = set(MatchStart.objects.values_list('match_id', flat=True))

    for match in CreateMatch.objects.filter(tournament=tournament).select_related('team1', 'team2').order_by('match_date'):
        inn1 = Innings.objects.filter(match=match, innings_number=1).first()
        inn2 = Innings.objects.filter(match=match, innings_number=2).first()

        if inn2 and inn2.status == 'COMPLETED':
            # Completed
            winner = margin = None
            try:
                mr = match.result
                winner = mr.winner
                margin = mr.result_summary
            except Exception:
                if inn2.total_runs > inn1.total_runs:
                    winner = inn2.batting_team
                    margin = f"{match.team1.team_name if inn2.batting_team == match.team1 else match.team2.team_name} won by {10 - inn2.total_wickets} wickets"
                elif inn1.total_runs > inn2.total_runs:
                    winner = inn1.batting_team
                    margin = f"{inn1.batting_team.team_name} won by {inn1.total_runs - inn2.total_runs} runs"
                else:
                    margin = "Match Tied"
            completed_matches.append({'match': match, 'inn1': inn1, 'inn2': inn2, 'winner': winner, 'margin': margin})

        elif (inn1 and inn1.status == 'IN_PROGRESS') or (inn2 and inn2.status == 'IN_PROGRESS'):
            live_matches.append({'match': match, 'inn1': inn1, 'inn2': inn2})

        elif match.id not in started_match_ids:
            # Detect knockout label
            ko_label = 'League'
            ko_is_knockout = False
            try:
                km = match.knockout_match
                ko_is_knockout = True
                STAGE_LABELS = {'PQF': 'Pre Quarter Final', 'QF': 'Quarter Final', 'SF': 'Semi Final', 'F': 'Final'}
                ko_label = STAGE_LABELS.get(km.stage.stage, km.stage.get_stage_display())
            except Exception:
                pass
            upcoming_matches.append({'match': match, 'ko_label': ko_label, 'is_knockout': ko_is_knockout})

        else:
            # Toss done / between innings — treat as live
            live_matches.append({'match': match, 'inn1': inn1, 'inn2': inn2})

    # Also tag completed & live with knockout info
    for group in [completed_matches, live_matches]:
        for md in group:
            try:
                km = md['match'].knockout_match
                STAGE_LABELS = {'PQF': 'Pre Quarter Final', 'QF': 'Quarter Final', 'SF': 'Semi Final', 'F': 'Final'}
                md['ko_label'] = STAGE_LABELS.get(km.stage.stage, km.stage.get_stage_display())
                md['is_knockout'] = True
            except Exception:
                md['ko_label'] = 'League'
                md['is_knockout'] = False

    # Build points table leaderboard
    max_overs = tournament.number_of_overs
    leaderboard = []
    for team in teams:
        wins = losses = 0
        rs = 0; of_ = 0.0; rc = 0; ob = 0.0
        for md in completed_matches:
            match_teams = [md['match'].team1, md['match'].team2]
            if team not in match_teams:
                continue
            i1, i2 = md.get('inn1'), md.get('inn2')
            if not i1 or not i2:
                continue
            bat_inn = i1 if i1.batting_team == team else i2
            bwl_inn = i2 if i1.batting_team == team else i1
            def b2o(b):
                return (b // 6) + ((b % 6) / 6)
            bat_ov = max_overs if (bat_inn.status == 'COMPLETED' and bat_inn.total_wickets < 10 and bat_inn.total_balls >= max_overs * 6) else b2o(bat_inn.total_balls) or max_overs
            bwl_ov = max_overs if (bwl_inn.status == 'COMPLETED' and bwl_inn.total_wickets < 10 and bwl_inn.total_balls >= max_overs * 6) else b2o(bwl_inn.total_balls) or max_overs
            rs += bat_inn.total_runs; of_ += bat_ov
            rc += bwl_inn.total_runs; ob += bwl_ov
            if md.get('winner') == team:
                wins += 1
            elif md.get('winner') is not None:
                losses += 1
        nrr = round((rs / of_) - (rc / ob), 3) if of_ > 0 and ob > 0 else 0.0
        leaderboard.append({
            'team': team, 'wins': wins, 'losses': losses,
            'played': wins + losses, 'points': wins * 2,
            'nrr': nrr, 'nrr_display': f"+{nrr:.3f}" if nrr >= 0 else f"{nrr:.3f}",
        })
    leaderboard.sort(key=lambda x: (-x['points'], -x['nrr']))
    for i, e in enumerate(leaderboard):
        e['rank'] = i + 1

    # ── PLAYER LEADERBOARDS ──
    # Get all innings for this tournament
    tournament_innings_ids = Innings.objects.filter(
        match__tournament=tournament
    ).values_list('id', flat=True)

    # Top run scorers
    from django.db.models import Sum, Count, F, FloatField, ExpressionWrapper, Case, When, Value
    batting_qs = (
        BattingScorecard.objects
        .filter(innings_id__in=tournament_innings_ids)
        .exclude(status='DNB')
        .values('batsman__id', 'batsman__player_name', 'batsman__photo')
        .annotate(
            total_runs=Sum('runs'),
            total_balls=Sum('balls_faced'),
            total_fours=Sum('fours'),
            total_sixes=Sum('sixes'),
            innings_count=Count('id'),
        )
        .filter(total_balls__gt=0)
        .order_by('-total_runs')
    )
    # Compute strike rate in Python (avoid db func complexity)
    top_run_scorers = []
    for b in batting_qs:
        sr = round((b['total_runs'] / b['total_balls']) * 100, 2) if b['total_balls'] else 0
        top_run_scorers.append({**b, 'strike_rate': sr})

    # Top strike rates (min 10 balls)
    top_strike_rates = sorted(
        [b for b in top_run_scorers if b['total_balls'] >= 10],
        key=lambda x: -x['strike_rate']
    )

    # Top wicket takers
    bowling_qs = (
        BowlingScorecard.objects
        .filter(innings_id__in=tournament_innings_ids)
        .values('bowler__id', 'bowler__player_name', 'bowler__photo')
        .annotate(
            total_wickets=Sum('wickets'),
            total_runs_given=Sum('runs_given'),
            innings_count=Count('id'),
        )
        .filter(total_wickets__gt=0)
        .order_by('-total_wickets', 'total_runs_given')
    )
    # Compute economy in Python
    def overs_to_balls_f(overs_val):
        o = float(overs_val)
        full = int(o); extra = round((o - full) * 10)
        return full * 6 + extra

    bowling_qs_with_overs = (
        BowlingScorecard.objects
        .filter(innings_id__in=tournament_innings_ids)
        .values('bowler__id', 'bowler__player_name', 'bowler__photo')
        .annotate(
            total_wickets=Sum('wickets'),
            total_runs_given=Sum('runs_given'),
            innings_count=Count('id'),
        )
        .order_by('-total_wickets', 'total_runs_given')
    )
    # Need overs — fetch individually
    from scoring.models import BowlingScorecard as BWS
    bowler_overs = {}
    for row in BWS.objects.filter(innings_id__in=tournament_innings_ids).select_related('bowler'):
        bid = row.bowler_id
        bowler_overs[bid] = bowler_overs.get(bid, 0) + overs_to_balls_f(row.overs_bowled)

    top_wicket_takers = []
    for b in bowling_qs:
        balls = bowler_overs.get(b['bowler__id'], 0)
        eco = round((b['total_runs_given'] * 6) / balls, 2) if balls > 0 else 0
        top_wicket_takers.append({**b, 'economy': eco, 'total_balls': balls})

    # Top economy (min 6 balls = 1 over)
    top_economy = sorted(
        [b for b in top_wicket_takers if b['total_balls'] >= 6 and b['economy'] > 0],
        key=lambda x: x['economy']
    )

    # Teams that have been assigned to at least one knockout match (any stage)
    # These are the "qualified" teams — chosen by the manager
    ko_team_ids = set()
    for km in KnockoutMatch.objects.filter(
        stage__tournament=tournament
    ).select_related('team1', 'team2'):
        if km.team1_id:
            ko_team_ids.add(km.team1_id)
        if km.team2_id:
            ko_team_ids.add(km.team2_id)

    # Only show Q badge if all league matches are complete
    # League matches = matches NOT linked to a knockout stage
    league_match_ids = set(
        CreateMatch.objects.filter(tournament=tournament).values_list('id', flat=True)
    ) - set(
        KnockoutMatch.objects.filter(
            stage__tournament=tournament
        ).exclude(match__isnull=True).values_list('match_id', flat=True)
    )
    all_league_done = not CreateMatch.objects.filter(
        id__in=league_match_ids
    ).exclude(
        id__in=MatchResult.objects.values_list('match_id', flat=True)
    ).exists() if league_match_ids else False

    qualified_team_ids = ko_team_ids if (all_league_done and ko_team_ids) else set()

    return render(request, 'tournamentdetails.html', {
        'tournament': tournament,
        'teams': teams,
        'tournament_complete': tournament_complete,
        'live_matches': live_matches,
        'upcoming_matches': upcoming_matches,
        'completed_matches': completed_matches,
        'leaderboard': leaderboard,
        'is_admin': is_admin,
        'top_run_scorers': top_run_scorers,
        'top_strike_rates': top_strike_rates,
        'top_wicket_takers': top_wicket_takers,
        'top_economy': top_economy,
        'qualified_team_ids': qualified_team_ids,
    })


def teamdetails(request, tournament_id, team_id):
    tournament = get_object_or_404(TournamentDetails, id=tournament_id)
    team = get_object_or_404(TeamDetails, id=team_id)

    tournament_team = get_object_or_404(
        TournamentTeam,
        tournament=tournament,
        team=team,
    )
    roster = (
        TournamentRoster.objects
        .filter(tournament_team=tournament_team)
        .select_related('player')
        .order_by('id')
    )
    return render(request, 'teamdetails.html', {
        'tournament': tournament,
        'team': team,
        'roster': roster,
    })