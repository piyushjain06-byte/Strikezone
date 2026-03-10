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
from accounts.models import GuestUser, PlayerFollow

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

def player_stats(request):
    player_id = request.session.get('player_id')
    if not player_id:
        return redirect('player_login')

    is_guest_session = str(player_id) == 'guest' or not str(player_id).isdigit()

    # ── AUTO-UPGRADE: if session is guest but a PlayerDetails now exists for their mobile ──
    if is_guest_session:
        mobile = request.session.get('player_mobile', '')
        if mobile:
            real_player = PlayerDetails.objects.filter(mobile_number=mobile).first()
            if real_player:
                # Promote session to real player
                request.session['player_id']   = real_player.id
                request.session['player_name'] = real_player.player_name
                player_id        = real_player.id
                is_guest_session = False

    # ── GUEST PROFILE VIEW / EDIT ──
    if is_guest_session:
        mobile = request.session.get('player_mobile', '')
        guest  = GuestUser.objects.filter(mobile_number=mobile).first() if mobile else None

        if not guest:
            return render(request, 'player_stats.html', {
                'player': None, 'is_guest': True,
                'player_name': request.session.get('player_name', 'Guest'),
                'guest': None,
            })

        if request.method == 'POST':
            # Photo upload
            if request.FILES.get('photo'):
                guest.photo = request.FILES['photo']
                guest.save(update_fields=['photo'])
                messages.success(request, 'Profile photo updated!')
                return redirect('player_stats')

            # Profile fields update
            if request.POST.get('update_profile'):
                new_name   = (request.POST.get('display_name') or '').strip()
                new_mobile = (request.POST.get('mobile_number') or '').strip()

                if not new_name:
                    messages.error(request, 'Name cannot be empty.')
                    return redirect('player_stats')

                if new_mobile and (len(new_mobile) < 10 or not new_mobile.replace('+', '').isdigit()):
                    messages.error(request, 'Enter a valid mobile number (at least 10 digits).')
                    return redirect('player_stats')

                if new_mobile and new_mobile != guest.mobile_number:
                    if GuestUser.objects.filter(mobile_number=new_mobile).exclude(id=guest.id).exists():
                        messages.error(request, 'This mobile number is already registered.')
                        return redirect('player_stats')
                    if PlayerDetails.objects.filter(mobile_number=new_mobile).exists():
                        messages.error(request, 'This mobile number is already linked to a player.')
                        return redirect('player_stats')
                    guest.mobile_number = new_mobile
                    request.session['player_mobile'] = new_mobile

                guest.display_name = new_name
                guest.save(update_fields=['display_name', 'mobile_number'])
                request.session['player_name'] = new_name
                messages.success(request, 'Profile updated successfully.')
                return redirect('player_stats')

        display_name = guest.display_name or request.session.get('player_name', 'Guest')
        return render(request, 'player_stats.html', {
            'player': None,
            'is_guest': True,
            'guest': guest,
            'guest_display_name': display_name,
            'guest_mobile': guest.mobile_number,
        })

    # ── REAL PLAYER VIEW ──
    player = get_object_or_404(PlayerDetails, id=int(player_id))

    # Handle profile updates
    if request.method == 'POST':
        if request.FILES.get('photo'):
            player.photo = request.FILES['photo']
            player.save(update_fields=['photo'])
            messages.success(request, 'Profile photo updated!')
            return redirect('player_stats')

        if request.POST.get('update_profile'):
            new_name = (request.POST.get('player_name') or '').strip()
            new_mobile = (request.POST.get('mobile_number') or '').strip()

            if not new_name:
                messages.error(request, "Name cannot be empty.")
                return redirect('player_stats')

            if new_mobile and (len(new_mobile) < 10 or not new_mobile.replace("+", "").isdigit()):
                messages.error(request, "Enter a valid mobile number (at least 10 digits).")
                return redirect('player_stats')

            if new_mobile and PlayerDetails.objects.filter(mobile_number=new_mobile).exclude(id=player.id).exists():
                messages.error(request, "This mobile number is already used by another player.")
                return redirect('player_stats')

            player.player_name = new_name
            player.mobile_number = new_mobile or None
            player.save(update_fields=['player_name', 'mobile_number'])

            # Keep GuestUser and session in sync when mobile changes
            old_mobile = request.session.get('player_mobile')
            if old_mobile and new_mobile and old_mobile != new_mobile:
                guest = GuestUser.objects.filter(mobile_number=old_mobile).first()
                if guest:
                    guest.mobile_number = new_mobile or old_mobile
                    guest.is_mobile_verified = False
                    guest.save(update_fields=['mobile_number', 'is_mobile_verified'])
                request.session['player_mobile'] = new_mobile or old_mobile

            request.session['player_name'] = player.player_name
            messages.success(request, "Profile updated successfully.")
            return redirect('player_stats')

    teams_played = (
        TournamentRoster.objects
        .filter(player=player)
        .select_related('tournament_team__team')
        .values_list('tournament_team__team__team_name', flat=True)
        .distinct()
    )

    batting_entries = BattingScorecard.objects.filter(batsman=player).select_related('innings__match__tournament')

    total_matches     = batting_entries.values('innings__match').distinct().count()
    total_innings_b   = batting_entries.exclude(status='DNB').count()
    total_runs        = sum(b.runs for b in batting_entries)
    total_balls_faced = sum(b.balls_faced for b in batting_entries)
    total_fours       = sum(b.fours for b in batting_entries)
    total_sixes       = sum(b.sixes for b in batting_entries)
    not_outs          = batting_entries.filter(status='NOT_OUT').count()
    dismissals        = total_innings_b - not_outs
    highest_score     = batting_entries.order_by('-runs').first()
    fifties           = batting_entries.filter(runs__gte=50, runs__lt=100).count()
    hundreds          = batting_entries.filter(runs__gte=100).count()
    batting_avg       = round(total_runs / dismissals, 2) if dismissals > 0 else total_runs
    batting_sr        = round((total_runs / total_balls_faced) * 100, 2) if total_balls_faced > 0 else 0

    bowling_entries = BowlingScorecard.objects.filter(bowler=player).select_related('innings__match__tournament')

    total_wickets       = sum(b.wickets for b in bowling_entries)
    total_runs_given    = sum(b.runs_given for b in bowling_entries)
    total_wides         = sum(b.wides for b in bowling_entries)
    total_no_balls      = sum(b.no_balls for b in bowling_entries)
    best_bowling        = bowling_entries.order_by('-wickets', 'runs_given').first()
    def overs_to_balls(overs_val):
        o = float(overs_val)
        full = int(o)
        extra = round((o - full) * 10)
        return full * 6 + extra
    total_balls_bowled  = sum(overs_to_balls(b.overs_bowled) for b in bowling_entries)
    bowling_economy     = round((total_runs_given * 6) / total_balls_bowled, 2) if total_balls_bowled > 0 else 0
    bowling_avg         = round(total_runs_given / total_wickets, 2) if total_wickets > 0 else 0

    mom_count = ManOfTheMatch.objects.filter(player=player).count()
    recent_innings = batting_entries.order_by('-innings__match__match_date')[:5]
    recent_bowling = bowling_entries.order_by('-innings__match__match_date')[:5]

    # Tournament awards for this player
    tourn_awards = TournamentAward.objects.filter(player=player).select_related('tournament').order_by('-awarded_at')
    mot_count  = tourn_awards.filter(award_type='MOT').count()
    bbat_count = tourn_awards.filter(award_type='BBAT').count()
    bbol_count = tourn_awards.filter(award_type='BBOL').count()

    # Fielding stats
    from scoring.models import Ball as ScoringBall
    catches   = ScoringBall.objects.filter(fielder=player, wicket_type__in=['CAUGHT','CAUGHT_AND_BOWLED']).count()
    run_outs  = ScoringBall.objects.filter(fielder=player, wicket_type='RUN_OUT').count()
    stumpings = ScoringBall.objects.filter(fielder=player, wicket_type='STUMPED').count()
    # Dismissals detail list (last 10)
    recent_dismissals = (
        ScoringBall.objects
        .filter(fielder=player, is_wicket=True)
        .select_related('player_dismissed', 'bowler', 'over__innings__match')
        .order_by('-id')[:10]
    )

    # Followers: registered players + guests
    from accounts.models import GuestFollow
    player_followers = PlayerFollow.objects.filter(following=player).select_related('follower').order_by('-created_at')
    guest_followers  = GuestFollow.objects.filter(following=player).select_related('guest').order_by('-created_at')
    follower_count   = player_followers.count() + guest_followers.count()

    return render(request, 'player_stats.html', {
        'player': player,
        'is_guest': False,
        'my_followers': player_followers,
        'my_guest_followers': guest_followers,
        'follower_count': follower_count,
        'teams_played': list(teams_played),
        'total_matches': total_matches,
        'total_innings_b': total_innings_b,
        'total_runs': total_runs,
        'total_balls_faced': total_balls_faced,
        'total_fours': total_fours,
        'total_sixes': total_sixes,
        'not_outs': not_outs,
        'highest_score': highest_score,
        'fifties': fifties,
        'hundreds': hundreds,
        'batting_avg': batting_avg,
        'batting_sr': batting_sr,
        'total_wickets': total_wickets,
        'total_runs_given': total_runs_given,
        'total_wides': total_wides,
        'total_no_balls': total_no_balls,
        'best_bowling': best_bowling,
        'bowling_economy': bowling_economy,
        'bowling_avg': bowling_avg,
        'recent_innings': recent_innings,
        'recent_bowling': recent_bowling,
        'mom_count': mom_count,
        'tourn_awards': tourn_awards,
        'mot_count': mot_count,
        'bbat_count': bbat_count,
        'bbol_count': bbol_count,
        'catches': catches,
        'run_outs': run_outs,
        'stumpings': stumpings,
        'recent_dismissals': recent_dismissals,
    })


