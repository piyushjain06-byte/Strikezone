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

from .views_awards import _is_tournament_complete

def live_scores_api(request):
    """Returns JSON with current scores of all live matches."""
    from scoring.models import Innings

    live_innings = Innings.objects.filter(status="IN_PROGRESS").select_related(
        'match', 'batting_team', 'bowling_team'
    )

    # Get unique live matches
    match_ids = list(set(i.match_id for i in live_innings))

    data = []
    for match_id in match_ids:
        inn1 = Innings.objects.filter(match_id=match_id, innings_number=1).first()
        inn2 = Innings.objects.filter(match_id=match_id, innings_number=2).first()

        entry = {
            'match_id': match_id,
            'inn1': None,
            'inn2': None,
        }

        if inn1:
            entry['inn1'] = {
                'runs': inn1.total_runs,
                'wickets': inn1.total_wickets,
                'overs': str(inn1.overs_completed),
                'team': str(inn1.batting_team),
            }
        if inn2:
            entry['inn2'] = {
                'runs': inn2.total_runs,
                'wickets': inn2.total_wickets,
                'overs': str(inn2.overs_completed),
                'team': str(inn2.batting_team),
            }

        data.append(entry)

    return JsonResponse({'matches': data})



# ── FLUTTER LIVE MATCHES API  –  /api/live-matches/ ──────────
def flutter_live_matches_api(request):
    """
    Returns live, recent, and upcoming matches in Flutter-friendly format.
    Combines live_scores_api + match result data in one call.
    """
    from matches.models import MatchResult
    from scoring.models import Innings

    def innings_dict(inn):
        if not inn: return None
        return {
            'team': str(inn.batting_team),
            'runs': inn.total_runs,
            'wickets': inn.total_wickets,
            'overs': str(inn.overs_completed),
        }

    def match_dict(match, inn1, inn2, status):
        result_obj = MatchResult.objects.filter(match=match).first()
        return {
            'id':         match.id,
            'team1':      match.team1.team_name,
            'team2':      match.team2.team_name,
            'tournament': match.tournament.tournament_name,
            'venue':      match.venue,
            'date':       str(match.match_date),
            'status':     status,
            'innings1':   innings_dict(inn1),
            'innings2':   innings_dict(inn2),
            'result':     result_obj.result_summary if result_obj else None,
            'winner':     result_obj.winner.team_name if result_obj and result_obj.winner else None,
        }

    live, recent, upcoming = [], [], []

    # Live — innings IN_PROGRESS
    live_match_ids = list(
        Innings.objects.filter(status='IN_PROGRESS')
        .values_list('match_id', flat=True).distinct()
    )
    for mid in live_match_ids:
        try:
            match = CreateMatch.objects.select_related('team1','team2','tournament').get(id=mid)
            inn1  = Innings.objects.filter(match=match, innings_number=1).first()
            inn2  = Innings.objects.filter(match=match, innings_number=2).first()
            live.append(match_dict(match, inn1, inn2, 'LIVE'))
        except Exception:
            pass

    # Recent — completed matches, last 10
    completed_ids = list(
        MatchResult.objects.select_related('match')
        .order_by('-id').values_list('match_id', flat=True)[:10]
    )
    for mid in completed_ids:
        if mid in live_match_ids: continue
        try:
            match = CreateMatch.objects.select_related('team1','team2','tournament').get(id=mid)
            inn1  = Innings.objects.filter(match=match, innings_number=1).first()
            inn2  = Innings.objects.filter(match=match, innings_number=2).first()
            recent.append(match_dict(match, inn1, inn2, 'COMPLETED'))
        except Exception:
            pass

    # Upcoming — matches with no innings yet
    all_started_ids = set(
        Innings.objects.values_list('match_id', flat=True).distinct()
    )
    upcoming_qs = (
        CreateMatch.objects
        .select_related('team1','team2','tournament')
        .exclude(id__in=all_started_ids)
        .order_by('match_date')[:10]
    )
    for match in upcoming_qs:
        upcoming.append(match_dict(match, None, None, 'UPCOMING'))

    return JsonResponse({'live': live, 'recent': recent, 'upcoming': upcoming})


# ── PUBLIC LIVE SCORECARD PAGE ──

