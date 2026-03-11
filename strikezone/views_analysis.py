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

from .views_awards import _is_tournament_complete, award_tournament_awards
from subscriptions.decorators import require_plan

@require_plan('pro', 'pro_plus')
def player_analysis_view(request, player_id):
    player = get_object_or_404(PlayerDetails, id=player_id)
    session_player_id = request.session.get('player_id')
    is_own = (str(session_player_id) == str(player_id))
    return render(request, 'player_analysis.html', {
        'player': player,
        'is_own': is_own,
    })


@require_plan('pro', 'pro_plus')
def player_analysis_api(request, player_id):
    """Always returns JSON — never crashes to HTML."""
    try:
        from django.db.models import Sum, Count, Max

        try:
            from strikezone.player_ml import generate_player_charts
            ML_AVAILABLE = True
        except Exception as ml_err:
            ML_AVAILABLE = False
            ML_ERROR = str(ml_err)
            generate_player_charts = None

        ZONE_MAP = {
            'FINE_LEG':0,'SQUARE_LEG':1,'MID_WICKET':2,'MID_ON':3,
            'STRAIGHT':4,'LONG_ON':5,'LONG_OFF':6,'MID_OFF':7,
            'COVER':8,'POINT':9,'THIRD_MAN':10,'FINE_LEG_DEEP':11,
        }

        player = get_object_or_404(PlayerDetails, id=player_id)

        # ── BATTING ──────────────────────────────────────────
        bat_qs = (BattingScorecard.objects
                  .filter(batsman=player)
                  .exclude(status='DNB')
                  .select_related('innings__match__team1','innings__match__team2','innings__batting_team')
                  .order_by('innings__match__match_date','id'))

        bat_records = []
        dismissals  = {}
        for r in bat_qs:
            try:
                opp = (r.innings.match.team2
                       if r.innings.batting_team == r.innings.match.team1
                       else r.innings.match.team1)
                opp_name = opp.team_name[:8]
            except Exception:
                opp_name = 'opp'
            sr_val = float(r.strike_rate) if r.balls_faced else 0
            bat_records.append({
                'runs': r.runs, 'balls': r.balls_faced,
                'fours': r.fours, 'sixes': r.sixes,
                'sr': sr_val, 'status': r.status,
                'dismissal': r.dismissal_info or '',
                'match_label': f"vs {opp_name}",
            })
            if r.status == 'OUT':
                # Parse dismissal_info e.g. "b Rohit", "c Kohli b Rohit", "lbw b Rohit", "run out"
                info = (r.dismissal_info or '').strip().lower()
                if info.startswith('c & b') or info.startswith('c&b'):
                    key = 'Caught & Bowled'
                elif info.startswith('c '):
                    key = 'Caught'
                elif info.startswith('b '):
                    key = 'Bowled'
                elif info.startswith('lbw'):
                    key = 'LBW'
                elif info.startswith('st ') or info.startswith('st †'):
                    key = 'Stumped'
                elif info.startswith('run out'):
                    key = 'Run Out'
                elif info.startswith('hit wicket'):
                    key = 'Hit Wicket'
                elif info.startswith('retired'):
                    key = 'Retired'
                else:
                    key = 'Out'
                dismissals[key] = dismissals.get(key, 0) + 1

        agg = bat_qs.aggregate(
            total_runs=Sum('runs'), total_balls=Sum('balls_faced'),
            total_fours=Sum('fours'), total_sixes=Sum('sixes'),
            innings=Count('id'), highest=Max('runs'),
        )
        total_runs    = agg['total_runs'] or 0
        total_balls   = agg['total_balls'] or 0
        total_innings = agg['innings'] or 0
        # Cricket average = runs / dismissals (not-out innings excluded from denominator)
        dismissal_count = bat_qs.filter(status='OUT').count()
        bat_avg = round(total_runs / dismissal_count, 1) if dismissal_count else total_runs
        bat_sr  = round(total_runs / total_balls * 100, 1) if total_balls else 0
        fifties  = sum(1 for r in bat_records if 50 <= r['runs'] < 100)
        hundreds = sum(1 for r in bat_records if r['runs'] >= 100)

        # Hat-tricks — safely import (model may not be registered in scoring/models.py yet)
        hat_trick_list = []
        hat_trick_count = 0
        try:
            from scoring.models import HatTrick
            hat_tricks_qs = HatTrick.objects.filter(bowler=player).select_related(
                'match__team1', 'match__team2', 'match__tournament',
                'victim1', 'victim2', 'victim3',
            ).order_by('-created_at')
            for ht in hat_tricks_qs:
                victims = ''
                try:
                    victims = ht.victims_display()
                except Exception:
                    parts = []
                    for v in [ht.victim1, ht.victim2, ht.victim3]:
                        if v: parts.append(v.player_name)
                    victims = ', '.join(parts)
                hat_trick_list.append({
                    'match': f"{ht.match.team1} vs {ht.match.team2}",
                    'tournament': ht.match.tournament.tournament_name,
                    'date': str(ht.match.match_date),
                    'victims': victims,
                })
            hat_trick_count = len(hat_trick_list)
        except Exception:
            pass

        # Phase batting — count balls directly from Ball model per over range
        inn_ids = list(bat_qs.values_list('innings_id', flat=True))
        phase_batting = {}
        for phase, ov_start, ov_end in [('powerplay',1,6),('middle',7,15),('death',16,99)]:
            phase_balls = Ball.objects.filter(
                over__innings_id__in=inn_ids,
                batsman=player,                       # ← only THIS player's balls
                over__over_number__gte=ov_start,
                over__over_number__lte=ov_end,
                is_legal_ball=True,
            )
            agg_phase = phase_balls.aggregate(runs=Sum('runs_off_bat'), cnt=Count('id'))
            phase_batting[phase] = {
                'runs':   agg_phase['runs'] or 0,
                'balls':  agg_phase['cnt'] or 0,
                'innings': max(total_innings, 1),
            }

        # Shot zones
        shot_zones = []
        zone_balls = Ball.objects.filter(
            over__innings_id__in=inn_ids,
            shot_direction__isnull=False,
            is_legal_ball=True,
        ).values('shot_direction', 'runs_off_bat')
        for b in zone_balls:
            z = ZONE_MAP.get(b['shot_direction'])
            if z is not None:
                shot_zones.append({'zone': z, 'runs': b['runs_off_bat']})

        # ── BOWLING ──────────────────────────────────────────
        bowl_qs = (BowlingScorecard.objects
                   .filter(bowler=player)
                   .select_related('innings__match__team1','innings__match__team2','innings__batting_team')
                   .order_by('innings__match__match_date','id'))

        bowl_records = []
        for r in bowl_qs:
            try:
                opp = (r.innings.match.team2
                       if r.innings.batting_team == r.innings.match.team1
                       else r.innings.match.team1)
                opp_name = opp.team_name[:8]
            except Exception:
                opp_name = 'opp'
            bowl_records.append({
                'overs': float(r.overs_bowled), 'runs': r.runs_given,
                'wickets': r.wickets, 'economy': float(r.economy),
                'wides': r.wides, 'no_balls': r.no_balls,
                'match_label': f"vs {opp_name}",
            })

        bowl_agg = bowl_qs.aggregate(
            total_wickets=Sum('wickets'), total_runs=Sum('runs_given'), innings=Count('id'))
        total_wickets  = bowl_agg['total_wickets'] or 0
        total_bowl_runs= bowl_agg['total_runs'] or 0
        bowl_avg = round(total_bowl_runs / total_wickets, 1) if total_wickets else None
        best_spell = max(bowl_records, key=lambda x: x['wickets'], default=None)
        best_fig   = f"{best_spell['wickets']}/{best_spell['runs']}" if best_spell else '—'

        # ── RADAR METRICS (0–100) ─────────────────────────────
        def norm(val, lo, hi):
            if hi <= lo: return 50
            return round(max(0, min(100, (val - lo) / (hi - lo) * 100)))

        radar_metrics = {}
        # Always build radar from whatever data is available (even 1 innings)
        if total_innings >= 1:
            radar_metrics['Average']     = norm(bat_avg, 0, 60)
            radar_metrics['Strike Rate'] = norm(bat_sr, 60, 200)
            consistency = 100 - round(sum(1 for r in bat_records if r['runs'] < 10) / max(total_innings,1) * 100)
            radar_metrics['Consistency'] = max(0, consistency)
            boundary_runs = (agg['total_fours'] or 0)*4 + (agg['total_sixes'] or 0)*6
            radar_metrics['Boundary %']  = norm(boundary_runs, 0, max(total_runs*0.7, 1))
            radar_metrics['Big Scores']  = norm(fifties*15 + hundreds*30, 0, 100)
            radar_metrics['Impact']      = norm(bat_avg + bat_sr/5, 0, 80)
        if total_wickets >= 1:
            bowl_ecos = [r['economy'] for r in bowl_records if r['overs'] > 0]
            avg_eco = sum(bowl_ecos)/len(bowl_ecos) if bowl_ecos else 8
            radar_metrics['Economy']    = norm(10 - avg_eco, 0, 10)
            radar_metrics['Wickets']    = norm(total_wickets, 0, 30)
            strike_rate_bowl = sum(1 for r in bowl_records if r['wickets']>=1)/max(len(bowl_records),1)*100
            radar_metrics['Penetration']= norm(strike_rate_bowl, 0, 100)

        # ── STRENGTHS & WEAKNESSES ────────────────────────────
        strengths, weaknesses = [], []

        if total_innings >= 1:
            if bat_sr >= 150:  strengths.append('Explosive striker — SR above 150')
            elif bat_sr >= 120: strengths.append('Good striker of the ball')
            elif bat_sr < 100 and total_innings > 3: weaknesses.append(f'Strike rate below 100 ({bat_sr}) — needs to accelerate')

            if bat_avg >= 35:  strengths.append('Reliable batsman — high average')
            elif bat_avg >= 20: strengths.append('Decent batting average')
            elif bat_avg < 15 and total_innings > 3: weaknesses.append(f'Low batting average ({bat_avg})')

        if fifties >= 3:   strengths.append(f'Converts starts — {fifties} fifties')
        if hundreds >= 1:  strengths.append(f'Match winner — {hundreds} century/ies')
        if hat_trick_count >= 1: strengths.append(f'Hat-trick hero — {hat_trick_count} hat-trick{"s" if hat_trick_count > 1 else ""}')

        ducks = sum(1 for r in bat_records if r['runs'] == 0)
        if ducks >= 2:     weaknesses.append(f'Prone to ducks ({ducks} times)')

        sixes_total = agg['total_sixes'] or 0
        if sixes_total >= 5: strengths.append(f'Big hitter — {sixes_total} sixes')

        if total_wickets >= 10: strengths.append(f'Reliable wicket-taker ({total_wickets} wickets)')
        if bowl_avg and bowl_avg < 15: strengths.append(f'Excellent bowling average ({bowl_avg})')
        if bowl_records:
            avg_eco2 = sum(r['economy'] for r in bowl_records)/len(bowl_records)
            if avg_eco2 < 7:   strengths.append(f'Economy bowler — avg {avg_eco2:.1f}')
            elif avg_eco2 > 10: weaknesses.append(f'Expensive — avg economy {avg_eco2:.1f}')

        if dismissals:
            top_d = max(dismissals, key=dismissals.get)
            if dismissals[top_d] >= 3:
                weaknesses.append(f'Often dismissed {top_d} ({dismissals[top_d]} times)')

        # ── BOWLER DISMISSAL ALERT — which bowlers dismiss this player most ──
        bowler_dismissal_alerts = []
        # Per-bowler dismissal count (exclude run-outs where bowler isn't credited)
        bowler_dismiss_counts = {}
        for ball in Ball.objects.filter(
            player_dismissed=player, is_wicket=True
        ).exclude(wicket_type='RUN_OUT').select_related('bowler'):
            if ball.bowler:
                bname = ball.bowler.player_name
                bowler_dismiss_counts[bname] = bowler_dismiss_counts.get(bname, 0) + 1

        for bname, cnt in sorted(bowler_dismiss_counts.items(), key=lambda x: -x[1]):
            if cnt >= 2:
                bowler_dismissal_alerts.append({
                    'bowler': bname,
                    'count': cnt,
                    'alert': f'⚠️ Dismissed by {bname} {cnt} times — needs a plan against this bowler'
                })
                weaknesses.append(f'Vulnerable to {bname} — dismissed {cnt} times')

        if not strengths:  strengths.append('Building stats — more matches needed')
        if not weaknesses: weaknesses.append('No major weaknesses identified yet')

        data = {
            'player': {'id': player.id, 'name': player.player_name},
            'batting': {
                'innings': total_innings, 'runs': total_runs,
                'avg': bat_avg, 'sr': bat_sr,
                'highest': agg.get('highest') or 0,
                'fours': agg.get('total_fours') or 0,
                'sixes': sixes_total,
                'fifties': fifties, 'hundreds': hundreds,
                'hat_tricks': hat_trick_count,
            },
            'bowling': {
                'innings': len(bowl_records), 'wickets': total_wickets,
                'avg': bowl_avg, 'best': best_fig,
            },
            'strengths': strengths,
            'weaknesses': weaknesses,
            'bowler_dismissal_alerts': bowler_dismissal_alerts,
            'bat_records': bat_records,
            'bowl_records': bowl_records,
            'dismissals': dismissals,
            'shot_zones': shot_zones,
            'phase_batting': phase_batting,
            'radar_metrics': radar_metrics,
            'hat_tricks': hat_trick_list,
        }

        # ML charts
        if ML_AVAILABLE and generate_player_charts:
            try:
                data['charts'] = generate_player_charts({
                    'name': player.player_name,
                    'bat_records': bat_records,
                    'bowl_records': bowl_records,
                    'dismissals': dismissals,
                    'shot_zones': shot_zones,
                    'phase_batting': phase_batting,
                    'radar_metrics': radar_metrics,
                })
            except Exception as chart_err:
                data['charts'] = {'error': str(chart_err)}
        else:
            data['charts'] = {
                'error': ML_ERROR if not ML_AVAILABLE else 'Charts unavailable',
                'install_hint': 'pip install matplotlib scikit-learn numpy pandas'
            }

        return JsonResponse(data)

    except Exception as e:
        import traceback
        return JsonResponse({
            'error': str(e),
            'traceback': traceback.format_exc(),
            'batting': {'innings':0,'runs':0,'avg':0,'sr':0,'highest':0,'fours':0,'sixes':0,'fifties':0,'hundreds':0},
            'bowling': {'innings':0,'wickets':0,'avg':None,'best':'—'},
            'strengths': ['Error loading data'],
            'weaknesses': [str(e)],
            'charts': {'error': str(e)},
        }, status=200)  # 200 so browser doesn't show HTML error page

