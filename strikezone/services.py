"""
match_engine.py
---------------
Service layer to control match flow. Call these functions from your views/admin.

Usage flow:
    1. begin_innings(match_start)               → creates Innings 1
    2. start_over(innings, over_number, bowler) → creates Over
    3. record_ball(over, data_dict)             → records each ball, auto-updates scores
    4. complete_innings(innings)                → finalise innings, set target for 2nd
    5. begin_innings_2(match_start, innings1)   → creates Innings 2 with target
    6. compute_result(match)                    → saves MatchResult
"""
from django.db import transaction
from scoring.models import Innings, Over, Ball, BattingScorecard, BowlingScorecard
from matches.models import MatchResult

# ─────────────────────────────────────────────
# 1. Begin Innings
# ─────────────────────────────────────────────
def begin_innings(match_start, innings_number=1, target=None):
    """
    Creates and returns an Innings object.

    Args:
        match_start (MatchStart): The started match instance.
        innings_number (int): 1 or 2.
        target (int|None): Required for 2nd innings.

    Returns:
        Innings
    """
    match = match_start.match

    if innings_number == 1:
        batting_team = match_start.batting_team
        bowling_team = match_start.bowling_team
    else:
        # 2nd innings — swap teams
        batting_team = match_start.bowling_team
        bowling_team = match_start.batting_team

    innings = Innings.objects.create(
        match=match,
        innings_number=innings_number,
        batting_team=batting_team,
        bowling_team=bowling_team,
        status="IN_PROGRESS",
        target=target
    )
    return innings


# ─────────────────────────────────────────────
# 2. Start Over
# ─────────────────────────────────────────────
def start_over(innings, over_number, bowler):
    """
    Creates an Over for the given innings.

    Args:
        innings (Innings): Current innings.
        over_number (int): 1-based over number.
        bowler (PlayerDetails): The bowler for this over.

    Returns:
        Over
    """
    if innings.status == "COMPLETED":
        raise ValueError("Cannot start a new over. Innings is already completed.")

    if over_number > innings.max_overs:
        raise ValueError(f"Over {over_number} exceeds max overs ({innings.max_overs}).")

    over = Over.objects.create(
        innings=innings,
        over_number=over_number,
        bowler=bowler
    )
    return over


# ─────────────────────────────────────────────
# 3. Record Ball
# ─────────────────────────────────────────────
@transaction.atomic
def record_ball(over, batsman, runs_off_bat=0, extra_runs=0,
                ball_type="NORMAL", is_wicket=False,
                wicket_type="NONE", player_dismissed=None, fielder=None):
    """
    Records a single delivery and auto-updates all scorecards.

    Args:
        over (Over): Current over.
        batsman (PlayerDetails): Batsman on strike.
        runs_off_bat (int): Runs scored off the bat.
        extra_runs (int): Wide/noball/bye/legbye extras.
        ball_type (str): NORMAL | WIDE | NO_BALL | BYE | LEG_BYE
        is_wicket (bool): Was a wicket taken?
        wicket_type (str): BOWLED | CAUGHT | LBW | RUN_OUT | STUMPED | HIT_WICKET | CAUGHT_AND_BOWLED
        player_dismissed (PlayerDetails|None): Who got out.
        fielder (PlayerDetails|None): Fielder who took catch/runout/stumping.

    Returns:
        Ball
    """
    innings = over.innings

    if innings.status == "COMPLETED":
        raise ValueError("Innings is already completed. Cannot record more balls.")

    if over.is_completed:
        raise ValueError("Over is already completed. Please start a new over.")

    # Ball number = total balls in this over (legal + illegal) + 1
    ball_number = over.balls.count() + 1

    ball = Ball.objects.create(
        over=over,
        ball_number=ball_number,
        batsman=batsman,
        bowler=over.bowler,
        runs_off_bat=runs_off_bat,
        extra_runs=extra_runs,
        ball_type=ball_type,
        is_wicket=is_wicket,
        wicket_type=wicket_type,
        player_dismissed=player_dismissed,
        fielder=fielder,
    )

    # ── Update Batting Scorecard ──
    _update_batting_scorecard(innings, batsman, ball)

    # ── Update Bowling Scorecard ──
    _update_bowling_scorecard(innings, over.bowler, ball)

    return ball


# ─────────────────────────────────────────────
# Internal: Update Batting Scorecard
# ─────────────────────────────────────────────
def _update_batting_scorecard(innings, batsman, ball):
    scorecard, _ = BattingScorecard.objects.get_or_create(
        innings=innings,
        batsman=batsman,
        defaults={
            "batting_position": BattingScorecard.objects.filter(innings=innings).count() + 1
        }
    )

    # Only byes/leg byes don't count to batsman runs
    if ball.ball_type not in ["WIDE", "BYE", "LEG_BYE"]:
        scorecard.runs += ball.runs_off_bat

    if ball.is_legal_ball:
        scorecard.balls_faced += 1

    if ball.runs_off_bat == 4:
        scorecard.fours += 1
    elif ball.runs_off_bat == 6:
        scorecard.sixes += 1

    if ball.is_wicket and ball.player_dismissed == batsman:
        scorecard.status = "OUT"
        scorecard.dismissal_info = _dismissal_text(ball)

    scorecard.save()