def public_live_scorecard(request, match_id):
    """Public page anyone can view - shows live scoring with full data."""
    match = get_object_or_404(CreateMatch, id=match_id)

    inn1 = Innings.objects.filter(match=match, innings_number=1).first()
    inn2 = Innings.objects.filter(match=match, innings_number=2).first()

    # Determine current innings
    current_innings = None
    if inn2 and inn2.status == 'IN_PROGRESS':
        current_innings = inn2
    elif inn1 and inn1.status == 'IN_PROGRESS':
        current_innings = inn1

    # Current over balls
    current_over = None
    current_over_balls = []
    if current_innings:
        current_over = current_innings.overs.filter(is_completed=False).first()
        if current_over:
            current_over_balls = list(current_over.balls.all())

    # Batting scorecard for each innings
    batting_sc1 = BattingScorecard.objects.filter(innings=inn1).order_by('batting_position').select_related('batsman') if inn1 else []
    bowling_sc1 = BowlingScorecard.objects.filter(innings=inn1).select_related('bowler') if inn1 else []
    batting_sc2 = BattingScorecard.objects.filter(innings=inn2).order_by('batting_position').select_related('batsman') if inn2 else []
    bowling_sc2 = BowlingScorecard.objects.filter(innings=inn2).select_related('bowler') if inn2 else []

    # Full squad for "yet to bat"
    batting_tt1 = TournamentTeam.objects.filter(tournament=match.tournament, team=match.team1).first() if inn1 else None
    batting_tt2 = TournamentTeam.objects.filter(tournament=match.tournament, team=match.team2).first() if inn2 else None

    # Figure out which team bats in which innings — use actual innings batting teams
    inn1_batting_tt = TournamentTeam.objects.filter(tournament=match.tournament, team=inn1.batting_team).first() if inn1 else None
    inn2_batting_tt = TournamentTeam.objects.filter(tournament=match.tournament, team=inn2.batting_team).first() if inn2 else None

    # Players who batted in each innings (use IDs not names for accuracy)
    batted_ids1 = set(b.batsman_id for b in batting_sc1)
    batted_ids2 = set(b.batsman_id for b in batting_sc2)

    # Yet to bat — filter by player ID to avoid name-collision bugs
    def get_yet_to_bat(tt, batted_ids):
        if not tt:
            return []
        return list(
            PlayerDetails.objects.filter(tournament_rosters__tournament_team=tt)
            .exclude(id__in=batted_ids)
            .distinct()
            .order_by('player_name')
        )

    yet_to_bat1 = get_yet_to_bat(inn1_batting_tt, batted_ids1)
    yet_to_bat2 = get_yet_to_bat(inn2_batting_tt, batted_ids2)

    target = (inn1.total_runs + 1) if inn1 and inn2 else None

    # Determine current striker from last ball faced in current innings
    striker_id = None
    non_striker_id = None
    if current_innings:
        from scoring.models import Ball
        last_ball = Ball.objects.filter(
            over__innings=current_innings
        ).order_by('-over__over_number', '-ball_number').first()
        if last_ball:
            # The batter who faced the last ball
            faced_id = last_ball.batsman_id
            # If odd runs scored on a legal ball, strike rotated — the OTHER batsman is now on strike
            runs_off_bat = last_ball.runs_off_bat
            strike_rotated = last_ball.is_legal_ball and (runs_off_bat % 2 == 1)
            not_out_ids = list(
                BattingScorecard.objects.filter(
                    innings=current_innings, status='NOT_OUT'
                ).order_by('batting_position').values_list('batsman_id', flat=True)
            )
            if strike_rotated:
                # Striker is the other not-out batsman
                striker_id = next((pid for pid in not_out_ids if pid != faced_id), faced_id)
                non_striker_id = faced_id if faced_id in not_out_ids else (not_out_ids[0] if not_out_ids else None)
            else:
                striker_id = faced_id if faced_id in not_out_ids else None
                non_striker_id = next((pid for pid in not_out_ids if pid != faced_id), None)
                if striker_id is None and not_out_ids:
                    striker_id = not_out_ids[0]
                    non_striker_id = not_out_ids[1] if len(not_out_ids) > 1 else None
        else:
            not_out = BattingScorecard.objects.filter(
                innings=current_innings, status='NOT_OUT'
            ).order_by('batting_position')
            if not_out.count() >= 2:
                striker_id = not_out[0].batsman_id
                non_striker_id = not_out[1].batsman_id
            elif not_out.count() == 1:
                striker_id = not_out[0].batsman_id

    # Match result
    result = None
    try:
        from matches.models import MatchResult
        mr = match.result
        result = mr.result_summary
    except Exception:
        pass

    return render(request, 'public_live_scorecard.html', {
        'match': match,
        'inn1': inn1,
        'inn2': inn2,
        'current_innings': current_innings,
        'current_over': current_over,
        'current_over_balls': current_over_balls,
        'batting_sc1': batting_sc1,
        'bowling_sc1': bowling_sc1,
        'batting_sc2': batting_sc2,
        'bowling_sc2': bowling_sc2,
        'yet_to_bat1': yet_to_bat1,
        'yet_to_bat2': yet_to_bat2,
        'target': target,
        'result': result,
        'striker_id': striker_id,
        'non_striker_id': non_striker_id,
    })


