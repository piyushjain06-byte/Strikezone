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
from .views_core import admin_required
from subscriptions.decorators import require_plan

@require_plan('pro_plus')
def manage_cricket(request):
    is_admin = (
        (request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser))
        or bool(request.session.get('player_mobile'))
    )

    tournament_form = TournamentForm()
    team_form = TeamForm()
    player_form = PlayerForm()
    active_tab = 'tournament'

    if request.method == "POST":

        if "tournament_submit" in request.POST:
            tournament_form = TournamentForm(request.POST)
            if tournament_form.is_valid():
                tournament = tournament_form.save(commit=False)
                # Track who created it
                if request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser):
                    tournament.created_by_admin = request.user
                elif request.session.get('player_id') and request.session['player_id'] != 'guest':
                    try:
                        tournament.created_by_player_id = request.session['player_id']
                    except Exception:
                        pass
                tournament.save()
                messages.success(request, "Tournament created successfully!")
                tournament_form = TournamentForm()
            active_tab = 'tournament'
        elif "team_submit" in request.POST:
            team_form = TeamForm(request.POST)
            if team_form.is_valid():
                tournament = team_form.cleaned_data["tournament"]
                # Ownership check
                from subscriptions.decorators import _is_privileged, _player_owns_tournament
                if not _is_privileged(request) and not _player_owns_tournament(request, tournament.id):
                    messages.warning(request, 'You can only add teams to tournaments you have created.')
                else:
                    team_code = (team_form.cleaned_data.get("team_code") or "").strip()
                    team_name = (team_form.cleaned_data.get("team_name") or "").strip()
                    team_created_date = team_form.cleaned_data.get("team_created_date")

                    team = None
                    if team_code:
                        team = TeamDetails.objects.filter(team_code__iexact=team_code).first()
                        if not team:
                            messages.error(request, f"No team found with Team ID '{team_code}'.")
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
                # Ownership check
                from subscriptions.decorators import _is_privileged, _player_owns_tournament
                if not _is_privileged(request) and not _player_owns_tournament(request, tournament.id):
                    messages.warning(request, 'You can only add players to tournaments you have created.')
                else:
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

                    # mobile_number is always present (required by form)
                    player = PlayerDetails.objects.filter(mobile_number=mobile_number).first()

                    if player:
                        # Existing player found by mobile — update name only if a new one was given
                        if player_name and player.player_name != player_name:
                            player.player_name = player_name
                            player.save(update_fields=["player_name"])
                    else:
                        # New player — auto-generate name from mobile if admin left name blank
                        if not player_name:
                            player_name = f"Player {mobile_number}"
                        player = PlayerDetails.objects.create(
                            player_name=player_name,
                            mobile_number=mobile_number,
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

    # Filter to owned + hired tournaments for pro_plus players
    from subscriptions.decorators import _is_privileged
    if _is_privileged(request):
        tournaments_qs = TournamentDetails.objects.all()
    else:
        pid = request.session.get('player_id')
        if pid and pid != 'guest':
            from tournaments.models import TournamentHire
            from django.db.models import Q
            hired_ids = TournamentHire.objects.filter(
                hired_player_id=pid
            ).values_list('tournament_id', flat=True)
            tournaments_qs = TournamentDetails.objects.filter(
                Q(created_by_player_id=pid) | Q(id__in=hired_ids)
            )
        else:
            tournaments_qs = TournamentDetails.objects.none()
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

    # Filter tournament dropdown for pro_plus players (owned + hired)
    if not _is_privileged(request):
        team_form.fields['tournament'].queryset = tournaments_qs
        player_form.fields['tournament'].queryset = tournaments_qs

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
@require_plan('pro_plus')
def create_match(request):
    if request.method == "POST":
        form = MatchForm(request.POST)
        if form.is_valid():
            # Ownership check for pro_plus players
            from subscriptions.decorators import _is_privileged, _player_owns_tournament
            tournament_id = form.cleaned_data.get('tournament').id if form.cleaned_data.get('tournament') else None
            if tournament_id and not _is_privileged(request):
                if not _player_owns_tournament(request, tournament_id):
                    messages.warning(request, 'You can only create matches in tournaments you have created.')
                    return redirect('upgrade_plan')
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
@require_plan('pro_plus')
def start_tournament(request):
    from subscriptions.decorators import _is_privileged, _player_owns_tournament
    # Filter tournaments list for pro_plus players
    if _is_privileged(request):
        tournaments_qs = TournamentDetails.objects.all()
    else:
        pid = request.session.get('player_id')
        if pid and pid != 'guest':
            tournaments_qs = TournamentDetails.objects.filter(created_by_player_id=pid)
        else:
            tournaments_qs = TournamentDetails.objects.none()

    if request.method == "POST":
        tournament_id = request.POST.get("tournament_id")
        tournament = get_object_or_404(TournamentDetails, id=tournament_id)
        # Ownership check
        if not _is_privileged(request) and not _player_owns_tournament(request, tournament.id):
            messages.warning(request, 'You can only start tournaments you have created.')
            return redirect('upgrade_plan')
        start_obj, created = StartTournament.objects.get_or_create(tournament=tournament)
        start_obj.is_started = True
        start_obj.save()
        messages.success(request, f"{tournament.tournament_name} has been started successfully!")
        return redirect("match_start")
    return render(request, "start_tournament.html", {"tournaments": tournaments_qs})

def edit_teams_view(request, tournament_id):
    """Show all teams and players for a tournament — allows moving players between teams."""
    from django.shortcuts import get_object_or_404
    from tournaments.models import TournamentDetails
    from teams.models import TournamentTeam, TournamentRoster
    from subscriptions.decorators import _is_privileged, _player_owns_tournament

    tournament = get_object_or_404(TournamentDetails, id=tournament_id)

    # Only accessible if teams_editable is True
    if not tournament.teams_editable:
        messages.warning(request, 'Team editing is not enabled for this tournament.')
        return redirect('tournamentdetails', id=tournament_id)

    # Permission: creator, hired staff, CEO, employee
    if not _is_privileged(request) and not _player_owns_tournament(request, tournament_id):
        messages.warning(request, 'You do not have permission to edit teams for this tournament.')
        return redirect('tournamentdetails', id=tournament_id)

    teams_data = []
    for tt in TournamentTeam.objects.filter(tournament=tournament).select_related('team').order_by('team__team_name'):
        roster = TournamentRoster.objects.filter(
            tournament_team=tt
        ).select_related('player').order_by('player__player_name')
        teams_data.append({'tournament_team': tt, 'team': tt.team, 'roster': list(roster)})

    return render(request, 'edit_teams.html', {
        'tournament': tournament,
        'teams_data': teams_data,
    })


def move_player_view(request, tournament_id):
    """AJAX: Move a player from one team to another within a tournament."""
    from django.shortcuts import get_object_or_404
    from tournaments.models import TournamentDetails
    from teams.models import TournamentTeam, TournamentRoster
    from subscriptions.decorators import _is_privileged, _player_owns_tournament
    import json

    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    tournament = get_object_or_404(TournamentDetails, id=tournament_id)

    if not tournament.teams_editable:
        return JsonResponse({'error': 'Team editing is not enabled.'}, status=403)

    if not _is_privileged(request) and not _player_owns_tournament(request, tournament_id):
        return JsonResponse({'error': 'Not authorised.'}, status=403)

    try:
        data = json.loads(request.body)
    except Exception:
        data = request.POST

    player_id   = data.get('player_id')
    to_team_id  = data.get('to_team_id')  # TournamentTeam id

    if not player_id or not to_team_id:
        return JsonResponse({'error': 'player_id and to_team_id required'}, status=400)

    try:
        roster = TournamentRoster.objects.select_related(
            'tournament_team__team', 'player'
        ).get(tournament_id=tournament_id, player_id=player_id)
    except TournamentRoster.DoesNotExist:
        return JsonResponse({'error': 'Player not found in this tournament.'}, status=404)

    try:
        new_tt = TournamentTeam.objects.get(id=to_team_id, tournament=tournament)
    except TournamentTeam.DoesNotExist:
        return JsonResponse({'error': 'Target team not found.'}, status=404)

    if roster.tournament_team_id == new_tt.id:
        return JsonResponse({'error': 'Player is already in that team.'}, status=400)

    old_team_name = roster.tournament_team.team.team_name
    new_team_name = new_tt.team.team_name
    player_name   = roster.player.player_name

    # Move: update tournament_team (unique constraint allows this since we're changing teams)
    roster.tournament_team = new_tt
    roster.save(update_fields=['tournament_team', 'updated_at'])

    return JsonResponse({
        'success': True,
        'message': f'{player_name} moved from {old_team_name} to {new_team_name}.',
        'player_name': player_name,
        'old_team': old_team_name,
        'new_team': new_team_name,
    })