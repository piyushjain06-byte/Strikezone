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

def _is_tournament_complete(tournament):
    """
    Returns True if the tournament has concluded:
    - Has a Final knockout match that is completed, OR
    - Was manually force-completed via the 'Complete Tournament' button
      (stored as tournament.is_force_completed flag)

    NOTE: league-only tournaments do NOT auto-complete when all league
    matches finish — the organiser must click 'Complete Tournament'.
    """
    from knockout.models import KnockoutStage, KnockoutMatch

    # Manual force-complete overrides everything
    if getattr(tournament, 'is_force_completed', False):
        return True

    # Has a Final stage → complete only when Final is done
    final_stage = KnockoutStage.objects.filter(tournament=tournament, stage='F').first()
    if final_stage:
        final_matches = KnockoutMatch.objects.filter(stage=final_stage)
        if final_matches.exists():
            return all(m.is_completed for m in final_matches)
        return False

    # No knockout exists → never auto-complete (wait for manual button)
    return False


def _collect_player_stats(tournament):
    """
    Collect per-player aggregate batting + bowling stats across all
    completed matches in the tournament.
    Returns dict: {player_id: {...stats...}}
    """
    all_matches = CreateMatch.objects.filter(tournament=tournament)
    player_stats = {}  # pid -> dict

    def ensure(pid, player):
        if pid not in player_stats:
            player_stats[pid] = {
                'player': player,
                'matches': set(),
                'innings_batted': 0,
                'runs': 0,
                'balls_faced': 0,
                'fours': 0,
                'sixes': 0,
                'not_outs': 0,
                'highest': 0,
                'wickets': 0,
                'runs_given': 0,
                'balls_bowled': 0,
                'wides': 0,
                'no_balls': 0,
                'best_w': 0,
                'best_r': 9999,
            }

    for match in all_matches:
        inn2 = Innings.objects.filter(match=match, innings_number=2).first()
        if not inn2 or inn2.status != 'COMPLETED':
            continue  # Only completed matches

        innings_list = Innings.objects.filter(match=match)
        for innings in innings_list:
            # Batting
            for bsc in BattingScorecard.objects.filter(innings=innings).select_related('batsman'):
                pid = bsc.batsman_id
                ensure(pid, bsc.batsman)
                player_stats[pid]['matches'].add(match.id)
                if bsc.status != 'DNB':
                    player_stats[pid]['innings_batted'] += 1
                    player_stats[pid]['runs'] += bsc.runs
                    player_stats[pid]['balls_faced'] += bsc.balls_faced
                    player_stats[pid]['fours'] += bsc.fours
                    player_stats[pid]['sixes'] += bsc.sixes
                    if bsc.status == 'NOT_OUT':
                        player_stats[pid]['not_outs'] += 1
                    if bsc.runs > player_stats[pid]['highest']:
                        player_stats[pid]['highest'] = bsc.runs

            # Bowling
            for bwsc in BowlingScorecard.objects.filter(innings=innings).select_related('bowler'):
                pid = bwsc.bowler_id
                ensure(pid, bwsc.bowler)
                player_stats[pid]['matches'].add(match.id)
                player_stats[pid]['wickets'] += bwsc.wickets
                player_stats[pid]['runs_given'] += bwsc.runs_given
                player_stats[pid]['wides'] += bwsc.wides
                player_stats[pid]['no_balls'] += bwsc.no_balls
                # Convert overs_bowled (X.Y) to balls
                ov = float(bwsc.overs_bowled)
                full = int(ov)
                extra = round((ov - full) * 10)
                player_stats[pid]['balls_bowled'] += full * 6 + extra
                # Best bowling
                if (bwsc.wickets > player_stats[pid]['best_w'] or
                   (bwsc.wickets == player_stats[pid]['best_w'] and bwsc.runs_given < player_stats[pid]['best_r'])):
                    player_stats[pid]['best_w'] = bwsc.wickets
                    player_stats[pid]['best_r'] = bwsc.runs_given

    # Compute derived stats
    for pid, s in player_stats.items():
        s['matches_played'] = len(s['matches'])
        dismissals = s['innings_batted'] - s['not_outs']
        s['batting_avg'] = round(s['runs'] / dismissals, 2) if dismissals > 0 else float(s['runs'])
        s['batting_sr']  = round((s['runs'] / s['balls_faced']) * 100, 2) if s['balls_faced'] > 0 else 0
        s['bowling_avg'] = round(s['runs_given'] / s['wickets'], 2) if s['wickets'] > 0 else 0
        s['bowling_econ'] = round((s['runs_given'] * 6) / s['balls_bowled'], 2) if s['balls_bowled'] > 0 else 0
        if s['best_r'] == 9999:
            s['best_r'] = 0

    return player_stats


