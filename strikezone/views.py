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
from datetime import date, datetime, timedelta


# ── ADMIN ONLY DECORATOR ──
def admin_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser):
            return view_func(request, *args, **kwargs)
        messages.error(request, "You must be logged in as an admin to access this page.")
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
    tournaments = TournamentDetails.objects.all()
    return render(request, 'tournaments.html', {'tournaments': tournaments})


def tournamentdetails(request, id):
    tournament = get_object_or_404(TournamentDetails, id=id)
    teams = (
        TeamDetails.objects
        .filter(tournament_entries__tournament=tournament)
        .distinct()
        .order_by('team_name')
    )
    tournament_complete = _is_tournament_complete(tournament)
    # Try to backfill awards for already-completed tournaments
    if tournament_complete:
        award_tournament_awards(id)
    return render(request, 'tournamentdetails.html', {
        'tournament': tournament,
        'teams': teams,
        'tournament_complete': tournament_complete,
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


def manage_cricket(request):
    is_admin = request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser)

    if not is_admin:
        messages.error(request, "You must be logged in as an admin to access this page.")
        next_url = request.get_full_path()
        return redirect(f"{reverse('admin_login')}?next={quote(next_url, safe='')}")

    tournament_form = TournamentForm()
    team_form = TeamForm()
    player_form = PlayerForm()
    active_tab = 'tournament'

    if request.method == "POST":

        if "tournament_submit" in request.POST:
            tournament_form = TournamentForm(request.POST)
            if tournament_form.is_valid():
                tournament_form.save()
                messages.success(request, "Tournament created successfully!")
                tournament_form = TournamentForm()
            active_tab = 'tournament'
        elif "team_submit" in request.POST:
            team_form = TeamForm(request.POST)
            if team_form.is_valid():
                tournament = team_form.cleaned_data["tournament"]
                team_code = (team_form.cleaned_data.get("team_code") or "").strip()
                team_name = (team_form.cleaned_data.get("team_name") or "").strip()
                team_created_date = team_form.cleaned_data.get("team_created_date")

                team = None
                if team_code:
                    team = TeamDetails.objects.filter(team_code__iexact=team_code).first()
                    if not team:
                        messages.error(request, f"No team found with Team ID '{team_code}'.")
                        active_tab = 'team'
                        tournaments_qs = TournamentDetails.objects.all()
                        # fall through to render at end
                    else:
                        TournamentTeam.objects.get_or_create(
                            tournament=tournament,
                            team=team,
                        )
                        messages.success(request, f"Team '{team.team_name}' ({team.team_code}) registered for {tournament.tournament_name}!")
                        team_form = TeamForm()
                else:
                    if not team_name:
                        messages.error(request, "Team name is required when Team ID is not provided.")
                    else:
                        team = TeamDetails.objects.create(
                            team_name=team_name,
                            team_created_date=team_created_date,
                        )
                        TournamentTeam.objects.get_or_create(
                            tournament=tournament,
                            team=team,
                        )
                        messages.success(request, f"New Team '{team.team_name}' created with ID {team.team_code} and registered for {tournament.tournament_name}!")
                        team_form = TeamForm()
            active_tab = 'team'
        elif "player_submit" in request.POST:
            player_form = PlayerForm(request.POST, request.FILES)
            if player_form.is_valid():
                tournament = player_form.cleaned_data["tournament"]
                team = player_form.cleaned_data["team"]
                player_name = (player_form.cleaned_data.get("player_name") or "").strip()
                mobile_number = (player_form.cleaned_data.get("mobile_number") or "").strip() or None
                photo = player_form.cleaned_data.get("photo")

                role = player_form.cleaned_data.get("role") or "BATSMAN"
                is_captain = bool(player_form.cleaned_data.get("is_captain"))
                is_vice_captain = bool(player_form.cleaned_data.get("is_vice_captain"))
                jersey_number = player_form.cleaned_data.get("jersey_number")

                tournament_team, _ = TournamentTeam.objects.get_or_create(
                    tournament=tournament,
                    team=team,
                )

                player = None
                if mobile_number:
                    player = PlayerDetails.objects.filter(mobile_number=mobile_number).first()

                    if player:
                        # Mobile-only flow: if name not provided, keep existing name.
                        if player_name and player.player_name != player_name:
                            player.player_name = player_name
                            player.save(update_fields=["player_name"])
                    else:
                        # New player with this mobile -> auto-create identity (no name needed)
                        if not player_name:
                            player_name = f"Player {mobile_number}"
                        player = PlayerDetails.objects.create(
                            player_name=player_name,
                            mobile_number=mobile_number,
                        )
                else:
                    # No mobile -> create a new player identity (name required by form)
                    player = PlayerDetails.objects.create(
                        player_name=player_name,
                        mobile_number=None,
                    )

                if photo and player:
                    player.photo = photo
                    player.save(update_fields=["photo"])

                try:
                    TournamentRoster.objects.create(
                        tournament_team=tournament_team,
                        player=player,
                        role=role,
                        is_captain=is_captain,
                        is_vice_captain=is_vice_captain,
                        jersey_number=jersey_number,
                    )
                    messages.success(request, f"{player.player_name} added to {team.team_name} ({tournament.tournament_name})!")
                    player_form = PlayerForm()
                except IntegrityError:
                    messages.error(
                        request,
                        f"{player.player_name} is already assigned to a team in {tournament.tournament_name}.",
                    )
            active_tab = 'player'

    tournaments_qs = TournamentDetails.objects.all()
    tournament_progress = []
    for t in tournaments_qs:
        tournament_teams = list(
            TournamentTeam.objects.filter(tournament=t).select_related("team").order_by("team__team_name")
        )
        teams_added = len(tournament_teams)
        teams_needed = t.number_of_teams
        teams_remaining = max(0, teams_needed - teams_added)
        team_data = []
        for tt in tournament_teams:
            roster = list(
                TournamentRoster.objects.filter(tournament_team=tt).select_related("player").order_by("id")
            )
            team_data.append({
                'tournament_team': tt,
                'team': tt.team,
                'roster': roster,
                'player_count': len(roster),
            })
        tournament_progress.append({
            'tournament': t,
            'teams_added': teams_added,
            'teams_needed': teams_needed,
            'teams_remaining': teams_remaining,
            'slots_range': range(teams_remaining),
            'is_complete': teams_added >= teams_needed,
            'team_data': team_data,
        })

    context = {
        'is_admin': is_admin,
        'tournament_form': tournament_form,
        'team_form': team_form,
        'player_form': player_form,
        'tournament_progress': tournament_progress,
        'tournaments': tournaments_qs,
        'teams': TeamDetails.objects.all().order_by('team_name'),
        'active_tab': active_tab,
        'team_count_options': [2, 4, 6, 8, 10],
    }
    return render(request, 'manage_cricket.html', context)


@admin_required
def create_match(request):
    if request.method == "POST":
        form = MatchForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('create_match')
    else:
        form = MatchForm()

    all_tournaments = TournamentDetails.objects.all()
    all_matches = CreateMatch.objects.select_related(
        'tournament', 'team1', 'team2'
    ).order_by('match_date')

    sidebar = []
    for t in all_tournaments:
        t_matches = [m for m in all_matches if m.tournament_id == t.id]
        sidebar.append({'tournament': t, 'matches': t_matches})

    return render(request, 'create_match.html', {'form': form, 'sidebar': sidebar})


def load_teams(request):
    tournament_id = request.GET.get('tournament_id')
    teams = TeamDetails.objects.filter(tournament_entries__tournament_id=tournament_id).distinct()
    team_list = [{'id': t.id, 'name': t.team_name} for t in teams]
    return JsonResponse(team_list, safe=False)


@admin_required
def start_tournament(request):
    tournaments_qs = TournamentDetails.objects.all()
    if request.method == "POST":
        tournament_id = request.POST.get("tournament_id")
        tournament = get_object_or_404(TournamentDetails, id=tournament_id)
        start_obj, created = StartTournament.objects.get_or_create(tournament=tournament)
        start_obj.is_started = True
        start_obj.save()
        messages.success(request, f"{tournament.tournament_name} has been started successfully!")
        return redirect("match_start")
    return render(request, "start_tournament.html", {"tournaments": tournaments_qs})


@admin_required
def match_start(request):
    # Only show matches that have NOT been started yet (no MatchStart record)
    started_match_ids = MatchStart.objects.values_list('match_id', flat=True)
    matches = CreateMatch.objects.exclude(id__in=started_match_ids).select_related('team1', 'team2', 'tournament').order_by('tournament__tournament_name', 'match_date')
    if request.method == "POST":
        match_id = request.POST.get("match_id")
        toss_winner_id = request.POST.get("toss_winner")
        decision = request.POST.get("decision")
        match = get_object_or_404(CreateMatch, id=match_id)
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

        return redirect('scoring', match_id=match_id)

    return render(request, 'start_innings.html', {
        'match': match, 'match_start': match_start,
        'batting_team': batting_team, 'bowling_team': bowling_team,
        'batsmen': batsmen, 'bowlers': bowlers,
        'innings_number': next_innings_number,
    })


# ── STEP 2: Live Scoring Page ──

@admin_required
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
        'max_overs': match.tournament.number_of_overs,
        'target': target,
    })