# ── LIVE SCORECARD JSON API (for auto-refresh on public page) ──

def live_scorecard_api(request, match_id):
    """Returns full JSON data for the public live scorecard page."""
    match = get_object_or_404(CreateMatch, id=match_id)

    inn1 = Innings.objects.filter(match=match, innings_number=1).first()
    inn2 = Innings.objects.filter(match=match, innings_number=2).first()

    def innings_data(inn):
        if not inn:
            return None
        batting = []
        for b in BattingScorecard.objects.filter(innings=inn).order_by('batting_position').select_related('batsman'):
            batting.append({
                'name': b.batsman.player_name,
                'runs': b.runs,
                'balls': b.balls_faced,
                'fours': b.fours,
                'sixes': b.sixes,
                'sr': str(b.strike_rate),
                'status': b.status,
                'dismissal': b.dismissal_info or b.status,
            })
        bowling = []
        for b in BowlingScorecard.objects.filter(innings=inn).select_related('bowler'):
            bowling.append({
                'name': b.bowler.player_name,
                'overs': str(b.overs_bowled),
                'runs': b.runs_given,
                'wickets': b.wickets,
                'economy': str(b.economy),
                'wides': b.wides,
                'no_balls': b.no_balls,
            })

        # Current over balls
        current_over = inn.overs.filter(is_completed=False).first()
        over_balls = []
        if current_over:
            for ball in current_over.balls.all():
                if ball.is_wicket:
                    over_balls.append('W')
                elif ball.ball_type == 'WIDE':
                    over_balls.append('Wd')
                elif ball.ball_type == 'NO_BALL':
                    over_balls.append('Nb')
                else:
                    over_balls.append(str(ball.runs_off_bat))

        # Yet to bat — use IDs not names for accurate comparison
        batting_tt = TournamentTeam.objects.filter(tournament=match.tournament, team=inn.batting_team).first()
        batted_ids_set = set(b.batsman_id for b in BattingScorecard.objects.filter(innings=inn))
        yet_to_bat = []
        if batting_tt:
            for p in PlayerDetails.objects.filter(tournament_rosters__tournament_team=batting_tt).distinct():
                if p.id not in batted_ids_set:
                    yet_to_bat.append(p.player_name)

        # Determine striker / non-striker for this innings
        striker_id_api = None
        non_striker_id_api = None
        if inn.status == 'IN_PROGRESS':
            from scoring.models import Ball as _Ball
            last_ball = _Ball.objects.filter(over__innings=inn).order_by('-over__over_number', '-ball_number').first()
            not_out_ids = list(
                BattingScorecard.objects.filter(innings=inn, status='NOT_OUT')
                .order_by('batting_position').values_list('batsman_id', flat=True)
            )
            if last_ball and not_out_ids:
                faced_id = last_ball.batsman_id
                rotated  = last_ball.is_legal_ball and (last_ball.runs_off_bat % 2 == 1)
                if rotated:
                    striker_id_api     = next((p for p in not_out_ids if p != faced_id), faced_id)
                    non_striker_id_api = faced_id if faced_id in not_out_ids else None
                else:
                    striker_id_api     = faced_id if faced_id in not_out_ids else None
                    non_striker_id_api = next((p for p in not_out_ids if p != faced_id), None)
                    if striker_id_api is None and not_out_ids:
                        striker_id_api     = not_out_ids[0]
                        non_striker_id_api = not_out_ids[1] if len(not_out_ids) > 1 else None
            elif not_out_ids:
                striker_id_api     = not_out_ids[0]
                non_striker_id_api = not_out_ids[1] if len(not_out_ids) > 1 else None

        return {
            'team': str(inn.batting_team),
            'bowling_team': str(inn.bowling_team),
            'total_runs': inn.total_runs,
            'total_wickets': inn.total_wickets,
            'overs': str(inn.overs_completed),
            'extras': inn.extras,
            'status': inn.status,
            'innings_number': inn.innings_number,
            'batting': batting,
            'bowling': bowling,
            'over_balls': over_balls,
            'current_over_num': current_over.over_number if current_over else None,
            'current_bowler': current_over.bowler.player_name if current_over else None,
            'yet_to_bat': yet_to_bat,
            'striker_id': striker_id_api,
            'non_striker_id': non_striker_id_api,
        }

    target = (inn1.total_runs + 1) if inn1 and inn2 else None

    result_summary = None
    try:
        result_summary = match.result.result_summary
    except Exception:
        pass

    return JsonResponse({
        'match_id': match_id,
        'team1': str(match.team1),
        'team2': str(match.team2),
        'inn1': innings_data(inn1),
        'inn2': innings_data(inn2),
        'target': target,
        'result': result_summary,
    })