def _best_batsman_score(s, tournament_avg_sr):
    """
    Best Batsman Index (BBI):
      = (Runs / max_runs_in_tournament * 50)       -- volume: 50 pts max
      + (batting_avg / max_avg * 30)                -- consistency: 30 pts max
      + SR bonus: +5 for every 25% above tournament avg SR
      + (matches_played / total_matches * 20)       -- presence: 20 pts max
    Minimum 2 innings to qualify.
    """
    if s['innings_batted'] < 2:
        return None
    # Will be normalised against tournament peers in the caller
    return {
        'runs': s['runs'],
        'avg': s['batting_avg'],
        'sr': s['batting_sr'],
        'hs': s['highest'],
        'innings': s['innings_batted'],
        'matches': s['matches_played'],
        'tournament_avg_sr': tournament_avg_sr,
    }


def _best_bowler_score(s):
    """
    Best Bowler Index (BBI):
      = (Wickets / max_wickets * 50)               -- wickets: 50 pts max
      + Economy bonus: (10 - economy) * 3 (capped at 0)
      + (1 / bowling_avg * 200) if wickets >= 2    -- avg quality
      + (matches_played presence)
    Minimum 2 wickets to qualify.
    """
    if s['wickets'] < 2:
        return None
    return {
        'wickets': s['wickets'],
        'avg': s['bowling_avg'],
        'econ': s['bowling_econ'],
        'best_w': s['best_w'],
        'best_r': s['best_r'],
        'matches': s['matches_played'],
        'balls': s['balls_bowled'],
    }