# ── STEP 3: AJAX - Record Ball ──

@admin_required
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
    # On legal ball: odd runs off bat → batsmen cross → swap striker/non-striker
    if ball.is_legal_ball and (runs_off_bat % 2 == 1):
        request.session['striker_id'], request.session['non_striker_id'] = (
            int(non_striker_id), int(striker_id)
        )

    # If wicket: the dismissed batsman must be replaced
    # striker_id/non_striker_id may have already swapped above if odd runs
    # The dismissed player is the one who faced the ball (batsman),
    # so ensure they are marked correctly in session
    if is_wicket:
        # After possible swap, set striker to dismissed batsman so new batsman replaces them
        # Actually: the new batsman always comes in at the END the dismissed player was at
        # If striker was dismissed (no swap), new batsman = striker
        # If striker was dismissed but also scored odd runs (ran before wicket), new = non_striker end
        # This is handled by session already — dismissed batsman's session slot gets new batsman
        dismissed_id = batsman.id
        current_s  = request.session.get('striker_id')
        current_ns = request.session.get('non_striker_id')
        if current_s and int(current_s) == dismissed_id:
            pass  # new batsman will replace striker — correct
        elif current_ns and int(current_ns) == dismissed_id:
            # Dismissed player is now in non_striker slot — new batsman should be non_striker
            # But convention: new batsman always comes in at striker end facing next ball
            request.session['striker_id'] = dismissed_id  # will be overwritten by select_new_batsman
        # Ensure dismissed player is in striker slot so new batsman replaces striker
        request.session['striker_id'] = dismissed_id

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

    current_striker = PlayerDetails.objects.filter(id=request.session.get('striker_id')).first()
    current_non_striker = PlayerDetails.objects.filter(id=request.session.get('non_striker_id')).first()

    needs_new_batsman = is_wicket and not innings_complete

    # ── Batsman stats for instant UI update ──
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
        'striker_id': request.session.get('striker_id'),
        'non_striker_id': request.session.get('non_striker_id'),
        'striker_name': current_striker.player_name if current_striker else '',
        'non_striker_name': current_non_striker.player_name if current_non_striker else '',
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
@require_POST
def select_new_batsman(request, match_id):
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'Invalid JSON data'}, status=400)

    new_batsman_id = data.get('new_batsman_id')
    if not new_batsman_id:
        return JsonResponse({'error': 'No batsman selected.'}, status=400)

    new_batsman = get_object_or_404(PlayerDetails, id=new_batsman_id)
    request.session['striker_id'] = int(new_batsman_id)

    # Return over_complete & innings_complete so JS knows whether to show bowler modal
    innings_id = request.session.get('innings_id')
    over_id    = request.session.get('over_id')
    innings    = Innings.objects.filter(id=innings_id).first()
    over       = Over.objects.filter(id=over_id).first()

    over_complete     = bool(over and over.is_completed)
    innings_complete  = bool(innings and innings.status == 'COMPLETED')

    # Build fresh available_batsmen list (server-authoritative)
    striker_id     = request.session.get('striker_id')
    non_striker_id = request.session.get('non_striker_id')
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
        currently_in = []
        if striker_id:    currently_in.append(int(striker_id))
        if non_striker_id: currently_in.append(int(non_striker_id))
        excluded = list(set(dismissed_ids + currently_in))
        if batting_tt:
            qs = (
                PlayerDetails.objects.filter(tournament_rosters__tournament_team=batting_tt)
                .exclude(id__in=excluded)
                .distinct()
                .order_by('player_name')
            )
            fresh_available = [{'id': p.id, 'name': p.player_name} for p in qs]

    return JsonResponse({
        'success': True,
        'new_striker_id': int(new_batsman_id),
        'new_striker_name': new_batsman.player_name,
        'over_complete': over_complete,
        'innings_complete': innings_complete,
        'available_batsmen': fresh_available,
    })