# ── PLAYER STATS API (JSON) ──

def player_stats_api(request, player_id):
    player = get_object_or_404(PlayerDetails, id=player_id)

    batting_entries   = BattingScorecard.objects.filter(batsman=player).select_related('innings__match')
    total_runs        = sum(b.runs for b in batting_entries)
    total_balls_faced = sum(b.balls_faced for b in batting_entries)
    total_fours       = sum(b.fours for b in batting_entries)
    total_sixes       = sum(b.sixes for b in batting_entries)
    not_outs          = batting_entries.filter(status='NOT_OUT').count()
    total_innings_b   = batting_entries.exclude(status='DNB').count()
    dismissals        = total_innings_b - not_outs
    batting_avg       = round(total_runs / dismissals, 2) if dismissals > 0 else total_runs
    batting_sr        = round((total_runs / total_balls_faced) * 100, 2) if total_balls_faced > 0 else 0
    fifties           = batting_entries.filter(runs__gte=50, runs__lt=100).count()
    hundreds          = batting_entries.filter(runs__gte=100).count()
    hs_entry          = batting_entries.order_by('-runs').first()
    highest_score     = hs_entry.runs if hs_entry else 0

    recent_innings = []
    for b in batting_entries.order_by('-innings__match__match_date')[:5]:
        recent_innings.append({
            'match': f"{b.innings.match.team1} vs {b.innings.match.team2}",
            'runs': b.runs,
            'balls_faced': b.balls_faced,
            'strike_rate': b.strike_rate,
        })

    bowling_entries  = BowlingScorecard.objects.filter(bowler=player).select_related('innings__match')
    total_wickets    = sum(b.wickets for b in bowling_entries)
    total_runs_given = sum(b.runs_given for b in bowling_entries)
    total_wides      = sum(b.wides for b in bowling_entries)
    total_no_balls   = sum(b.no_balls for b in bowling_entries)
    total_overs_dec  = sum(float(b.overs_bowled) for b in bowling_entries)
    def _o2b(o):
        f = int(o); return f*6 + round((o-f)*10)
    total_balls_api  = sum(_o2b(float(b.overs_bowled)) for b in bowling_entries)
    bowling_economy  = round((total_runs_given * 6) / total_balls_api, 2) if total_balls_api > 0 else 0
    bowling_avg      = round(total_runs_given / total_wickets, 2) if total_wickets > 0 else 0
    best_entry       = bowling_entries.order_by('-wickets', 'runs_given').first()
    best_bowling     = f"{best_entry.wickets}/{best_entry.runs_given}" if best_entry else '—'

    recent_bowling = []
    for b in bowling_entries.order_by('-innings__match__match_date')[:5]:
        recent_bowling.append({
            'match': f"{b.innings.match.team1} vs {b.innings.match.team2}",
            'overs_bowled': str(b.overs_bowled),
            'runs_given': b.runs_given,
            'wickets': b.wickets,
        })

    photo_url = player.photo.url if player.photo else ''
    mom_count = ManOfTheMatch.objects.filter(player=player).count()
    from scoring.models import HatTrick as HT
    hat_trick_count = HT.objects.filter(bowler=player).count()

    return JsonResponse({
        'photo_url': photo_url,
        'total_runs': total_runs,
        'batting_avg': batting_avg,
        'batting_sr': batting_sr,
        'highest_score': highest_score,
        'fifties': fifties,
        'hundreds': hundreds,
        'total_fours': total_fours,
        'total_sixes': total_sixes,
        'recent_innings': recent_innings,
        'total_wickets': total_wickets,
        'bowling_economy': bowling_economy,
        'bowling_avg': bowling_avg,
        'best_bowling': best_bowling,
        'total_runs_given': total_runs_given,
        'total_wides': total_wides,
        'total_no_balls': total_no_balls,
        'recent_bowling': recent_bowling,
        'mom_count': mom_count,
        'hat_trick_count': hat_trick_count,
    })