def award_tournament_awards(tournament_id):
    """
    Main entry point. Safe to call multiple times (idempotent).
    Awards: Best Batsman, Best Bowler, Man of the Tournament.
    """
    tournament = TournamentDetails.objects.filter(id=tournament_id).first()
    if not tournament:
        return

    # If awards already exist, check if MOT is from champion team.
    # If not, delete all and recompute with the corrected formula.
    existing = TournamentAward.objects.filter(tournament=tournament)
    if existing.count() >= 3:
        mot_award = existing.filter(award_type='MOT').first()
        if mot_award:
            from knockout.models import KnockoutStage, KnockoutMatch as _KM
            _final = KnockoutStage.objects.filter(tournament=tournament, stage='F').first()
            if _final:
                _fkm = _KM.objects.filter(stage=_final, is_completed=True).first()
                if _fkm and _fkm.winner:
                    from teams.models import TournamentTeam as _TT, TournamentRoster as _TR
                    _ctt = _TT.objects.filter(tournament=tournament, team=_fkm.winner).first()
                    if _ctt:
                        _cpids = set(_TR.objects.filter(tournament_team=_ctt).values_list('player_id', flat=True))
                        if mot_award.player_id in _cpids:
                            return  # Already correct — MOT from champion team
                        else:
                            existing.delete()  # Wrong MOT — delete and recompute
                    else:
                        return
                else:
                    return
            else:
                return  # No final yet
        else:
            return

    if not _is_tournament_complete(tournament):
        return

    pstats = _collect_player_stats(tournament)
    if not pstats:
        return

    # ── Tournament-wide averages for normalisation ──
    total_runs_all  = sum(s['runs'] for s in pstats.values())
    total_balls_all = sum(s['balls_faced'] for s in pstats.values())
    tournament_avg_sr = (total_runs_all / total_balls_all * 100) if total_balls_all > 0 else 100

    max_runs    = max((s['runs']    for s in pstats.values()), default=1) or 1
    max_avg_bat = max((s['batting_avg'] for s in pstats.values()), default=1) or 1
    max_wickets = max((s['wickets'] for s in pstats.values()), default=1) or 1
    total_matches = max((s['matches_played'] for s in pstats.values()), default=1) or 1

    # ── BEST BATSMAN ──
    best_bat_pid, best_bat_val = None, -1
    for pid, s in pstats.items():
        if s['innings_batted'] < 2:
            continue
        vol_pts    = (s['runs'] / max_runs) * 50
        avg_pts    = (s['batting_avg'] / max_avg_bat) * 30
        sr_diff    = ((s['batting_sr'] - tournament_avg_sr) / tournament_avg_sr * 100) if tournament_avg_sr > 0 else 0
        sr_bonus   = max(0, (sr_diff / 25) * 5)
        pres_pts   = (s['matches_played'] / total_matches) * 20
        total = vol_pts + avg_pts + sr_bonus + pres_pts
        if total > best_bat_val:
            best_bat_val = total
            best_bat_pid = pid

    # ── BEST BOWLER ──
    best_bowl_pid, best_bowl_val = None, -1
    for pid, s in pstats.items():
        if s['wickets'] < 2:
            continue
        wkt_pts  = (s['wickets'] / max_wickets) * 50
        econ_pts = max(0, (10 - s['bowling_econ']) * 3)
        avg_pts  = (1 / s['bowling_avg']) * 200 if s['bowling_avg'] > 0 else 0
        avg_pts  = min(avg_pts, 30)   # cap at 30
        pres_pts = (s['matches_played'] / total_matches) * 20
        total = wkt_pts + econ_pts + avg_pts + pres_pts
        if total > best_bowl_val:
            best_bowl_val = total
            best_bowl_pid = pid

    # ── MAN OF THE TOURNAMENT ──
    # RULE: MOT is ALWAYS from the champion (winning) team only.
    # Among champion team players, pick the best performer.
    mot_pid, mot_val = None, -1
    mom_counts = {}
    from matches.models import ManOfTheMatch
    for mom in ManOfTheMatch.objects.filter(match__tournament=tournament):
        mom_counts[mom.player_id] = mom_counts.get(mom.player_id, 0) + 1

    max_mom = max(mom_counts.values()) if mom_counts else 1

    # ── Find champion team ──
    champion_team = None
    from knockout.models import KnockoutStage, KnockoutMatch as KM
    final_stage = KnockoutStage.objects.filter(tournament=tournament, stage='F').first()
    if final_stage:
        final_km = KM.objects.filter(stage=final_stage, is_completed=True).first()
        if final_km and final_km.winner:
            champion_team = final_km.winner
    # Fallback for round-robin only tournaments — team with most wins
    if not champion_team:
        from collections import Counter
        win_counts = Counter()
        for match in CreateMatch.objects.filter(tournament=tournament):
            try:
                mr = match.result
                if mr.winner:
                    win_counts[mr.winner_id] += 1
            except Exception:
                pass
        if win_counts:
            top_team_id = win_counts.most_common(1)[0][0]
            from teams.models import TeamDetails as TD
            champion_team = TD.objects.filter(id=top_team_id).first()

    # ── Get champion team player IDs ──
    champion_player_ids = set()
    if champion_team:
        from teams.models import TournamentTeam as TT, TournamentRoster as TR
        champ_tt = TT.objects.filter(tournament=tournament, team=champion_team).first()
        if champ_tt:
            champion_player_ids = set(
                TR.objects.filter(tournament_team=champ_tt)
                .values_list('player_id', flat=True)
            )

    # ── Score ONLY champion team players ──
    for pid, s in pstats.items():
        # Skip anyone NOT on the champion team
        if pid not in champion_player_ids:
            continue

        # Batting component (0–50)
        bat_component = (s['runs'] / max_runs) * 30 + (s['batting_avg'] / max_avg_bat) * 20

        # Bowling component (0–50)
        if s['wickets'] > 0:
            bowl_component = (
                (s['wickets'] / max_wickets) * 30
                + min((1 / s['bowling_avg']) * 100 if s['bowling_avg'] > 0 else 0, 20)
            )
        else:
            bowl_component = 0

        # MOM bonus (0–15)
        mom_bonus = (mom_counts.get(pid, 0) / max_mom) * 15

        # Presence (0–5)
        pres_pts = (s['matches_played'] / total_matches) * 5

        total = bat_component + bowl_component + mom_bonus + pres_pts
        if total > mot_val:
            mot_val = total
            mot_pid = pid

    # ── Save awards ──
    def save_award(award_type, pid, score_val):
        if not pid:
            return
        s = pstats[pid]
        TournamentAward.objects.update_or_create(
            tournament=tournament,
            award_type=award_type,
            defaults={
                'player_id': pid,
                'score': round(score_val, 2),
                'total_runs': s['runs'],
                'total_balls_faced': s['balls_faced'],
                'batting_avg': round(float(s['batting_avg']), 2),
                'batting_sr': round(float(s['batting_sr']), 2),
                'highest_score': s['highest'],
                'total_wickets': s['wickets'],
                'bowling_avg': round(float(s['bowling_avg']), 2),
                'bowling_economy': round(float(s['bowling_econ']), 2),
                'best_bowling': f"{s['best_w']}/{s['best_r']}",
                'matches_played': s['matches_played'],
            }
        )

    save_award('BBAT', best_bat_pid, best_bat_val)
    save_award('BBOL', best_bowl_pid, best_bowl_val)
    save_award('MOT',  mot_pid, mot_val)