# ─────────────────────────────────────────────────────────────────
# PUBLIC TEAM PROFILE  –  /team/<team_id>/
# ─────────────────────────────────────────────────────────────────
def public_team_profile(request, team_id):
    from django.db.models import Sum, Count, Q

    team = get_object_or_404(TeamDetails, id=team_id)

    # All matches involving this team (across all tournaments)
    matches_qs = CreateMatch.objects.filter(
        Q(team1=team) | Q(team2=team)
    ).select_related('tournament', 'team1', 'team2').order_by('-match_date', '-id')

    # Build match context
    match_data = []
    wins = losses = ties = 0
    for match in matches_qs:
        result = getattr(match, 'result', None)
        innings = list(match.innings.order_by('innings_number'))
        inn1 = innings[0] if len(innings) > 0 else None
        inn2 = innings[1] if len(innings) > 1 else None

        status = 'pending'
        winner_name = None
        margin = None

        if result:
            status = 'completed'
            winner_name = result.winner.team_name if result.winner else None
            margin = result.result_summary
            if result.winner == team:
                wins += 1
            elif result.result_type == 'TIE':
                ties += 1
            else:
                losses += 1

        match_data.append({
            'match': match,
            'inn1': inn1,
            'inn2': inn2,
            'status': status,
            'winner': winner_name,
            'margin': margin,
        })

    total = wins + losses + ties
    win_pct = round((wins / total * 100) if total else 0)

    # Players across all rosters for this team
    rosters = TournamentRoster.objects.filter(
        tournament_team__team=team
    ).select_related('player', 'tournament_team__tournament').order_by('player__player_name')

    # Batting stats per player
    player_bat_stats = (
        BattingScorecard.objects
        .filter(batsman__tournament_rosters__tournament_team__team=team)
        .values('batsman__id', 'batsman__player_name', 'batsman__photo')
        .annotate(
            total_runs=Sum('runs'),
            total_balls=Sum('balls_faced'),
            total_fours=Sum('fours'),
            total_sixes=Sum('sixes'),
            innings_count=Count('id'),
        )
        .order_by('-total_runs')
    )

    player_bowl_stats = (
        BowlingScorecard.objects
        .filter(bowler__tournament_rosters__tournament_team__team=team)
        .values('bowler__id', 'bowler__player_name', 'bowler__photo')
        .annotate(
            total_wickets=Sum('wickets'),
            total_runs_given=Sum('runs_given'),
            innings_count=Count('id'),
        )
        .order_by('-total_wickets')
    )

    # ── Knockout Honours ──
    # Winner: won the Final
    winner_of = list(
        KnockoutMatch.objects.filter(
            stage__stage='F', winner=team, is_completed=True
        ).select_related('stage__tournament').order_by('-stage__tournament__start_date')
    )

    # Runner-up: was in the Final but did NOT win
    runnerup_of = list(
        KnockoutMatch.objects.filter(
            stage__stage='F', is_completed=True
        ).filter(
            django_models.Q(team1=team) | django_models.Q(team2=team)
        ).exclude(winner=team).select_related('stage__tournament').order_by('-stage__tournament__start_date')
    )

    # Semifinalist: was in SF but lost there (did not advance as winner)
    sf_of = list(
        KnockoutMatch.objects.filter(
            stage__stage='SF', is_completed=True
        ).filter(
            django_models.Q(team1=team) | django_models.Q(team2=team)
        ).exclude(winner=team).select_related('stage__tournament').order_by('-stage__tournament__start_date')
    )

    # Quarterfinalist: was in QF but lost there
    qf_of = list(
        KnockoutMatch.objects.filter(
            stage__stage='QF', is_completed=True
        ).filter(
            django_models.Q(team1=team) | django_models.Q(team2=team)
        ).exclude(winner=team).select_related('stage__tournament').order_by('-stage__tournament__start_date')
    )

    # Pre-QF exits
    pqf_of = list(
        KnockoutMatch.objects.filter(
            stage__stage='PQF', is_completed=True
        ).filter(
            django_models.Q(team1=team) | django_models.Q(team2=team)
        ).exclude(winner=team).select_related('stage__tournament').order_by('-stage__tournament__start_date')
    )

    return render(request, 'team_profile.html', {
        'team': team,
        'match_data': match_data,
        'wins': wins,
        'losses': losses,
        'ties': ties,
        'total': total,
        'win_pct': win_pct,
        'rosters': rosters,
        'player_bat_stats': player_bat_stats,
        'player_bowl_stats': player_bowl_stats,
        'winner_of': winner_of,
        'runnerup_of': runnerup_of,
        'sf_of': sf_of,
        'qf_of': qf_of,
        'pqf_of': pqf_of,
    })