def player_matches(request):
    player_id = request.session.get('player_id')
    if not player_id:
        return redirect(f"{reverse('player_login')}?next={reverse('player_matches')}")

    is_guest = str(player_id).startswith('guest') or not str(player_id).isdigit()
    if is_guest:
        return render(request, 'player_matches.html', {
            'is_guest': True,
            'tournaments': [],
        })

    player = get_object_or_404(PlayerDetails, id=int(player_id))

    rosters = (
        TournamentRoster.objects
        .filter(player=player)
        .select_related('tournament', 'tournament_team__team')
    )

    tournaments_map = {}
    for r in rosters:
        t = r.tournament
        key = t.id
        if key not in tournaments_map:
            tournaments_map[key] = {
                'tournament': t,
                'teams': set(),
                'matches': [],
            }
        tournaments_map[key]['teams'].add(r.tournament_team.team)

    for info in tournaments_map.values():
        teams = list(info['teams'])
        matches = CreateMatch.objects.filter(
            tournament=info['tournament']
        ).filter(
            django_models.Q(team1__in=teams) | django_models.Q(team2__in=teams)
        ).select_related('team1', 'team2').order_by('match_date')

        match_rows = []
        for m in matches:
            inn1 = Innings.objects.filter(match=m, innings_number=1).first()
            inn2 = Innings.objects.filter(match=m, innings_number=2).first()

            status = 'SCHEDULED'
            status_label = 'Scheduled'

            if inn2 and inn2.status == 'COMPLETED':
                status = 'COMPLETED'
                status_label = 'Completed'
            elif (inn1 and inn1.status == 'IN_PROGRESS') or (inn2 and inn2.status == 'IN_PROGRESS'):
                status = 'LIVE'
                status_label = 'Live'
            elif inn1 and inn1.status == 'COMPLETED':
                status = 'IN_PROGRESS'
                status_label = 'Innings Break'
            elif inn1:
                status = 'LIVE'
                status_label = 'Live'

            has_scorecard = bool(inn1 or inn2)
            is_live = status in ('LIVE', 'IN_PROGRESS')

            match_rows.append({
                'match': m,
                'status': status,
                'status_label': status_label,
                'has_scorecard': has_scorecard,
                'is_live': is_live,
            })

        info['matches'] = match_rows

    tournaments_list = sorted(
        tournaments_map.values(),
        key=lambda x: x['tournament'].start_date or x['tournament'].created_at
    )

    return render(request, 'player_matches.html', {
        'is_guest': False,
        'tournaments': tournaments_list,
    })