# ─────────────────────────────────────────────────────────────────
# TEAM ANALYSIS VIEW  –  /team/<id>/analysis/
# ─────────────────────────────────────────────────────────────────
@require_plan('pro', 'pro_plus')
def team_analysis_view(request, team_id):
    from teams.models import TeamDetails
    team = get_object_or_404(TeamDetails, id=team_id)
    return render(request, 'team_analysis.html', {'team': team})


@require_plan('pro', 'pro_plus')
def team_analysis_api(request, team_id):
    """Returns full team analysis JSON with charts."""
    try:
        from django.db.models import Sum, Count, Q, Avg, Max
        from teams.models import TeamDetails

        try:
            from strikezone.team_ml import generate_team_charts
            ML_AVAILABLE = True
        except Exception as ml_err:
            ML_AVAILABLE = False
            ML_ERROR = str(ml_err)
            generate_team_charts = None

        team = get_object_or_404(TeamDetails, id=team_id)

        # All matches involving this team
        all_matches = CreateMatch.objects.filter(
            Q(team1=team) | Q(team2=team)
        ).select_related('tournament', 'team1', 'team2').order_by('match_date')

        # Collect match results
        wins = losses = ties = 0
        match_records = []
        win_streak = 0
        max_win_streak = 0
        cur_streak = 0

        for match in all_matches:
            inn1 = Innings.objects.filter(match=match, innings_number=1).first()
            inn2 = Innings.objects.filter(match=match, innings_number=2).first()
            if not inn2 or inn2.status != 'COMPLETED':
                continue

            result = getattr(match, 'result', None)
            if not result:
                continue

            won = result.winner == team
            tied = result.result_type == 'TIE' if hasattr(result, 'result_type') else False

            if won:
                wins += 1
                cur_streak += 1
                max_win_streak = max(max_win_streak, cur_streak)
            elif tied:
                ties += 1
                cur_streak = 0
            else:
                losses += 1
                cur_streak = 0

            # Batting/bowling innings for this team
            team_batting_inn = inn1 if inn1.batting_team == team else inn2
            team_bowling_inn = inn2 if inn1.batting_team == team else inn1

            match_records.append({
                'match_id': match.id,
                'opponent': match.team2.team_name if match.team1 == team else match.team1.team_name,
                'tournament': match.tournament.tournament_name,
                'date': str(match.match_date),
                'won': won,
                'tied': tied,
                'team_runs': team_batting_inn.total_runs,
                'team_wickets': team_batting_inn.total_wickets,
                'team_balls': team_batting_inn.total_balls,
                'opp_runs': team_bowling_inn.total_runs,
                'opp_wickets': team_bowling_inn.total_wickets,
                'opp_balls': team_bowling_inn.total_balls,
                'result_summary': result.result_summary,
                'venue': match.venue or '',
            })

        total = wins + losses + ties
        win_pct = round(wins / total * 100) if total else 0

        # ── Player stats for this team ──
        from teams.models import TournamentRoster, TournamentTeam
        rosters = TournamentRoster.objects.filter(
            tournament_team__team=team
        ).select_related('player')
        player_ids = list(rosters.values_list('player_id', flat=True).distinct())

        # All innings where this team batted/bowled
        team_batting_innings = Innings.objects.filter(
            batting_team=team, status='COMPLETED'
        )
        team_bowling_innings = Innings.objects.filter(
            bowling_team=team, status='COMPLETED'
        )

        # Batting stats per player
        bat_stats = {}
        for bsc in BattingScorecard.objects.filter(
            innings__in=team_batting_innings,
            batsman_id__in=player_ids
        ).exclude(status='DNB').select_related('batsman'):
            pid = bsc.batsman_id
            if pid not in bat_stats:
                bat_stats[pid] = {
                    'name': bsc.batsman.player_name,
                    'innings': 0, 'runs': 0, 'balls': 0,
                    'fours': 0, 'sixes': 0, 'not_outs': 0,
                    'highest': 0, 'fifties': 0, 'hundreds': 0,
                }
            s = bat_stats[pid]
            s['innings'] += 1
            s['runs'] += bsc.runs
            s['balls'] += bsc.balls_faced
            s['fours'] += bsc.fours
            s['sixes'] += bsc.sixes
            if bsc.status == 'NOT_OUT':
                s['not_outs'] += 1
            s['highest'] = max(s['highest'], bsc.runs)
            if bsc.runs >= 100:
                s['hundreds'] += 1
            elif bsc.runs >= 50:
                s['fifties'] += 1

        for pid, s in bat_stats.items():
            dismissals = s['innings'] - s['not_outs']
            s['avg'] = round(s['runs'] / dismissals, 1) if dismissals > 0 else s['runs']
            s['sr'] = round(s['runs'] / s['balls'] * 100, 1) if s['balls'] > 0 else 0

        # Bowling stats per player
        bowl_stats = {}
        for bwsc in BowlingScorecard.objects.filter(
            innings__in=team_bowling_innings,
            bowler_id__in=player_ids
        ).select_related('bowler'):
            pid = bwsc.bowler_id
            if pid not in bowl_stats:
                bowl_stats[pid] = {
                    'name': bwsc.bowler.player_name,
                    'innings': 0, 'wickets': 0, 'runs': 0, 'balls': 0,
                    'wides': 0, 'no_balls': 0,
                    'best_w': 0, 'best_r': 999,
                }
            s = bowl_stats[pid]
            s['innings'] += 1
            s['wickets'] += bwsc.wickets
            s['runs'] += bwsc.runs_given
            s['wides'] += bwsc.wides
            s['no_balls'] += bwsc.no_balls
            ov = float(bwsc.overs_bowled)
            full = int(ov)
            extra = round((ov - full) * 10)
            s['balls'] += full * 6 + extra
            if (bwsc.wickets > s['best_w'] or
                    (bwsc.wickets == s['best_w'] and bwsc.runs_given < s['best_r'])):
                s['best_w'] = bwsc.wickets
                s['best_r'] = bwsc.runs_given

        for pid, s in bowl_stats.items():
            s['avg'] = round(s['runs'] / s['wickets'], 1) if s['wickets'] > 0 else None
            s['economy'] = round(s['runs'] * 6 / s['balls'], 2) if s['balls'] > 0 else 0
            if s['best_r'] == 999:
                s['best_r'] = 0

        # Sort by runs/wickets
        top_batsmen = sorted(bat_stats.values(), key=lambda x: -x['runs'])[:8]
        top_bowlers = sorted(bowl_stats.values(), key=lambda x: (-x['wickets'], x.get('economy', 99)))[:8]

        # ── Team batting phases ──
        phase_data = {'powerplay': {'runs': 0, 'wickets': 0, 'overs': 0},
                      'middle':    {'runs': 0, 'wickets': 0, 'overs': 0},
                      'death':     {'runs': 0, 'wickets': 0, 'overs': 0}}
        max_ov = 20  # default T20
        if all_matches.exists():
            max_ov = all_matches.first().tournament.number_of_overs

        for inn in team_batting_innings:
            for ov in Over.objects.filter(innings=inn).order_by('over_number'):
                n = ov.over_number
                if n <= min(6, max_ov):
                    phase_data['powerplay']['runs'] += ov.runs_in_over
                    phase_data['powerplay']['wickets'] += ov.wickets_in_over
                    phase_data['powerplay']['overs'] += 1
                elif n <= min(int(max_ov * 0.75), max_ov):
                    phase_data['middle']['runs'] += ov.runs_in_over
                    phase_data['middle']['wickets'] += ov.wickets_in_over
                    phase_data['middle']['overs'] += 1
                else:
                    phase_data['death']['runs'] += ov.runs_in_over
                    phase_data['death']['wickets'] += ov.wickets_in_over
                    phase_data['death']['overs'] += 1

        # ── Captain analysis ──
        captain_info = None
        captain_roster = rosters.filter(is_captain=True).first()
        if captain_roster:
            cap = captain_roster.player
            cap_bat = bat_stats.get(cap.id, {})
            cap_bowl = bowl_stats.get(cap.id, {})
            cap_wins = sum(1 for m in match_records if m['won'])
            captain_info = {
                'name': cap.player_name,
                'batting_runs': cap_bat.get('runs', 0),
                'batting_avg': cap_bat.get('avg', 0),
                'batting_sr': cap_bat.get('sr', 0),
                'bowling_wickets': cap_bowl.get('wickets', 0),
                'bowling_economy': cap_bowl.get('economy', 0),
                'matches_led': total,
                'wins': wins,
                'win_pct': win_pct,
            }

        # ── Loss analysis ──
        loss_reasons = []
        if losses > 0:
            low_score_losses = sum(1 for m in match_records if not m['won'] and not m['tied'] and m['team_runs'] < m['opp_runs'] - 20)
            collapse_losses = sum(1 for m in match_records if not m['won'] and not m['tied'] and m['team_wickets'] >= 8)
            bowling_failures = sum(1 for m in match_records if not m['won'] and not m['tied'] and m['opp_runs'] > m['team_runs'] and m['opp_wickets'] < 5)
            close_losses = sum(1 for m in match_records if not m['won'] and not m['tied'] and abs(m['team_runs'] - m['opp_runs']) <= 10)

            if low_score_losses >= 1:
                loss_reasons.append(f'Batting collapses — scored well below par in {low_score_losses} loss(es)')
            if collapse_losses >= 1:
                loss_reasons.append(f'Lost 8+ wickets in {collapse_losses} match(es) — lower order fragile')
            if bowling_failures >= 1:
                loss_reasons.append(f'Bowling leaked runs in {bowling_failures} match(es) — opposition batted freely')
            if close_losses >= 1:
                loss_reasons.append(f'{close_losses} close loss(es) within 10 runs — finishing/bowling needs work')

        # ── Win factors ──
        win_reasons = []
        if wins > 0:
            dominant_wins = sum(1 for m in match_records if m['won'] and m['team_runs'] - m['opp_runs'] > 20)
            bowling_wins = sum(1 for m in match_records if m['won'] and m['opp_wickets'] >= 7)
            if dominant_wins >= 1:
                win_reasons.append(f'Dominant batting performances in {dominant_wins} match(es)')
            if bowling_wins >= 1:
                win_reasons.append(f'Tight bowling restricted opponents in {bowling_wins} match(es)')
            if max_win_streak >= 2:
                win_reasons.append(f'Best win streak of {max_win_streak} consecutive wins shows consistency')

        # ── Performers ──
        performers = []
        for pid, s in bat_stats.items():
            if s['innings'] >= 2 and s['avg'] >= 25:
                performers.append({'name': s['name'], 'role': 'Batsman',
                                   'stat': f"{s['runs']} runs @ avg {s['avg']}", 'type': 'bat'})
        for pid, s in bowl_stats.items():
            if s['wickets'] >= 3:
                performers.append({'name': s['name'], 'role': 'Bowler',
                                   'stat': f"{s['wickets']} wickets, eco {s['economy']}", 'type': 'bowl'})
        performers.sort(key=lambda x: 0 if x['type'] == 'bat' else 1)

        # ── Underperformers ──
        underperformers = []
        for pid, s in bat_stats.items():
            if s['innings'] >= 3 and s['avg'] < 12:
                underperformers.append({'name': s['name'], 'issue': f"Low average: {s['avg']} in {s['innings']} innings"})
        for pid, s in bowl_stats.items():
            if s['innings'] >= 2 and s['economy'] > 10:
                underperformers.append({'name': s['name'], 'issue': f"High economy: {s['economy']} in {s['innings']} spells"})

        # ── Build response ──
        data = {
            'team': {'id': team.id, 'name': team.team_name},
            'summary': {
                'total': total, 'wins': wins, 'losses': losses, 'ties': ties,
                'win_pct': win_pct, 'max_win_streak': max_win_streak,
            },
            'match_records': match_records,
            'top_batsmen': top_batsmen,
            'top_bowlers': top_bowlers,
            'phase_data': phase_data,
            'captain': captain_info,
            'loss_reasons': loss_reasons,
            'win_reasons': win_reasons,
            'performers': performers[:6],
            'underperformers': underperformers[:4],
        }

        # ── ML Charts ──
        if ML_AVAILABLE and generate_team_charts:
            try:
                data['charts'] = generate_team_charts(data)
            except Exception as ce:
                data['charts'] = {'error': str(ce)}
        else:
            data['charts'] = {
                'error': ML_ERROR if not ML_AVAILABLE else 'Charts unavailable',
                'install_hint': 'pip install matplotlib numpy pandas'
            }

        return JsonResponse(data)

    except Exception as e:
        import traceback
        return JsonResponse({
            'error': str(e),
            'traceback': traceback.format_exc(),
            'summary': {'total': 0, 'wins': 0, 'losses': 0, 'ties': 0, 'win_pct': 0},
            'charts': {'error': str(e)},
        }, status=200)