# ─────────────────────────────────────────────────────────────────
# PUBLIC PLAYER PROFILE  –  /player/<player_id>/profile/
# ─────────────────────────────────────────────────────────────────
def public_player_profile(request, player_id):
    from django.db.models import Sum, Count, Max, Q

    player = get_object_or_404(PlayerDetails, id=player_id)

    # Is this the logged-in player viewing their own profile?
    is_own = (request.session.get('player_id') == player.id)

    # All rosters this player has been in
    rosters = TournamentRoster.objects.filter(
        player=player
    ).select_related('tournament_team__team', 'tournament_team__tournament').order_by('-id')

    # All innings IDs this player participated in (batting or bowling)
    bat_innings_ids = BattingScorecard.objects.filter(player=player).exclude(status='DNB').values_list('innings_id', flat=True)
    # ── BATTING ──
    bat_records = (
        BattingScorecard.objects
        .filter(batsman=player)
        .exclude(status='DNB')
        .select_related('innings__match__team1', 'innings__match__team2',
                        'innings__match__tournament', 'innings__batting_team')
        .order_by('-innings__match__match_date', '-id')
    )

    bat_totals = bat_records.aggregate(
        total_runs=Sum('runs'),
        total_balls=Sum('balls_faced'),
        total_fours=Sum('fours'),
        total_sixes=Sum('sixes'),
        innings_count=Count('id'),
        highest=Max('runs'),
    )
    total_runs   = bat_totals['total_runs'] or 0
    total_balls  = bat_totals['total_balls'] or 0
    total_fours  = bat_totals['total_fours'] or 0
    total_sixes  = bat_totals['total_sixes'] or 0
    innings_count = bat_totals['innings_count'] or 0
    highest      = bat_totals['highest'] or 0

    outs = bat_records.filter(status='OUT').count()
    bat_avg = round(total_runs / outs, 2) if outs > 0 else (total_runs if innings_count > 0 else 0)
    sr      = round((total_runs / total_balls) * 100, 2) if total_balls > 0 else 0

    bat_total = {
        'total_runs': total_runs,
        'total_balls': total_balls,
        'total_fours': total_fours,
        'total_sixes': total_sixes,
        'innings_count': innings_count,
        'highest': highest,
    }

    # ── BOWLING ──
    bowl_records = (
        BowlingScorecard.objects
        .filter(bowler=player)
        .select_related('innings__match__team1', 'innings__match__team2',
                        'innings__match__tournament', 'innings__batting_team')
        .order_by('-innings__match__match_date', '-id')
    )

    bowl_totals = bowl_records.aggregate(
        total_wickets=Sum('wickets'),
        total_runs_given=Sum('runs_given'),
        innings_count=Count('id'),
    )
    total_wickets    = bowl_totals['total_wickets'] or 0
    total_runs_given = bowl_totals['total_runs_given'] or 0
    bowl_innings     = bowl_totals['innings_count'] or 0

    bowl_avg  = round(total_runs_given / total_wickets, 2) if total_wickets > 0 else None
    best_spell = None
    for b in bowl_records:
        if best_spell is None or b.wickets > best_spell.wickets or (b.wickets == best_spell.wickets and b.runs_given < best_spell.runs_given):
            best_spell = b

    bowl_total = {
        'total_wickets': total_wickets,
        'total_runs_given': total_runs_given,
        'innings_count': bowl_innings,
        'best': f"{best_spell.wickets}/{best_spell.runs_given}" if best_spell else '–',
    }

    # ── MOM AWARDS ──
    mom_awards = ManOfTheMatch.objects.filter(
        player=player
    ).select_related('match__team1', 'match__team2', 'match__tournament').order_by('-match__match_date')

    # ── TOURNAMENT AWARDS ──
    tournament_awards = TournamentAward.objects.filter(
        player=player
    ).select_related('tournament').order_by('-tournament__start_date')

    # Profile URL for QR code
    profile_url = request.build_absolute_uri(f'/player/{player.id}/profile/')

    return render(request, 'player_profile.html', {
        'player': player,
        'is_own': is_own,
        'rosters': rosters,
        'bat_records': bat_records,
        'bowl_records': bowl_records,
        'bat_total': bat_total,
        'bowl_total': bowl_total,
        'bat_avg': bat_avg,
        'sr': sr,
        'bowl_avg': bowl_avg,
        'mom_awards': mom_awards,
        'tournament_awards': tournament_awards,
        'profile_url': profile_url,
    })


