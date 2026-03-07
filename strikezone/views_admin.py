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
@require_plan('pro_plus')
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
@require_plan('pro_plus')
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