from django.shortcuts import redirect, render, get_object_or_404
from django.http import JsonResponse
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.views.decorators.http import require_POST
from django.db import models as django_models
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.urls import reverse

from tournaments.models import TournamentDetails, StartTournament
from teams.models import TeamDetails, PlayerDetails
from matches.models import CreateMatch, MatchStart, MatchResult
from scoring.models import Innings, Over, Ball, BattingScorecard, BowlingScorecard
from knockout.models import KnockoutStage, KnockoutMatch
from accounts.models import GuestUser

from strikezone.forms import MatchForm, TournamentForm, TeamForm, PlayerForm
from strikezone.services import begin_innings, start_over, record_ball

import json
from datetime import date, datetime


# ── ADMIN ONLY DECORATOR ──
def admin_required(view_func):
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser):
            return view_func(request, *args, **kwargs)
        messages.error(request, "You must be logged in as an admin to access this page.")
        return redirect('admin_login')
    wrapper.__name__ = view_func.__name__
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

        top_batsmen = (
            BattingScorecard.objects
            .filter(innings_id__in=all_innings_ids)
            .values('batsman__id', 'batsman__player_name', 'batsman__team__team_name', 'batsman__photo')
            .annotate(
                total_runs=Sum('runs'),
                total_balls=Sum('balls_faced'),
                total_fours=Sum('fours'),
                total_sixes=Sum('sixes'),
            )
            .order_by('-total_runs')[:8]
        )

        top_bowlers = (
            BowlingScorecard.objects
            .filter(innings_id__in=all_innings_ids)
            .values('bowler__id', 'bowler__player_name', 'bowler__team__team_name', 'bowler__photo')
            .annotate(
                total_wickets=Sum('wickets'),
                total_runs_given=Sum('runs_given'),
            )
            .order_by('-total_wickets', 'total_runs_given')[:8]
        )

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
    teams = tournament.teams.all()
    return render(request, 'tournamentdetails.html', {'tournament': tournament, 'teams': teams})


def teamdetails(request, tournament_id, team_id):
    tournament = get_object_or_404(TournamentDetails, id=tournament_id)
    team = get_object_or_404(TeamDetails, id=team_id, tournament=tournament)
    players = team.players.all()
    return render(request, 'teamdetails.html', {'tournament': tournament, 'team': team, 'players': players})