# ══════════════════════════════════════════════
# UNIVERSAL IMPACT INDEX — MAN OF THE MATCH ENGINE
# ══════════════════════════════════════════════

def calculate_uii(match):
    """
    Calculates the Universal Impact Index (UII) for every player
    who participated in the match and returns the best player.

    Formula:
      Batting  = (player_runs / team_total_runs) * 100
               + SR_bonus  (+5 per 25% above match_avg_sr)
      Bowling  = (player_wickets / total_wickets_fell) * 100
               + pressure  = (dot_balls / balls_bowled) * 30
      Finisher = +15 if NOT OUT in a successful run chase (2nd innings winner)
      Partner  = +10 if a wicket taken was a batsman who scored >= 25% of team runs
      Multiplier: winner * 1.1,  loser * 1.0
    """
    inn1 = Innings.objects.filter(match=match, innings_number=1).first()
    inn2 = Innings.objects.filter(match=match, innings_number=2).first()
    if not inn1 or not inn2:
        return None

    # --- Match-level aggregates ---
    total_wickets_fell = inn1.total_wickets + inn2.total_wickets
    if total_wickets_fell == 0:
        total_wickets_fell = 1  # avoid divide-by-zero

    # Match average SR (total runs off bat / total legal balls)
    total_runs_all = inn1.total_runs + inn2.total_runs
    total_legal_balls = inn1.total_balls + inn2.total_balls
    match_avg_sr = (total_runs_all / total_legal_balls * 100) if total_legal_balls > 0 else 100

    # Result
    try:
        result = match.result
        winner_team = result.winner
    except Exception:
        winner_team = None

    # Determine if 2nd innings was a successful run chase
    chase_success = (inn2.total_runs >= inn1.total_runs and winner_team == inn2.batting_team)

    # --- Collect all player IDs who participated ---
    all_player_ids = set()
    for sc in BattingScorecard.objects.filter(innings__match=match).values_list('batsman_id', flat=True):
        all_player_ids.add(sc)
    for sc in BowlingScorecard.objects.filter(innings__match=match).values_list('bowler_id', flat=True):
        all_player_ids.add(sc)

    best_player = None
    best_uii = -999

    for pid in all_player_ids:
        player = PlayerDetails.objects.filter(id=pid).first()
        if not player:
            continue

        uii = 0.0

        # ── Which innings did this player bat/bowl in? ──
        bat_sc = BattingScorecard.objects.filter(innings__match=match, batsman=player).first()
        bowl_sc = BowlingScorecard.objects.filter(innings__match=match, bowler=player).first()

        # Figure out which team this player is on
        player_team = None
        if bat_sc:
            player_team = bat_sc.innings.batting_team
        elif bowl_sc:
            player_team = bowl_sc.innings.bowling_team

        # ── 1. BATTING ──
        if bat_sc and bat_sc.status != 'DNB':
            team_total = bat_sc.innings.total_runs
            if team_total > 0:
                bat_pct = (bat_sc.runs / team_total) * 100
            else:
                bat_pct = 0

            # SR bonus
            if bat_sc.balls_faced > 0:
                player_sr = (bat_sc.runs / bat_sc.balls_faced) * 100
                sr_diff_pct = ((player_sr - match_avg_sr) / match_avg_sr) * 100 if match_avg_sr > 0 else 0
                sr_bonus = max(0, (sr_diff_pct / 25) * 5)
            else:
                sr_bonus = 0

            uii += bat_pct + sr_bonus

        # ── 2. BOWLING ──
        if bowl_sc and bowl_sc.wickets > 0 or (bowl_sc and float(bowl_sc.overs_bowled) > 0):
            if bowl_sc:
                wicket_pct = (bowl_sc.wickets / total_wickets_fell) * 100

                # Dot ball pressure factor
                overs_val = float(bowl_sc.overs_bowled)
                full = int(overs_val)
                extra = round((overs_val - full) * 10)
                balls_bowled = full * 6 + extra

                # Count dot balls bowled by this player
                dot_balls = Ball.objects.filter(
                    over__innings__match=match,
                    bowler=player,
                    runs_off_bat=0,
                    extra_runs=0,
                    is_legal_ball=True,
                ).count()

                pressure = (dot_balls / balls_bowled * 30) if balls_bowled > 0 else 0
                uii += wicket_pct + pressure

                # Partnership Breaker bonus
                for ball in Ball.objects.filter(over__innings__match=match, bowler=player, is_wicket=True).select_related('player_dismissed'):
                    if ball.player_dismissed:
                        dismissed_sc = BattingScorecard.objects.filter(
                            innings__match=match,
                            batsman=ball.player_dismissed
                        ).first()
                        if dismissed_sc:
                            t = dismissed_sc.innings.total_runs
                            if t > 0 and dismissed_sc.runs >= (t * 0.25):
                                uii += 10

        # ── 3. FINISHER BONUS ──
        if chase_success and bat_sc and bat_sc.status == 'NOT_OUT' and bat_sc.innings == inn2:
            uii += 15

        # ── 4. RESULT MULTIPLIER ──
        if winner_team and player_team == winner_team:
            uii *= 1.1

        if uii > best_uii:
            best_uii = uii
            best_player = player

    return best_player, best_uii if best_player else (None, 0)