# ══════════════════════════════════════════════
# KNOCKOUT BRACKET VIEWS
# ══════════════════════════════════════════════

def public_player_profile(request, player_id):
    from django.db.models import Sum, Count, Max

    player = get_object_or_404(PlayerDetails, id=player_id)

    # All rosters (to know which teams / tournaments)
    rosters = (
        TournamentRoster.objects
        .filter(player=player)
        .select_related('tournament_team__team', 'tournament_team__tournament')
        .order_by('-id')
    )

    # Batting stats
    bat_qs = BattingScorecard.objects.filter(batsman=player).select_related(
        'innings__match__team1', 'innings__match__team2',
        'innings__match__tournament', 'innings__batting_team',
    ).order_by('-innings__match__match_date', '-id')

    bat_total = bat_qs.aggregate(
        total_runs=Sum('runs'),
        total_balls=Sum('balls_faced'),
        total_fours=Sum('fours'),
        total_sixes=Sum('sixes'),
        innings_count=Count('id'),
        highest=Max('runs'),
    )

    # Bowling stats
    bowl_qs = BowlingScorecard.objects.filter(bowler=player).select_related(
        'innings__match__team1', 'innings__match__team2',
        'innings__match__tournament', 'innings__batting_team',
    ).order_by('-innings__match__match_date', '-id')

    bowl_total = bowl_qs.aggregate(
        total_wickets=Sum('wickets'),
        total_runs_given=Sum('runs_given'),
        innings_count=Count('id'),
    )

    # MOM awards
    mom_awards = ManOfTheMatch.objects.filter(player=player).select_related(
        'match__team1', 'match__team2', 'match__tournament'
    ).order_by('-awarded_at')

    # Tournament awards (MOT, Best Batsman, Best Bowler)
    tournament_awards = TournamentAward.objects.filter(player=player).select_related(
        'tournament'
    ).order_by('-awarded_at')

    # Hat-tricks
    from scoring.models import HatTrick
    hat_tricks = HatTrick.objects.filter(bowler=player).select_related(
        'match__team1', 'match__team2', 'match__tournament',
        'victim1', 'victim2', 'victim3',
    ).order_by('-created_at')

    # Compute averages
    bat_avg = None
    sr = None
    if bat_total['innings_count']:
        bat_avg = round(bat_total['total_runs'] / bat_total['innings_count'], 2) if bat_total['total_runs'] else 0
        sr = round(bat_total['total_runs'] / bat_total['total_balls'] * 100, 1) if bat_total['total_balls'] else 0

    bowl_avg = None
    bowl_econ = None
    if bowl_total['innings_count'] and bowl_total['total_wickets']:
        bowl_avg = round(bowl_total['total_runs_given'] / bowl_total['total_wickets'], 2)

    session_player_id = request.session.get('player_id')
    is_own        = (str(session_player_id) == str(player_id))
    is_logged_in  = bool(session_player_id and str(session_player_id).isdigit())
    follower_count = PlayerFollow.objects.filter(following=player).count()
    is_following  = False
    if is_logged_in and not is_own:
        try:
            me = PlayerDetails.objects.get(id=int(session_player_id))
            is_following = PlayerFollow.objects.filter(follower=me, following=player).exists()
        except PlayerDetails.DoesNotExist:
            pass
    profile_url = request.build_absolute_uri(f'/player/{player.id}/profile/')
    return render(request, 'player_profile.html', {
        'player': player,
        'rosters': rosters,
        'bat_records': bat_qs,
        'bowl_records': bowl_qs,
        'bat_total': bat_total,
        'bowl_total': bowl_total,
        'bat_avg': bat_avg,
        'sr': sr,
        'bowl_avg': bowl_avg,
        'mom_awards': mom_awards,
        'tournament_awards': tournament_awards,
        'hat_tricks': hat_tricks,
        'is_own': is_own,
        'is_logged_in': is_logged_in,
        'follower_count': follower_count,
        'is_following': is_following,
        'profile_url': profile_url,
    })




