"""
simulate_matches.py
--------------------
Generates realistic, fully-played matches (ball by ball, real scorecards,
real Man-of-the-Match / tournament awards) so a tournament looks populated
for a demo — without manually scoring every ball by hand.

It uses the SAME service functions the live scoring screen uses
(strikezone/services.py: begin_innings, start_over, record_ball) so every
row it creates is exactly as valid and consistent as if a human had scored
it live. Man-of-the-Match and tournament awards are computed by calling the
same award functions the app already calls after a real match finishes.

USAGE
-----
    # Simulate every not-yet-played match in a tournament
    python manage.py simulate_matches --tournament 3

    # Simulate just one specific match
    python manage.py simulate_matches --match 15

    # Simulate only the next 2 pending matches in a tournament
    python manage.py simulate_matches --tournament 3 --count 2

    # Force-overwrite a match that was already played/simulated
    python manage.py simulate_matches --match 15 --force

    # Quick short matches for a faster demo (e.g. 5 overs instead of the
    # tournament's real over count)
    python manage.py simulate_matches --tournament 3 --overs 5

    # Reproducible results (same random outcome every run)
    python manage.py simulate_matches --tournament 3 --seed 42
"""

import math
import random

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from tournaments.models import TournamentDetails, StartTournament
from teams.models import TournamentTeam, TournamentRoster
from matches.models import CreateMatch, MatchStart, MatchResult, ManOfTheMatch
from scoring.models import Innings, Over, Ball, BattingScorecard, BowlingScorecard

from strikezone.services import begin_innings, start_over, record_ball
from strikezone.views_awards import award_man_of_the_match, award_tournament_awards
from strikezone.views_knockout import auto_advance_knockout


# ── Ball-outcome probabilities (tuned to look like a normal T20/ODI game,
#    not chaotic random noise) ──────────────────────────────────────────
BASE_OUTCOME_WEIGHTS = {
    "DOT":    35,
    "1":      25,
    "2":       8,
    "3":       1,
    "4":      10,
    "6":       6,
    "WICKET":  7,
    "WIDE":    5,
    "NO_BALL": 3,
}

# ── Match-level "pitch" — chosen once per match, shifts the whole game
#    toward a high-scoring shootout or a low-scoring grind. Weighted so
#    most matches are balanced, with occasional extremes either way. ──
PITCH_PROFILES = {
    "BATTING_PARADISE": {**BASE_OUTCOME_WEIGHTS, "DOT": 22, "1": 24, "4": 18, "6": 12, "WICKET": 4},
    "BALANCED":         dict(BASE_OUTCOME_WEIGHTS),
    "BOWLING_FRIENDLY": {**BASE_OUTCOME_WEIGHTS, "DOT": 46, "1": 22, "4": 5, "6": 2, "WICKET": 11},
}
PITCH_CHOICE_WEIGHTS = {"BATTING_PARADISE": 25, "BALANCED": 50, "BOWLING_FRIENDLY": 25}

# ── Per-player "form" — rolled once per player per match, separately for
#    batting and bowling, so the same player can smash a 90 one match and
#    a duck the next. Most land near 1.0 (average); a few land as a
#    stand-out performance or an off day. ──────────────────────────────
FORM_LEVELS  = [0.5, 0.7, 0.85, 1.0, 1.15, 1.35, 1.6, 2.0]
FORM_WEIGHTS = [5,   12,  20,   26,  18,   10,   6,   3]


def roll_form():
    return random.choices(FORM_LEVELS, weights=FORM_WEIGHTS, k=1)[0]


WICKET_TYPE_WEIGHTS = {
    "CAUGHT":            40,
    "BOWLED":            30,
    "LBW":               10,
    "STUMPED":           10,
    "RUN_OUT":            5,
    "CAUGHT_AND_BOWLED":  3,
    "HIT_WICKET":         2,
}


def weighted_choice(weights: dict):
    keys = list(weights.keys())
    vals = list(weights.values())
    return random.choices(keys, weights=vals, k=1)[0]


def matchup_weights(pitch_weights, batter_form, bowler_form):
    """
    Blends the pitch's base odds with how this specific batter's form
    stacks up against this specific bowler's form for this one ball.
    battle > 1  → batter is winning the contest right now (more boundaries,
                  fewer dots/wickets). battle < 1 → bowler is on top.
    """
    battle = batter_form / bowler_form
    battle = max(0.4, min(battle, 2.5))  # keep it believable, not absurd

    w = dict(pitch_weights)
    w["4"]      = w["4"] * battle
    w["6"]      = w["6"] * (battle ** 1.4)
    w["DOT"]    = w["DOT"] / battle
    w["WICKET"] = w["WICKET"] / battle
    return w