# ── UNDO LAST BALL ──

@admin_required
@require_POST
def undo_ball_view(request, match_id):
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

    return JsonResponse({
        'success': True,
        'over_number': over.over_number,
        'bowler': bowler.player_name,
    })


# ── STEP 5: Start 2nd Innings ──

@admin_required
def start_second_innings(request, match_id):
    for key in ('innings_id', 'over_id', 'striker_id', 'non_striker_id'):
        request.session.pop(key, None)
    return redirect('start_innings', match_id=match_id)


# ── STEP 6: Match Result ──

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

    # Backfill MOM for old matches that completed before this feature existed
    if inn2 and inn2.status == 'COMPLETED':
        try:
            mom = match.man_of_the_match
        except Exception:
            award_man_of_the_match(match.id)
            try:
                mom = match.man_of_the_match
            except Exception:
                mom = None
    else:
        mom = None

    return render(request, 'match_scorecard.html', {
        'match': match,
        'sc1': sc1,
        'sc2': sc2,
        'winner': winner,
        'margin': margin,
        'mom': mom,
    })


# ── ADMIN LOGIN / LOGOUT ──

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

                    if player:
                        request.session['player_id'] = player.id
                        request.session['player_name'] = player.player_name
                    else:
                        request.session['player_id'] = 'guest'
                        request.session['player_name'] = 'Guest'

                    request.session['player_mobile'] = mobile

                    if next_param and url_has_allowed_host_and_scheme(next_param, allowed_hosts={request.get_host()}):
                        return redirect(next_param)
                    return redirect('home')

                if not password_ok and not error:
                    error = "Invalid mobile number or password."

    return render(request, 'player_login.html', {'error': error, 'next': next_param})