# ─────────────────────────────────────────────────────────────────
# PLAYER ANALYSIS VIEW  –  /player/<id>/analysis/
# ─────────────────────────────────────────────────────────────────
@admin_required
@require_plan('pro_plus')
def edit_player(request, player_id):
    """Edit a player's details via AJAX POST — returns JSON."""
    player = get_object_or_404(PlayerDetails, id=player_id)
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
        except Exception:
            data = request.POST

        player_name    = (data.get('player_name') or '').strip()
        mobile_number  = (data.get('mobile_number') or '').strip() or None
        role           = data.get('role') or player.role or 'BATSMAN'
        jersey_number  = data.get('jersey_number') or None

        if not player_name:
            return JsonResponse({'success': False, 'error': 'Player name is required'})

        player.player_name   = player_name
        # Only update mobile if explicitly sent (not null) — it's used for OTP login
        if mobile_number is not None:
            player.mobile_number = mobile_number
        player.role          = role
        if jersey_number is not None:
            player.jersey_number = jersey_number
        player.save()

        # Also update TournamentRoster captain/vc flags if provided
        roster_id  = data.get('roster_id')
        is_captain = data.get('is_captain')
        is_vc      = data.get('is_vice_captain')
        if roster_id is not None:
            try:
                roster = TournamentRoster.objects.get(id=roster_id)
                if is_captain is not None:
                    roster.is_captain      = bool(is_captain)
                if is_vc is not None:
                    roster.is_vice_captain = bool(is_vc)
                roster.save()
            except TournamentRoster.DoesNotExist:
                pass

        return JsonResponse({
            'success': True,
            'player_name': player.player_name,
            'role': player.role,
        })
    return JsonResponse({'success': False, 'error': 'POST required'}, status=405)