def _build_match_data_prompt(match_id):
    """Collect all match data and format it into a rich prompt for Groq."""
    match = get_object_or_404(CreateMatch, id=match_id)
    tournament = match.tournament

    inn1 = Innings.objects.filter(match=match, innings_number=1).first()
    inn2 = Innings.objects.filter(match=match, innings_number=2).first()

    if not inn1:
        return None, "Match data not available."

    # ── Knockout vs League detection ──
    is_knockout = False
    knockout_stage_name = ''
    knockout_context_block = ''
    try:
        km = match.knockout_match
        is_knockout = True
        knockout_stage_name = km.stage.get_stage_display()
        STAGE_STAKES = {
            'F':   ('FINAL', 'This is the TOURNAMENT FINAL — the highest-stakes match. One team lifts the trophy, the other goes home. Every decision, every run, every wicket carries enormous weight. Analyse with the gravitas of a championship decider.'),
            'SF':  ('SEMI-FINAL', 'This is a SEMI-FINAL — a knockout eliminator. Both teams are one loss away from elimination. Pressure is intense, margins are thin, and tactical nous separates finalists from also-rans.'),
            'QF':  ('QUARTER-FINAL', 'This is a QUARTER-FINAL knockout match. The top 8 have qualified and elimination is on the line. Teams play with heightened urgency compared to league fixtures.'),
            'PQF': ('PRE QUARTER-FINAL', 'This is a PRE QUARTER-FINAL knockout match. Teams that scraped through the league stage now fight for survival — desperate cricket, high adrenaline, nothing to lose.'),
        }
        stage_code = km.stage.stage
        if stage_code in STAGE_STAKES:
            label, context = STAGE_STAKES[stage_code]
            knockout_context_block = f"""
⚠️  MATCH TYPE: {label} (KNOCKOUT — ELIMINATION MATCH)
{context}

KEY KNOCKOUT ANALYSIS REQUIREMENTS:
- Highlight how knockout pressure visibly affected batting/bowling decisions
- Note any conservative or overly-aggressive play driven by elimination fear
- Assess who handled the big-occasion pressure better
- Compare how each team's approach differed from typical league-stage cricket
- Identify the "knockout mentality" moments — clutch performances under do-or-die pressure
- The losing team is ELIMINATED — reflect this finality in your analysis tone
"""
        else:
            knockout_context_block = f"\n⚠️  MATCH TYPE: KNOCKOUT MATCH ({knockout_stage_name})\nThis is an elimination match — the loser exits the tournament.\n"
    except Exception:
        is_knockout = False
        knockout_context_block = "\nMATCH TYPE: LEAGUE MATCH\nThis is a points-table league fixture. Teams accumulate wins for knockout qualification. Analyse within the context of the broader tournament standings.\n"

    def batting_lines(innings):
        rows = BattingScorecard.objects.filter(innings=innings).order_by('batting_position').select_related('batsman')
        lines = []
        for r in rows:
            status = r.status
            if status == 'DNB':
                lines.append(f"  {r.batsman.player_name}: Did Not Bat")
            else:
                out_str = "not out" if status == 'NOT_OUT' else (r.dismissal_info or "out")
                lines.append(
                    f"  {r.batsman.player_name}: {r.runs} ({r.balls_faced}b) "
                    f"4s:{r.fours} 6s:{r.sixes} SR:{r.strike_rate} [{out_str}]"
                )
        return "\n".join(lines) if lines else "  (no batting data)"

    def bowling_lines(innings):
        rows = BowlingScorecard.objects.filter(innings=innings).select_related('bowler')
        from scoring.models import HatTrick as HT
        lines = []
        for r in rows:
            ht_flag = ' 🎩HAT-TRICK' if HT.objects.filter(innings=innings, bowler=r.bowler).exists() else ''
            lines.append(
                f"  {r.bowler.player_name}: {r.overs_bowled}ov {r.runs_given}R "
                f"{r.wickets}W Wd:{r.wides} Nb:{r.no_balls} Eco:{r.economy}{ht_flag}"
            )
        return "\n".join(lines) if lines else "  (no bowling data)"

    def over_summary(innings):
        overs = Over.objects.filter(innings=innings).order_by('over_number')
        lines = []
        for ov in overs:
            balls = Ball.objects.filter(over=ov).order_by('ball_number')
            ball_str = " ".join(
                f"{'W' if b.is_wicket else ''}{b.total_runs}" for b in balls
            )
            lines.append(f"  Over {ov.over_number}: [{ball_str}] — {ov.runs_in_over}R {ov.wickets_in_over}W")
        return "\n".join(lines) if lines else "  (no over data)"

    # Powerplay (first 6 overs)
    def powerplay_score(innings):
        pp_overs = Over.objects.filter(innings=innings, over_number__lte=6)
        runs = sum(o.runs_in_over for o in pp_overs)
        wkts = sum(o.wickets_in_over for o in pp_overs)
        return f"{runs}/{wkts}"

    # Result
    winner_name = "Unknown"
    result_summary = "In progress"
    try:
        mr = match.result
        winner_name = mr.winner.team_name if mr.winner else "Tie/No Result"
        result_summary = mr.result_summary
    except Exception:
        pass

    # MOM
    mom_name = "N/A"
    try:
        mom_name = match.man_of_the_match.player.player_name
    except Exception:
        pass

    prompt = f"""
You are a professional international cricket analyst.

Analyze the following completed match and generate a COMPLETE PROFESSIONAL MATCH ANALYSIS.
{knockout_context_block}
=== MATCH DETAILS ===
Tournament: {tournament.tournament_name}
Match Type: {"🏆 KNOCKOUT — " + knockout_stage_name if is_knockout else "📋 League Match"}
Format: {tournament.get_tournament_type_display()} — {tournament.number_of_overs} overs
Venue: {match.venue}
Date: {match.match_date}
Teams: {match.team1.team_name} vs {match.team2.team_name}

=== TOSS ===
"""
    try:
        ms = match.match_start
        prompt += f"Toss Winner: {ms.toss_winner.team_name}, chose to {ms.decision}\n"
        prompt += f"Batting First: {ms.batting_team.team_name}\n"
    except Exception:
        prompt += "Toss data not available\n"

    prompt += f"""
=== 1ST INNINGS — {inn1.batting_team.team_name} ===
Total: {inn1.total_runs}/{inn1.total_wickets} ({inn1.overs_completed} overs)
Extras: {inn1.extras}
Powerplay (1-6): {powerplay_score(inn1)}

BATTING:
{batting_lines(inn1)}

BOWLING:
{bowling_lines(inn1)}

OVER BY OVER:
{over_summary(inn1)}
"""

    if inn2:
        prompt += f"""
=== 2ND INNINGS — {inn2.batting_team.team_name} ===
Target: {inn2.target}
Total: {inn2.total_runs}/{inn2.total_wickets} ({inn2.overs_completed} overs)
Extras: {inn2.extras}
Powerplay (1-6): {powerplay_score(inn2)}

BATTING:
{batting_lines(inn2)}

BOWLING:
{bowling_lines(inn2)}

OVER BY OVER:
{over_summary(inn2)}
"""

    # Collect hat-tricks for this match
    from scoring.models import HatTrick as _PromptHT
    _prompt_hts = _PromptHT.objects.filter(match=match).select_related('bowler','victim1','victim2','victim3')
    hat_trick_prompt_block = ''
    if _prompt_hts.exists():
        ht_lines = [f"  {ht.bowler.player_name} dismissed {ht.victims_display()}" for ht in _prompt_hts]
        hat_trick_prompt_block = '\n=== HAT-TRICKS ===\n' + '\n'.join(ht_lines) + '\n'

    prompt += hat_trick_prompt_block
    prompt += f"""
=== RESULT ===
Winner: {winner_name}
Summary: {result_summary}
Player of the Match: {mom_name}

=== YOUR TASK ===
Generate a COMPLETE PROFESSIONAL MATCH ANALYSIS with ALL sections below.
Format EXACTLY as shown using these section markers (I will parse them):

[HEADLINE]
One punchy sports journalist headline

[SUMMARY]
3-4 paragraph match summary covering how the game unfolded, turning points, momentum shifts

[TOSS_IMPACT]
Analysis of toss decision, pitch behavior, whether it helped

[BATTING_ANALYSIS]
Deep analysis of both teams' batting — top order, middle order, finishing, strike rates, boundary hitting, dot ball pressure, impact vs cosmetic innings

[BOWLING_ANALYSIS]
Deep analysis of both teams' bowling — powerplay control, middle overs, death overs, economy vs wickets, best spell

[PHASES]
Powerplay comparison, middle overs battle, death overs breakdown, key partnerships

[TACTICAL]
Captaincy decisions, bowling changes, field placements, use of all-rounders, tactical mistakes

[METRICS]
Run rate progression, pressure index moments, win probability swings, required rate comparison, dot ball %, boundary frequency

[PLAYER_RATINGS]
Rate top 5 impactful players on 1-10 scale with 2-line explanation each

[WHY_WON]
5 specific cricketing reasons the winning team won (numbered list)

[WHY_LOST]
5 specific cricketing reasons the losing team lost (numbered list)

[IMPROVEMENTS]
{"What each team must work on — keeping in mind this was a knockout match and the losing team is now ELIMINATED. Focus on what the winning team must sharpen before their NEXT KNOCKOUT round." if is_knockout else "What each team must improve before their next match."}

{"[KNOCKOUT_VERDICT]" + chr(10) + "A special section ONLY for knockout matches: How did knockout pressure shape this match? Who rose to the occasion? Who crumbled? Was this a classic knockout thriller or a dominant display? What does this result mean for the tournament?" if is_knockout else ""}

Be professional, tactical, data-driven. No generic commentary. Like a top TV cricket analyst.
{"This was a " + knockout_stage_name + " — write with the weight and drama that a knockout elimination deserves." if is_knockout else ""}
"""
    return match, prompt