# ─────────────────────────────────────────────
# Internal: Update Bowling Scorecard
# ─────────────────────────────────────────────
def _update_bowling_scorecard(innings, bowler, ball):
    scorecard, _ = BowlingScorecard.objects.get_or_create(
        innings=innings,
        bowler=bowler
    )

    # Byes and leg byes don't go to bowler's runs
    if ball.ball_type not in ["BYE", "LEG_BYE"]:
        scorecard.runs_given += ball.total_runs

    if ball.ball_type == "WIDE":
        scorecard.wides += 1
    elif ball.ball_type == "NO_BALL":
        scorecard.no_balls += 1

    if ball.is_wicket and ball.wicket_type not in ["RUN_OUT"]:
        scorecard.wickets += 1

    # Recalculate overs bowled from legal balls
    legal_balls = Ball.objects.filter(
        over__innings=innings,
        over__bowler=bowler,
        is_legal_ball=True
    ).count()
    scorecard.overs_bowled = round(legal_balls // 6 + (legal_balls % 6) / 10, 1)

    scorecard.save()


# ─────────────────────────────────────────────
# Internal: Dismissal Text
# ─────────────────────────────────────────────
def _dismissal_text(ball):
    wt = ball.wicket_type
    bowler = ball.bowler.player_name
    fielder = ball.fielder.player_name if ball.fielder else None

    if wt == "BOWLED":
        return f"b {bowler}"
    elif wt == "CAUGHT":
        return f"c {fielder} b {bowler}" if fielder else f"c & b {bowler}"
    elif wt == "CAUGHT_AND_BOWLED":
        return f"c & b {bowler}"
    elif wt == "LBW":
        return f"lbw b {bowler}"
    elif wt == "STUMPED":
        return f"st {fielder} b {bowler}"
    elif wt == "RUN_OUT":
        return f"run out ({fielder})" if fielder else "run out"
    elif wt == "HIT_WICKET":
        return f"hit wicket b {bowler}"
    return "out"


# ─────────────────────────────────────────────
# 4. Complete Innings
# ─────────────────────────────────────────────
def complete_innings(innings):
    """Manually mark an innings as completed (e.g. declaration)."""
    innings.status = "COMPLETED"
    innings.save()
    return innings


# ─────────────────────────────────────────────
# 5. Compute Match Result
# ─────────────────────────────────────────────
def compute_result(match):
    """
    Determines the match result after both innings are done.
    Call this after the 2nd innings is completed.

    Returns:
        MatchResult
    """
    innings_list = match.innings.order_by("innings_number")

    if innings_list.count() < 2:
        raise ValueError("Both innings must be completed before computing result.")

    innings1 = innings_list.get(innings_number=1)
    innings2 = innings_list.get(innings_number=2)

    team1_runs = innings1.total_runs   # team that batted first
    team2_runs = innings2.total_runs   # team that chased
    team2_wickets_lost = innings2.total_wickets

    if team2_runs > team1_runs:
        # Chasing team won
        winner = innings2.batting_team
        result_type = "WIN_BY_WICKETS"
        win_margin = 10 - team2_wickets_lost
        summary = f"{winner} won by {win_margin} wicket{'s' if win_margin != 1 else ''}"

    elif team1_runs > team2_runs:
        # First batting team won
        winner = innings1.batting_team
        result_type = "WIN_BY_RUNS"
        win_margin = team1_runs - team2_runs
        summary = f"{winner} won by {win_margin} run{'s' if win_margin != 1 else ''}"

    else:
        winner = None
        result_type = "TIE"
        win_margin = 0
        summary = "Match tied"

    result = MatchResult.objects.create(
        match=match,
        winner=winner,
        result_type=result_type,
        win_margin=win_margin,
        result_summary=summary
    )
    return result


# ─────────────────────────────────────────────
# Helper: Get Current Match State
# ─────────────────────────────────────────────
def get_match_state(match):
    """
    Returns a dict summarising the current live match state.
    Useful for your frontend/API response.
    """
    state = {
        "match": str(match),
        "innings": []
    }

    for innings in match.innings.order_by("innings_number"):
        current_over = innings.overs.filter(is_completed=False).first()
        state["innings"].append({
            "innings_number": innings.innings_number,
            "batting_team": str(innings.batting_team),
            "bowling_team": str(innings.bowling_team),
            "score": f"{innings.total_runs}/{innings.total_wickets}",
            "overs": innings.overs_completed,
            "status": innings.status,
            "target": innings.target,
            "current_over": innings.overs.count() if current_over else None,
        })

    return state