@admin_required
@require_plan('pro_plus')
def delete_player(request, player_id):
    """Remove a player from a tournament roster (or fully delete if no roster_id)."""
    player = get_object_or_404(PlayerDetails, id=player_id)
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
        except Exception:
            data = request.POST

        roster_id = data.get('roster_id')
        if roster_id:
            # Remove only from this tournament's roster
            TournamentRoster.objects.filter(id=roster_id).delete()
            return JsonResponse({'success': True, 'action': 'removed_from_roster'})
        else:
            # Full delete — remove all rosters then the player
            TournamentRoster.objects.filter(player=player).delete()
            player.delete()
            return JsonResponse({'success': True, 'action': 'deleted'})
    return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

# ── Follow toggle ────────────────────────────────────────────────────────
def toggle_follow(request, player_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    session_player_id = request.session.get('player_id')
    if not session_player_id or not str(session_player_id).isdigit():
        return JsonResponse({'error': 'Login required.'}, status=401)
    if int(session_player_id) == player_id:
        return JsonResponse({'error': 'Cannot follow yourself.'}, status=400)
    follower  = get_object_or_404(PlayerDetails, id=int(session_player_id))
    following = get_object_or_404(PlayerDetails, id=player_id)
    obj = PlayerFollow.objects.filter(follower=follower, following=following).first()
    if obj:
        obj.delete()
        action = 'unfollowed'
    else:
        PlayerFollow.objects.create(follower=follower, following=following)
        action = 'followed'
    return JsonResponse({
        'action': action,
        'follower_count': PlayerFollow.objects.filter(following=following).count(),
    })


# ── Followers list ───────────────────────────────────────────────────────
def player_followers_list(request, player_id):
    player    = get_object_or_404(PlayerDetails, id=player_id)
    followers = PlayerFollow.objects.filter(following=player).select_related('follower').order_by('-created_at')
    return render(request, 'player_followers.html', {
        'player': player,
        'followers': followers,
        'follower_count': followers.count(),
    })