def send_otp_sms(mobile, otp_code):
    """
    Send OTP via Twilio SMS.
    Returns (True, None) on success or (False, error_message) on failure.
    """
    import requests as http_requests
    from django.conf import settings
    import logging
    logger = logging.getLogger(__name__)

    account_sid = getattr(settings, 'TWILIO_ACCOUNT_SID', None)
    auth_token  = getattr(settings, 'TWILIO_AUTH_TOKEN', None)
    from_number = getattr(settings, 'TWILIO_PHONE_NUMBER', None)

    if not all([account_sid, auth_token, from_number]):
        return False, "Twilio credentials not configured"

    try:
        # Make sure number has country code
        number = mobile.strip().replace(' ', '')
        if not number.startswith('+'):
            number = '+91' + number  # default to India country code

        url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"

        payload = {
            "To": number,
            "From": from_number,
            "Body": f"Your StrikeZone login OTP is {otp_code}. Valid for 5 minutes. Do not share with anyone.",
        }

        response = http_requests.post(
            url,
            data=payload,
            auth=(account_sid, auth_token),
            timeout=10
        )

        logger.info(f"[Twilio] Status: {response.status_code} | Response: {response.text}")
        data = response.json()

        if response.status_code == 201:
            return True, None
        else:
            error_msg = data.get('message', str(data))
            logger.error(f"[Twilio] Failed: {data}")
            return False, error_msg

    except Exception as e:
        logger.error(f"[Twilio] Exception: {e}")
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
                otp_code = f"{random.randint(100000, 999999):06d}"
                request.session['otp_mobile'] = mobile
                request.session['otp_code'] = otp_code
                request.session['otp_expires_at'] = (timezone.now() + timedelta(minutes=5)).isoformat()
                request.session['otp_attempts'] = 0

                sms_sent, sms_error = send_otp_sms(mobile, otp_code)

                if sms_sent:
                    messages.success(request, f"OTP sent to {mobile}. Valid for 5 minutes.")
                else:
                    messages.error(request, f"Could not send OTP. Reason: {sms_error}")
                    for key in ('otp_mobile', 'otp_code', 'otp_expires_at', 'otp_attempts'):
                        request.session.pop(key, None)
                    return render(request, 'player_request_otp.html')

                return redirect('player_verify_otp')

    return render(request, 'player_request_otp.html')