def award_man_of_the_match(match_id):
    """Award MOM for a completed match. Safe to call multiple times (idempotent)."""
    from matches.models import ManOfTheMatch
    match = CreateMatch.objects.filter(id=match_id).first()
    if not match:
        return

    # Already awarded
    if ManOfTheMatch.objects.filter(match=match).exists():
        return

    result = calculate_uii(match)
    if not result or not result[0]:
        return

    best_player, best_uii = result

    # Snapshot batting + bowling stats
    bat_sc   = BattingScorecard.objects.filter(innings__match=match, batsman=best_player).first()
    bowl_sc  = BowlingScorecard.objects.filter(innings__match=match, bowler=best_player).first()

    ManOfTheMatch.objects.create(
        match=match,
        player=best_player,
        uii_score=round(best_uii, 2),
        bat_runs=bat_sc.runs if bat_sc else 0,
        bat_balls=bat_sc.balls_faced if bat_sc else 0,
        bat_fours=bat_sc.fours if bat_sc else 0,
        bat_sixes=bat_sc.sixes if bat_sc else 0,
        bowl_wickets=bowl_sc.wickets if bowl_sc else 0,
        bowl_runs=bowl_sc.runs_given if bowl_sc else 0,
        bowl_overs=str(bowl_sc.overs_bowled) if bowl_sc else '0',
    )


# ── LIVE SCORE API (used by home page auto-refresh) ──


# ── Force Complete Tournament (manual button) ─────────────────────────────────
def force_complete_tournament(request, tournament_id):
    """
    Allows organiser to manually complete a tournament (e.g. league-only,
    or after league before knockout is set up).
    Sets is_force_completed=True and triggers awards.
    Only POST. Only for can_manage users.
    """
    from django.views.decorators.http import require_POST as _require_POST
    from subscriptions.decorators import _is_privileged, _get_effective_plan
    from .views_core import admin_required as _admin_required

    if request.method != 'POST':
        from django.http import JsonResponse
        return JsonResponse({'error': 'POST required'}, status=405)

    if not (_is_privileged(request) or _get_effective_plan(request) == 'pro_plus'):
        from django.http import JsonResponse
        return JsonResponse({'error': 'Permission denied.'}, status=403)

    tournament = get_object_or_404(TournamentDetails, id=tournament_id)

    # Mark as force-completed
    tournament.is_force_completed = True
    tournament.save(update_fields=['is_force_completed'])

    # Run awards
    award_tournament_awards(tournament_id)

    from django.http import JsonResponse
    return JsonResponse({'success': True, 'message': 'Tournament completed and awards calculated.'})