def global_search_api(request):
    from django.db.models import Count, Q

    q = request.GET.get('q', '').strip()
    if len(q) < 1:
        return JsonResponse({'tournaments': [], 'teams': [], 'players': []})

    # ── Tournaments ──
    tournaments = (
        TournamentDetails.objects
        .filter(tournament_name__icontains=q)
        .order_by('tournament_name')[:6]
    )
    t_results = []
    for t in tournaments:
        t_results.append({
            'id': t.id,
            'name': t.tournament_name,
            'sub': f"{t.get_tournament_type_display()} · {t.number_of_overs} overs",
        })

    # ── Teams (sorted by match count for exact-name ties) ──
    teams_qs = (
        TeamDetails.objects
        .filter(team_name__icontains=q)
        .annotate(
            match_count=Count('team1_matches', distinct=True) + Count('team2_matches', distinct=True)
        )
        .order_by('-match_count', 'team_name')[:6]
    )
    team_results = []
    for t in teams_qs:
        team_results.append({
            'id': t.id,
            'name': t.team_name,
            'sub': f"{t.match_count} matches played",
        })

    # ── Players (sorted by match count for exact-name ties) ──
    players_qs = (
        PlayerDetails.objects
        .filter(player_name__icontains=q)
        .annotate(
            match_count=Count(
                'tournament_rosters__tournament_team__tournament__matches',
                distinct=True
            )
        )
        .order_by('-match_count', 'player_name')[:6]
    )
    player_results = []
    for p in players_qs:
        photo_url = p.photo.url if p.photo else None
        # Get team name from most recent roster
        roster = p.tournament_rosters.select_related('tournament_team__team').order_by('-id').first()
        team_name = roster.tournament_team.team.team_name if roster else 'No team'
        player_results.append({
            'id': p.id,
            'name': p.player_name,
            'photo': photo_url,
            'sub': team_name,
        })

    return JsonResponse({
        'tournaments': t_results,
        'teams': team_results,
        'players': player_results,
    })

# ─────────────────────────────────────────────────────────────
#  MATCH ANALYSIS  — AI-powered post-match deep dive
# ─────────────────────────────────────────────────────────────