def player_verify_otp(request):
    mobile = request.session.get('otp_mobile')
    code = request.session.get('otp_code')
    expires_raw = request.session.get('otp_expires_at')
    attempts = request.session.get('otp_attempts', 0)

    if not mobile or not code or not expires_raw:
        messages.error(request, "OTP session expired. Please request a new code.")
        return redirect('player_request_otp')

    try:
        expires_at = datetime.fromisoformat(expires_raw)
    except Exception:
        expires_at = timezone.now() - timedelta(seconds=1)

    if timezone.now() > expires_at:
        for key in ('otp_mobile', 'otp_code', 'otp_expires_at', 'otp_attempts'):
            request.session.pop(key, None)
        messages.error(request, "OTP expired. Please request a new code.")
        return redirect('player_request_otp')

    if request.method == 'POST':
        user_code = (request.POST.get('otp', '') or '').strip()
        attempts += 1
        request.session['otp_attempts'] = attempts

        if attempts > 5:
            for key in ('otp_mobile', 'otp_code', 'otp_expires_at', 'otp_attempts'):
                request.session.pop(key, None)
            messages.error(request, "Too many incorrect attempts. Please request a new code.")
            return redirect('player_request_otp')

        if user_code != code:
            messages.error(request, "Incorrect OTP. Please try again.")
        else:
            for key in ('otp_mobile', 'otp_code', 'otp_expires_at', 'otp_attempts'):
                request.session.pop(key, None)

            guest, _ = GuestUser.objects.get_or_create(mobile_number=mobile)
            if guest.password == "":
                guest.password = make_password(f"otp-{mobile}")
            guest.is_mobile_verified = True
            guest.save()

            if request.user.is_authenticated:
                auth_logout(request)

            request.session.cycle_key()

            player = PlayerDetails.objects.filter(mobile_number=mobile).first()
            if player:
                request.session['player_id'] = player.id
                request.session['player_name'] = player.player_name
            else:
                request.session['player_id'] = 'guest'
                request.session['player_name'] = 'Guest'

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

def player_stats(request):
    player_id = request.session.get('player_id')
    if not player_id:
        return redirect('player_login')

    is_guest = str(player_id).startswith('guest') or not str(player_id).isdigit()

    if is_guest:
        return render(request, 'player_stats.html', {
            'player': None,
            'player_name': request.session.get('player_name', 'Guest'),
            'is_guest': True,
        })

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

            # Mobile is optional at model level, but if provided it must be unique
            if new_mobile and PlayerDetails.objects.filter(mobile_number=new_mobile).exclude(id=player.id).exists():
                messages.error(request, "This mobile number is already used by another player.")
                return redirect('player_stats')

            # Update core fields
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

    return render(request, 'player_stats.html', {
        'player': player,
        'is_guest': False,
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
def knockout_bracket(request, tournament_id):
    tournament = get_object_or_404(TournamentDetails, id=tournament_id)
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
def setup_knockout_stage(request, tournament_id):
    tournament = get_object_or_404(TournamentDetails, id=tournament_id)
    leaderboard = get_tournament_leaderboard(tournament)

    existing_stages = KnockoutStage.objects.filter(tournament=tournament).order_by('stage_order')

    if not existing_stages.exists():
        next_stage_code = None
    else:
        last_stage = existing_stages.last()
        next_stage_code = NEXT_STAGE.get(last_stage.stage)

    if not existing_stages.exists():
        available_teams = [entry['team'] for entry in leaderboard]
        available_labels = [f"TOP {entry['rank']} - {entry['team'].team_name}" for entry in leaderboard]
    else:
        last_stage = existing_stages.last()
        last_stage_matches = KnockoutMatch.objects.filter(stage=last_stage).order_by('match_number')
        available_teams = []
        available_labels = []
        for km in last_stage_matches:
            if km.winner:
                available_teams.append(km.winner)
                available_labels.append(
                    f"{last_stage.get_stage_display()} M{km.match_number} Winner - {km.winner.team_name}"
                )
            else:
                available_teams.append(None)
                available_labels.append(f"{last_stage.get_stage_display()} M{km.match_number} Winner - TBD")

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
        'available_teams': list(zip(available_teams, available_labels)),
        'next_stage_code': next_stage_code,
        'stage_choices': stage_choices,
        'existing_stages': existing_stages,
    })