def manage_cricket(request):
    is_admin = request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser)

    if not is_admin:
        messages.error(request, "You must be logged in as an admin to access this page.")
        return redirect('admin_login')

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
                team_form.save()
                messages.success(request, "Team created successfully!")
                team_form = TeamForm()
            active_tab = 'team'
        elif "player_submit" in request.POST:
            player_form = PlayerForm(request.POST, request.FILES)
            if player_form.is_valid():
                player_form.save()
                messages.success(request, "Player created successfully!")
                player_form = PlayerForm()
            active_tab = 'player'

    tournaments_qs = TournamentDetails.objects.all().prefetch_related('teams__players')
    tournament_progress = []
    for t in tournaments_qs:
        teams = list(t.teams.all())
        teams_added = len(teams)
        teams_needed = t.number_of_teams
        teams_remaining = max(0, teams_needed - teams_added)
        team_data = []
        for team in teams:
            players = list(team.players.all())
            team_data.append({'team': team, 'players': players, 'player_count': len(players)})
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
        'teams': TeamDetails.objects.select_related('tournament').all(),
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
    teams = TeamDetails.objects.filter(tournament_id=tournament_id)
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

    batsmen = PlayerDetails.objects.filter(team=batting_team)
    bowlers = PlayerDetails.objects.filter(team=bowling_team)

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
    bowling_team_players = PlayerDetails.objects.filter(team=innings.bowling_team)

    dismissed_ids = list(
        BattingScorecard.objects.filter(innings=innings)
        .exclude(status='NOT_OUT')
        .values_list('batsman_id', flat=True)
    )
    currently_in = [int(striker_id), int(non_striker_id)] if striker_id and non_striker_id else []
    excluded_ids = list(set(dismissed_ids + currently_in))
    available_batsmen = PlayerDetails.objects.filter(team=innings.batting_team).exclude(id__in=excluded_ids)

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

    ball = record_ball(
        over=over,
        batsman=batsman,
        runs_off_bat=runs_off_bat,
        extra_runs=extra_runs,
        ball_type=ball_type,
        is_wicket=is_wicket,
        wicket_type=wicket_type,
        player_dismissed=player_dismissed,
    )

    if ball.is_legal_ball and (runs_off_bat % 2 == 1):
        request.session['striker_id'], request.session['non_striker_id'] = (
            int(non_striker_id), int(striker_id)
        )

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

    legal_balls = over.balls.filter(is_legal_ball=True).count()
    over_complete = over.is_completed
    innings_complete = innings.status == "COMPLETED"

    if over_complete and not innings_complete:
        s = request.session.get('striker_id')
        ns = request.session.get('non_striker_id')
        request.session['striker_id'] = ns
        request.session['non_striker_id'] = s

    current_striker = PlayerDetails.objects.filter(id=request.session.get('striker_id')).first()
    current_non_striker = PlayerDetails.objects.filter(id=request.session.get('non_striker_id')).first()

    needs_new_batsman = is_wicket and not innings_complete

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

    return JsonResponse({
        'success': True,
        'new_striker_id': int(new_batsman_id),
        'new_striker_name': new_batsman.player_name,
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
    teams = TeamDetails.objects.filter(tournament=tournament)
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

    return render(request, 'tournament_history.html', {
        'tournament': tournament,
        'match_data': match_data,
        'leaderboard': leaderboard,
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
        batting = BattingScorecard.objects.filter(innings=innings).order_by('batting_position').select_related('batsman', 'batsman__team')
        bowling = BowlingScorecard.objects.filter(innings=innings).select_related('bowler', 'bowler__team')
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

    return render(request, 'match_scorecard.html', {
        'match': match,
        'sc1': sc1,
        'sc2': sc2,
        'winner': winner,
        'margin': margin,
    })


# ── ADMIN LOGIN / LOGOUT ──

def admin_login(request):
    if request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser):
        return redirect('manage_cricket')

    error = None
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        user = authenticate(request, username=username, password=password)
        if user is not None and (user.is_staff or user.is_superuser):
            auth_login(request, user)
            return redirect('manage_cricket')
        elif user is not None:
            error = "Your account does not have admin privileges."
        else:
            error = "Invalid username or password."

    return render(request, 'admin_login.html', {'error': error})


def admin_logout(request):
    auth_logout(request)
    return redirect('admin_login')


# ── PLAYER LOGIN ──

def player_login(request):
    if request.session.get('player_id'):
        return redirect('player_stats')

    error = None
    if request.method == 'POST':
        mobile = request.POST.get('mobile', '').strip()
        password = request.POST.get('password', '').strip()

        PLAYER_PASSWORD = "cricket123"

        if password != PLAYER_PASSWORD:
            error = "Incorrect password."
        else:
            player = PlayerDetails.objects.filter(mobile_number=mobile).first()
            if player:
                request.session['player_id'] = player.id
                request.session['player_name'] = player.player_name
                return redirect('player_stats')
            else:
                request.session['player_id'] = 'guest'
                request.session['player_name'] = 'Guest'
                request.session['player_mobile'] = mobile
                return redirect('player_stats')

    return render(request, 'player_login.html', {'error': error})


def player_register(request):
    if request.session.get('player_id'):
        return redirect('player_stats')

    error   = None
    success = None

    if request.method == 'POST':
        mobile   = request.POST.get('mobile', '').strip()
        password = request.POST.get('password', '').strip()
        confirm  = request.POST.get('confirm_password', '').strip()

        if not mobile or not password or not confirm:
            error = "All fields are required."
        elif len(mobile) < 10:
            error = "Enter a valid mobile number (at least 10 digits)."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        elif password != confirm:
            error = "Passwords do not match."
        elif PlayerDetails.objects.filter(mobile_number=mobile).exists():
            error = "This number is linked to a player account. Please login directly."
        elif GuestUser.objects.filter(mobile_number=mobile).exists():
            error = "An account with this mobile number already exists. Please login."
        else:
            GuestUser.objects.create(mobile_number=mobile, password=password)
            success = "Account created! You can now login."

    return render(request, 'player_register.html', {'error': error, 'success': success})


def player_logout(request):
    for key in ('player_id', 'player_name', 'player_mobile'):
        request.session.pop(key, None)
    return redirect('player_login')


# ── PLAYER STATS ──

def player_stats(request):
    player_id = request.session.get('player_id')
    if not player_id:
        return redirect('player_login')

    if str(player_id).startswith('guest') or not str(player_id).isdigit():
        return render(request, 'player_stats.html', {
            'player': None,
            'player_name': request.session.get('player_name', 'Guest'),
            'is_guest': True,
        })

    player = get_object_or_404(PlayerDetails, id=int(player_id))

    # Handle photo upload
    if request.method == 'POST' and request.FILES.get('photo'):
        player.photo = request.FILES['photo']
        player.save()
        messages.success(request, 'Profile photo updated!')
        return redirect('player_stats')

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

    recent_innings = batting_entries.order_by('-innings__match__match_date')[:5]
    recent_bowling = bowling_entries.order_by('-innings__match__match_date')[:5]

    return render(request, 'player_stats.html', {
        'player': player,
        'is_guest': False,
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
    })


# ══════════════════════════════════════════════
# KNOCKOUT BRACKET VIEWS
# ══════════════════════════════════════════════

def get_tournament_leaderboard(tournament):
    max_overs = tournament.number_of_overs
    teams = TeamDetails.objects.filter(tournament=tournament)
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