class Command(BaseCommand):
    help = "Simulate realistic, fully completed matches with real ball-by-ball data for demo purposes."

    def add_arguments(self, parser):
        parser.add_argument("--tournament", type=int, help="Tournament ID — simulate its pending matches.")
        parser.add_argument("--match", type=int, help="Simulate one specific match ID.")
        parser.add_argument("--count", type=int, default=None, help="Limit how many pending matches to simulate.")
        parser.add_argument("--overs", type=int, default=None, help="Override overs-per-innings for a faster demo.")
        parser.add_argument("--force", action="store_true", help="Wipe and re-simulate a match that already has a result.")
        parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducible results.")

    # ─────────────────────────────────────────────────────────────
    def handle(self, *args, **opts):
        if opts["seed"] is not None:
            random.seed(opts["seed"])

        if not opts["tournament"] and not opts["match"]:
            raise CommandError("Pass --tournament <id> or --match <id>.")

        if opts["match"]:
            matches = CreateMatch.objects.filter(id=opts["match"])
            if not matches.exists():
                raise CommandError(f"No match with id={opts['match']}.")
        else:
            tournament = TournamentDetails.objects.filter(id=opts["tournament"]).first()
            if not tournament:
                raise CommandError(f"No tournament with id={opts['tournament']}.")
            matches = CreateMatch.objects.filter(tournament=tournament).order_by("match_date", "id")
            if not opts["force"]:
                matches = matches.exclude(result__isnull=False)
            if opts["count"]:
                matches = matches[: opts["count"]]

            # Make sure the tournament is marked started, or start_over()/live
            # pages would otherwise treat it as not-yet-live.
            start_obj, _ = StartTournament.objects.get_or_create(tournament=tournament)
            if not start_obj.is_started:
                start_obj.is_started = True
                start_obj.save()

        matches = list(matches)
        if not matches:
            self.stdout.write(self.style.WARNING("Nothing to simulate — no pending matches found."))
            return

        for match in matches:
            try:
                self.simulate_match(match, overs_override=opts["overs"], force=opts["force"])
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"  ✗ Match {match.id} ({match}) failed: {exc}"))

    # ─────────────────────────────────────────────────────────────
    @transaction.atomic
    def simulate_match(self, match, overs_override=None, force=False):
        existing_result = MatchResult.objects.filter(match=match).first()
        if existing_result and not force:
            self.stdout.write(f"  – Skipping match {match.id} ({match}) — already has a result. Use --force to redo.")
            return

        if existing_result or hasattr(match, "match_start"):
            self._wipe_match_data(match)

        team1, team2 = match.team1, match.team2
        roster1 = self._get_roster(match.tournament, team1)
        roster2 = self._get_roster(match.tournament, team2)

        if len(roster1) < 2 or len(roster2) < 2:
            self.stdout.write(self.style.WARNING(
                f"  – Skipping match {match.id} ({match}) — a team needs at least 2 players in its roster."
            ))
            return

        # ── Toss ──
        toss_winner = random.choice([team1, team2])
        decision = random.choice(["BAT", "BOWL"])
        match_start = MatchStart.objects.create(
            match=match, toss_winner=toss_winner, decision=decision,
        )
        if overs_override:
            match_start.custom_overs = overs_override
            match_start.save(update_fields=["custom_overs"])

        batting_team_1 = match_start.batting_team
        bowling_team_1 = match_start.bowling_team
        rosters = {team1.id: roster1, team2.id: roster2}

        # ── Pitch + per-player form, rolled once for the whole match ──
        pitch = random.choices(list(PITCH_PROFILES), weights=list(PITCH_CHOICE_WEIGHTS.values()), k=1)[0]
        pitch_weights = PITCH_PROFILES[pitch]
        all_players = roster1 + roster2
        batting_form = {p.id: roll_form() for p in all_players}
        bowling_form = {p.id: roll_form() for p in all_players}

        # ── Innings 1 ──
        innings1 = begin_innings(match_start, innings_number=1)
        self._play_innings(innings1, rosters[batting_team_1.id], rosters[bowling_team_1.id],
                            pitch_weights, batting_form, bowling_form)

        # ── Innings 2 (chasing) ──
        innings1.refresh_from_db()
        innings2 = begin_innings(match_start, innings_number=2, target=innings1.total_runs + 1)
        self._play_innings(innings2, rosters[bowling_team_1.id], rosters[batting_team_1.id],
                            pitch_weights, batting_form, bowling_form)
        innings2.refresh_from_db()

        # ── Result + awards (mirrors what record_ball_view does live) ──
        if innings2.total_runs > innings1.total_runs:
            winner, result_type = innings2.batting_team, "WIN_BY_WICKETS"
            margin = 10 - innings2.total_wickets
            summary = f"{winner.team_name} won by {margin} wicket{'s' if margin != 1 else ''}"
        elif innings1.total_runs > innings2.total_runs:
            winner, result_type = innings1.batting_team, "WIN_BY_RUNS"
            margin = innings1.total_runs - innings2.total_runs
            summary = f"{winner.team_name} won by {margin} run{'s' if margin != 1 else ''}"
        else:
            winner, result_type, margin = None, "TIE", None
            summary = "Match Tied"

        MatchResult.objects.create(
            match=match, winner=winner, result_type=result_type,
            win_margin=margin, result_summary=summary,
        )
        auto_advance_knockout(match.id)
        award_man_of_the_match(match.id)
        award_tournament_awards(match.tournament_id)

        self.stdout.write(self.style.SUCCESS(
            f"  ✓ Match {match.id} [{pitch}]: {team1.team_name} {innings1.total_runs}/{innings1.total_wickets} "
            f"vs {team2.team_name} {innings2.total_runs}/{innings2.total_wickets} — {summary}"
        ))

    # ─────────────────────────────────────────────────────────────
    def _get_roster(self, tournament, team):
        tt = TournamentTeam.objects.filter(tournament=tournament, team=team).first()
        if not tt:
            return []
        players = list(TournamentRoster.objects.filter(tournament_team=tt).select_related("player"))
        random.shuffle(players)
        return [r.player for r in players]

    def _wipe_match_data(self, match):
        """Same cleanup restart_match() does, so --force starts from a clean slate."""
        for innings in Innings.objects.filter(match=match):
            for over in innings.overs.all():
                over.balls.all().delete()
            innings.overs.all().delete()
            BattingScorecard.objects.filter(innings=innings).delete()
            BowlingScorecard.objects.filter(innings=innings).delete()
        Innings.objects.filter(match=match).delete()
        ManOfTheMatch.objects.filter(match=match).delete()
        MatchResult.objects.filter(match=match).delete()
        MatchStart.objects.filter(match=match).delete()

    # ─────────────────────────────────────────────────────────────
    def _play_innings(self, innings, batting_order, bowling_pool, pitch_weights, batting_form, bowling_form):
        max_overs = innings.max_overs
        max_overs_per_bowler = max(1, math.ceil(max_overs / 5))
        overs_bowled_count = {p.id: 0 for p in bowling_pool}

        next_batsman_idx = 2
        striker, non_striker = batting_order[0], batting_order[1]
        prev_bowler = None

        over_number = 1
        all_out = False

        while True:
            innings.refresh_from_db()
            if innings.status == "COMPLETED" or over_number > max_overs or all_out:
                break

            bowler = self._pick_bowler(bowling_pool, prev_bowler, overs_bowled_count, max_overs_per_bowler)
            over = start_over(innings, over_number, bowler)
            prev_bowler = bowler

            while True:
                innings.refresh_from_db()
                over.refresh_from_db()
                if innings.status == "COMPLETED" or over.is_completed:
                    break

                ball_weights = matchup_weights(pitch_weights, batting_form[striker.id], bowling_form[bowler.id])
                outcome = weighted_choice(ball_weights)
                runs_off_bat, extra_runs, ball_type, is_wicket = 0, 0, "NORMAL", False
                wicket_type, fielder = "NONE", None

                if outcome == "DOT":
                    pass
                elif outcome in ("1", "2", "3", "4", "6"):
                    runs_off_bat = int(outcome)
                elif outcome == "WIDE":
                    ball_type, extra_runs = "WIDE", 1
                elif outcome == "NO_BALL":
                    ball_type, extra_runs = "NO_BALL", 1
                elif outcome == "WICKET":
                    is_wicket = True
                    wicket_type = weighted_choice(WICKET_TYPE_WEIGHTS)
                    if wicket_type in ("CAUGHT", "STUMPED", "RUN_OUT"):
                        fielding_pool = [p for p in bowling_pool if p.id != bowler.id] or bowling_pool
                        fielder = random.choice(fielding_pool)

                ball = record_ball(
                    over=over, batsman=striker,
                    runs_off_bat=runs_off_bat, extra_runs=extra_runs,
                    ball_type=ball_type, is_wicket=is_wicket,
                    wicket_type=wicket_type,
                    player_dismissed=striker if is_wicket else None,
                    fielder=fielder,
                )

                if is_wicket:
                    if next_batsman_idx < len(batting_order):
                        striker = batting_order[next_batsman_idx]
                        next_batsman_idx += 1
                    else:
                        all_out = True
                        innings.refresh_from_db()
                        if innings.status != "COMPLETED":
                            innings.status = "COMPLETED"
                            innings.save(update_fields=["status"])
                        if not over.is_completed:
                            over.is_completed = True
                            over.save(update_fields=["is_completed"])
                        break
                elif ball.is_legal_ball and runs_off_bat % 2 == 1:
                    striker, non_striker = non_striker, striker

            overs_bowled_count[bowler.id] += 1

            innings.refresh_from_db()
            if innings.status == "COMPLETED" or all_out:
                break
            # End of over — ends swap ends
            striker, non_striker = non_striker, striker
            over_number += 1

    def _pick_bowler(self, pool, prev_bowler, overs_count, max_per_bowler):
        eligible = [p for p in pool if p.id != getattr(prev_bowler, "id", None) and overs_count[p.id] < max_per_bowler]
        if not eligible:
            eligible = [p for p in pool if overs_count[p.id] < max_per_bowler]
        if not eligible:
            eligible = pool
        return random.choice(eligible)