@admin_required
def start_knockout_match(request, knockout_match_id):
    km = get_object_or_404(KnockoutMatch, id=knockout_match_id)
    tournament = km.stage.tournament

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
def link_knockout_matches(request, tournament_id):
    tournament = get_object_or_404(TournamentDetails, id=tournament_id)

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

def _is_tournament_complete(tournament):
    """
    Returns True if the tournament has concluded:
    - Has a Final knockout match that is completed, OR
    - All created matches have completed innings (for round-robin only tournaments)
    """
    from knockout.models import KnockoutStage, KnockoutMatch
    # Check for Final stage
    final_stage = KnockoutStage.objects.filter(tournament=tournament, stage='F').first()
    if final_stage:
        final_matches = KnockoutMatch.objects.filter(stage=final_stage)
        if final_matches.exists():
            return all(m.is_completed for m in final_matches)
        return False
    # No knockout — all matches completed
    all_matches = CreateMatch.objects.filter(tournament=tournament)
    if not all_matches.exists():
        return False
    for m in all_matches:
        inn2 = Innings.objects.filter(match=m, innings_number=2).first()
        if not inn2 or inn2.status != 'COMPLETED':
            return False
    return True


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

    # Players who batted in each innings
    batted_ids1 = set(b.batsman_id for b in batting_sc1)
    batted_ids2 = set(b.batsman_id for b in batting_sc2)

    # Yet to bat
    def get_yet_to_bat(tt, batted_ids):
        if not tt:
            return []
        return list(
            PlayerDetails.objects.filter(tournament_rosters__tournament_team=tt)
            .exclude(id__in=batted_ids)
            .distinct()
            .order_by('player_name')
        )

    # Figure out which team bats in which innings
    inn1_batting_tt = TournamentTeam.objects.filter(tournament=match.tournament, team=inn1.batting_team).first() if inn1 else None
    inn2_batting_tt = TournamentTeam.objects.filter(tournament=match.tournament, team=inn2.batting_team).first() if inn2 else None

    yet_to_bat1 = get_yet_to_bat(inn1_batting_tt, batted_ids1)
    yet_to_bat2 = get_yet_to_bat(inn2_batting_tt, batted_ids2)

    target = (inn1.total_runs + 1) if inn1 and inn2 else None

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

        # Yet to bat
        batting_tt = TournamentTeam.objects.filter(tournament=match.tournament, team=inn.batting_team).first()
        batted_ids = set(b['name'] for b in batting)
        yet_to_bat = []
        if batting_tt:
            for p in PlayerDetails.objects.filter(tournament_rosters__tournament_team=batting_tt).distinct():
                if p.player_name not in batted_ids:
                    yet_to_bat.append(p.player_name)

        return {
            'team': str(inn.batting_team),
            'bowling_team': str(inn.bowling_team),
            'total_runs': inn.total_runs,
            'total_wickets': inn.total_wickets,
            'overs': str(inn.overs_completed),
            'extras': inn.extras,
            'status': inn.status,
            'batting': batting,
            'bowling': bowling,
            'over_balls': over_balls,
            'current_over_num': current_over.over_number if current_over else None,
            'current_bowler': current_over.bowler.player_name if current_over else None,
            'yet_to_bat': yet_to_bat,
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