# ─────────────────────────────────────────────────────────────
#  ML ANALYSIS VIEWS
# ─────────────────────────────────────────────────────────────

@require_plan('pro', 'pro_plus')
def match_analysis_view(request, match_id):
    match = get_object_or_404(CreateMatch, id=match_id)
    inn2 = Innings.objects.filter(match=match, innings_number=2).first()
    if not inn2 or inn2.status != 'COMPLETED':
        messages.error(request, "Analysis is available only after match completion.")
        return redirect('match_scorecard', match_id=match_id)
    return render(request, 'match_analysis.html', {'match': match})


@require_plan('pro', 'pro_plus')
def match_analysis_api(request, match_id):
    """Returns structured data + ML charts as base64 images. Always returns JSON, never crashes to HTML."""
    try:
        from strikezone.ml_analysis import generate_all_charts
        ML_AVAILABLE = True
        ML_ERROR = ''
    except ImportError as e:
        ML_AVAILABLE = False
        ML_ERROR = str(e)
    except Exception as e:
        ML_AVAILABLE = False
        ML_ERROR = str(e)

    match = get_object_or_404(CreateMatch, id=match_id)
    tournament = match.tournament

    inn1 = Innings.objects.filter(match=match, innings_number=1).first()
    inn2 = Innings.objects.filter(match=match, innings_number=2).first()
    if not inn1:
        return JsonResponse({'error': 'Match data not available.'}, status=400)

    def get_over_data(innings):
        overs = Over.objects.filter(innings=innings).order_by('over_number')
        result, cumul = [], 0
        for ov in overs:
            cumul += ov.runs_in_over
            result.append({'over': ov.over_number, 'runs': ov.runs_in_over,
                            'wickets': ov.wickets_in_over, 'cumulative': cumul})
        return result

    def get_balls_detail(innings):
        """Ball-level data for ML charts — uses real shot_direction from DB."""
        ZONE_MAP = {
            'FINE_LEG': 0, 'SQUARE_LEG': 1, 'MID_WICKET': 2, 'MID_ON': 3,
            'STRAIGHT': 4, 'LONG_ON': 5, 'LONG_OFF': 6, 'MID_OFF': 7,
            'COVER': 8, 'POINT': 9, 'THIRD_MAN': 10, 'FINE_LEG_DEEP': 11,
        }
        balls = Ball.objects.filter(over__innings=innings).order_by(
            'over__over_number', 'ball_number')
        result, cumul = [], 0
        for b in balls:
            cumul += b.total_runs
            zone = ZONE_MAP.get(b.shot_direction) if b.shot_direction else None
            result.append({
                'runs_off_bat': b.runs_off_bat,
                'total_runs': b.total_runs,
                'is_wicket': b.is_wicket,
                'is_legal': b.is_legal_ball,
                'runs': b.runs_off_bat,
                'zone': zone,
                'has_direction': zone is not None,
                'cumulative_runs': cumul,
            })
        return result

    def get_batting(innings):
        rows = BattingScorecard.objects.filter(innings=innings).order_by(
            'batting_position').select_related('batsman')
        return [{'name': r.batsman.player_name, 'runs': r.runs, 'balls': r.balls_faced,
                 'fours': r.fours, 'sixes': r.sixes, 'sr': float(r.strike_rate),
                 'status': r.status, 'dismissal': r.dismissal_info or ''}
                for r in rows if r.status != 'DNB']

    def get_bowling(innings):
        rows = BowlingScorecard.objects.filter(innings=innings).select_related('bowler')
        return [{'name': r.bowler.player_name, 'overs': float(r.overs_bowled),
                 'runs': r.runs_given, 'wickets': r.wickets, 'wides': r.wides,
                 'no_balls': r.no_balls, 'economy': float(r.economy)}
                for r in rows]

    def phase_stats(innings):
        overs = Over.objects.filter(innings=innings).order_by('over_number')
        mx = tournament.number_of_overs
        pp, mid, death = {'runs':0,'wickets':0}, {'runs':0,'wickets':0}, {'runs':0,'wickets':0}
        for ov in overs:
            n = ov.over_number
            if n <= min(6, mx): pp['runs']+=ov.runs_in_over; pp['wickets']+=ov.wickets_in_over
            elif n <= min(int(mx*.75), mx): mid['runs']+=ov.runs_in_over; mid['wickets']+=ov.wickets_in_over
            else: death['runs']+=ov.runs_in_over; death['wickets']+=ov.wickets_in_over
        return {'powerplay': pp, 'middle': mid, 'death': death}

    def dot_pct(innings):
        balls = Ball.objects.filter(over__innings=innings, is_legal_ball=True)
        t = balls.count()
        return round((balls.filter(runs_off_bat=0, extra_runs=0).count()/t*100) if t else 0, 1)

    def boundary_pct(innings):
        balls = Ball.objects.filter(over__innings=innings, is_legal_ball=True)
        t = balls.count()
        return round((balls.filter(runs_off_bat__gte=4).count()/t*100) if t else 0, 1)

    winner_name = result_text = ''
    try: mr = match.result; winner_name = mr.winner.team_name if mr.winner else 'Tie'; result_text = mr.result_summary
    except: pass
    mom_name = ''
    try: mom_name = match.man_of_the_match.player.player_name
    except: pass

    # ── Knockout vs League detection ──
    is_knockout = False
    knockout_stage_name = 'League Match'
    knockout_label = ''
    try:
        km = match.knockout_match
        is_knockout = True
        knockout_stage_name = km.stage.get_stage_display()
        STAGE_EMOJIS = {'F': '🏆 FINAL', 'SF': '🥊 SEMI-FINAL', 'QF': '⚔️ QUARTER-FINAL', 'PQF': '🔥 PRE QUARTER-FINAL'}
        knockout_label = STAGE_EMOJIS.get(km.stage.stage, f'🏟️ {knockout_stage_name}')
    except Exception:
        knockout_label = '📋 League Match'

    data = {
        'match': {
            'team1': match.team1.team_name, 'team2': match.team2.team_name,
            # These are the BATTING teams — always correct regardless of toss
            'bat_first': inn1.batting_team.team_name,
            'bat_second': inn2.batting_team.team_name if inn2 else '',
            'venue': match.venue, 'date': str(match.match_date),
            'tournament': tournament.tournament_name,
            'format': f"{tournament.get_tournament_type_display()} {tournament.number_of_overs}ov",
            'overs': tournament.number_of_overs,
            'winner': winner_name, 'result': result_text, 'mom': mom_name,
            'is_knockout': is_knockout,
            'knockout_stage': knockout_stage_name,
            'knockout_label': knockout_label,
        },
        'innings1': {
            'team': inn1.batting_team.team_name,
            'total': inn1.total_runs, 'wickets': inn1.total_wickets,
            'overs': inn1.overs_completed, 'extras': inn1.extras,
            'dot_pct': dot_pct(inn1), 'boundary_pct': boundary_pct(inn1),
            'overs_data': get_over_data(inn1),
            'batting': get_batting(inn1), 'bowling': get_bowling(inn1),
            'balls_detail': get_balls_detail(inn1),
            'phases': phase_stats(inn1),
        },
    }
    if inn2:
        data['innings2'] = {
            'team': inn2.batting_team.team_name,
            'total': inn2.total_runs, 'wickets': inn2.total_wickets,
            'overs': inn2.overs_completed, 'extras': inn2.extras,
            'target': inn2.target,
            'dot_pct': dot_pct(inn2), 'boundary_pct': boundary_pct(inn2),
            'overs_data': get_over_data(inn2),
            'batting': get_batting(inn2), 'bowling': get_bowling(inn2),
            'balls_detail': get_balls_detail(inn2),
            'phases': phase_stats(inn2),
        }

    # ── GROQ AI insights ──
    try:
        # Use innings teams as authoritative labels — not match.team1/team2
        # which may differ from who batted first
        team_bat1 = data['innings1']['team']   # team that batted 1st
        team_bat2 = data.get('innings2', {}).get('team', '') if inn2 else ''

        # Collect hat-tricks for this match
        from scoring.models import HatTrick as _HT
        _ht_inn1 = _HT.objects.filter(innings__match=match).select_related('bowler','victim1','victim2','victim3')
        hat_trick_note = ''
        if _ht_inn1.exists():
            ht_lines = [f"{ht.bowler.player_name} (dismissed {ht.victims_display()})" for ht in _ht_inn1]
            hat_trick_note = '\nHat-Tricks: ' + '; '.join(ht_lines)

        b1  = " | ".join([f"{b['name']} {b['runs']}({b['balls']}b)" for b in data['innings1']['batting'][:6]])
        b2  = " | ".join([f"{b['name']} {b['runs']}({b['balls']}b)" for b in data.get('innings2',{}).get('batting',[])[:6]])
        # bw1 = bowlers from innings1 (team_bat2 bowled at team_bat1)
        # bw2 = bowlers from innings2 (team_bat1 bowled at team_bat2)
        bw1 = " | ".join([f"{b['name']} {b['overs']}ov {b['runs']}R {b['wickets']}W" for b in data['innings1']['bowling'][:5]])
        bw2 = " | ".join([f"{b['name']} {b['overs']}ov {b['runs']}R {b['wickets']}W" for b in data.get('innings2',{}).get('bowling',[])[:5]])
        inn2_line = f"{team_bat2}: {data['innings2']['total']}/{data['innings2']['wickets']} chasing {data['innings2']['target']}" if inn2 else ""
        knockout_ai_note = (
            f"⚠️ THIS IS A {knockout_stage_name.upper()} — KNOCKOUT ELIMINATION MATCH. "
            f"The loser is eliminated from the tournament. Adjust your analysis to reflect the high-stakes, do-or-die nature. "
            f"Note pressure moments, clutch performances, and how knockout urgency shaped the game."
            if is_knockout else
            "This is a league match. Analyse normally within tournament standings context."
        )
        ai_prompt = f"""Cricket: {team_bat1} vs {team_bat2 or 'TBD'}
Match Type: {knockout_label}
{knockout_ai_note}
1st Innings — {team_bat1}: {data['innings1']['total']}/{data['innings1']['wickets']} ({data['innings1']['overs']} ov)
{inn2_line}
Result: {result_text}
Batting — {team_bat1}: {b1}
Batting — {team_bat2}: {b2}
Bowling — {team_bat2} (bowled in inn1): {bw1}
Bowling — {team_bat1} (bowled in inn2): {bw2}
Dot%: {data['innings1']['dot_pct']}% (inn1) vs {data.get('innings2',{}).get('dot_pct',0)}% (inn2)
Boundary%: {data['innings1']['boundary_pct']}% (inn1) vs {data.get('innings2',{}).get('boundary_pct',0)}% (inn2)
MOM: {mom_name}{hat_trick_note}
IMPORTANT: In the JSON keys below, "team1" means "{team_bat1}" (batted 1st), "team2" means "{team_bat2}" (batted 2nd).
Reply ONLY valid JSON no markdown:
{{"headline":"punchy headline that mentions the match stage if knockout","verdict":"2 sentence verdict reflecting knockout stakes if applicable","turning_point":"1 sentence","player_of_match_reason":"why {mom_name} won","team1_batting_insight":"batting insight for {team_bat1}","team2_batting_insight":"batting insight for {team_bat2}","team1_bowling_insight":"bowling insight for {team_bat1}","team2_bowling_insight":"bowling insight for {team_bat2}","best_spell":"name+figures","top_partnership":"1 line","winner_reason":"why won","loser_reason":"why lost","knockout_pressure_note":{"was the match affected by knockout pressure? 1-2 sentences. Empty string if league match." if is_knockout else '""'},"player_ratings":[{{"name":"","score":9.0,"role":"","note":""}}]}}"""
        _groq = GroqClient(api_key=os.environ.get("GROQ_API_KEY", ""))
        resp = _groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role":"system","content":"Cricket analyst. Valid JSON only."},
                      {"role":"user","content":ai_prompt}],
            max_tokens=700, temperature=0.6)
        raw = resp.choices[0].message.content.strip().replace('```json','').replace('```','').strip()
        data['ai'] = json.loads(raw)
    except Exception:
        data['ai'] = {
            'headline': f"{winner_name} win{' in ' + knockout_stage_name if is_knockout else ''}", 'verdict': result_text,
            'turning_point': 'Key phase decided the match.',
            'player_of_match_reason': f'{mom_name} was outstanding.',
            'team1_batting_insight': 'Solid batting.', 'team2_batting_insight': 'Competitive innings.',
            'team1_bowling_insight': 'Disciplined bowling.', 'team2_bowling_insight': 'Bowlers fought hard.',
            'best_spell': 'Best spell of the match.', 'top_partnership': 'Key partnership.',
            'winner_reason': 'Better all-round.', 'loser_reason': 'Could not match target.',
            'knockout_pressure_note': f'This was a {knockout_stage_name} — knockout pressure was a key factor.' if is_knockout else '',
            'player_ratings': [],
        }

    # ── ML CHARTS (generated server-side with matplotlib + sklearn) ──
    if ML_AVAILABLE:
        try:
            data['charts'] = generate_all_charts(data)
        except Exception as e:
            data['charts'] = {'error': str(e), 'install_hint': 'pip install matplotlib scikit-learn numpy pandas'}
    else:
        data['charts'] = {
            'error': f'ML libraries not installed: {ML_ERROR}',
            'install_hint': 'Run: pip install matplotlib scikit-learn numpy pandas'
        }

    return JsonResponse(data)