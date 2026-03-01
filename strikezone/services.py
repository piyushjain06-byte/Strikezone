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
                wicket_type="NONE", player_dismissed=None, fielder=None,
                shot_direction=None):
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
        shot_direction=shot_direction or None,
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


# ─────────────────────────────────────────────
# UNDO LAST BALL
# ─────────────────────────────────────────────
@transaction.atomic
def undo_last_ball(innings):
    """
    Removes the last ball and fully reverses all scorecards.
    Also computes the correct striker/non-striker state BEFORE
    that ball was bowled so the view can restore the session.

    Strike rotation rules (same as record_ball_view):
      - Odd runs off bat (1,3,5)  → batsmen crossed → swap back
      - End of over (legal ball 6) → swap back
      - Wicket → restore dismissed player to their end

    Returns a dict with restored state, or None if no balls.
    """
    # ── Get last ball ──
    last_ball = (
        Ball.objects
        .filter(over__innings=innings)
        .order_by('-over__over_number', '-ball_number')
        .select_related('over', 'batsman', 'bowler', 'player_dismissed', 'fielder')
        .first()
    )
    if not last_ball:
        return None

    over          = last_ball.over
    batsman       = last_ball.batsman   # who was ON STRIKE for this ball
    bowler        = last_ball.bowler
    was_legal     = last_ball.is_legal_ball
    was_wicket    = last_ball.is_wicket
    runs_off_bat  = last_ball.runs_off_bat
    extra_runs    = last_ball.extra_runs
    ball_type     = last_ball.ball_type
    wicket_type   = last_ball.wicket_type
    dismissed_player = last_ball.player_dismissed

    # ── Figure out who will be striker AFTER undo ──
    # The ball record tells us who was batting (striker) at the TIME of this ball.
    # We need to figure out who was NON-striker at that time too.
    # Strategy: look at the previous ball to find out who batted before.
    # But simpler: the current session already has post-ball striker.
    # We derive purely from the ball data:
    #
    #   Case 1: Normal wicket (striker dismissed)
    #     → after undo: striker = dismissed_player, non-striker = whoever is now non-striker
    #
    #   Case 2: Run-out of non-striker
    #     → after undo: striker = batsman (on-strike for ball), non-striker = dismissed_player
    #
    #   Case 3: Odd runs (1,3,5) — batsmen crossed
    #     → after undo: undo the swap → striker = batsman (who hit it), non-striker = current_striker
    #
    #   Case 4: Even runs or dot — no crossing
    #     → after undo: striker = batsman (same as before), non-striker unchanged
    #
    #   Case 5: End of over (6th legal ball) was also completed
    #     → the over-end swap also happened → undo it on top

    # Was this the last legal ball of the over? (over was completed by this ball)
    legal_balls_in_over_before = over.balls.filter(is_legal_ball=True).count()
    # (last_ball is still in DB at this point, so this count includes it)
    was_last_ball_of_over = was_legal and (legal_balls_in_over_before == 6) and over.is_completed

    # ── Determine post-undo striker pair ──
    # Start from "who was batting on this ball"
    # We also need to know who was the non-striker on this ball.
    # The non-striker on this ball = the CURRENT non-striker in session,
    # but ONLY if no crossing happened. We can't reliably get non-striker
    # from the ball record alone, so we compute it differently:
    #
    # Key insight: ball.batsman = striker on that ball.
    # After the ball, the session holds the current striker.
    # We just need to return the correct pair.
    #
    # The result dict will include restore_striker_id and restore_nonstriker_id
    # which the VIEW will set into the session.
    # The view currently has the POST-ball session state.
    # We compute PRE-ball state here:

    # We'll compute the restore pair based on ball type:
    # restore_striker = the person who SHOULD be on strike after undo
    # restore_nonstriker = the other active batsman

    # We need to know the current (post-ball) session pair — pass them in via innings.
    # Actually we can't access session here. So return enough info for the view to decide.

    # Return: pre_ball_striker_id, pre_ball_nonstriker_id
    # Rules:
    #   - pre_ball_striker = ball.batsman ALWAYS (they were on strike)
    #   - pre_ball_nonstriker:
    #       * If wicket of striker → pre_nonstriker = current_nonstriker (unchanged)
    #       * If run-out of nonstriker → pre_nonstriker = dismissed_player
    #       * Otherwise → need to know who was non-striker on this ball
    #         = the person who is currently the non-striker IF no run-crossing
    #           OR the person currently the striker IF run-crossing happened

    # To figure out run-crossing: odd runs_off_bat (for non-wide balls)
    # For WIDE: runs are extras, no crossing even with odd extras typically,
    # but wicket could happen. Wide + wicket (run-out) is possible.
    # For NO_BALL: runs can cause crossing.

    # Simpler approach: tell the view these facts and let the view resolve:
    pre_ball_striker_id = batsman.id

    # Crossing happened if:
    runs_caused_crossing = (
        ball_type in ('NORMAL', 'NO_BALL') and (runs_off_bat % 2 == 1) and not was_wicket
    ) or (
        ball_type in ('NORMAL', 'NO_BALL') and was_wicket and (runs_off_bat % 2 == 1)
        # wicket ball with odd runs — batsmen crossed before wicket fell
    )
    # Wide: odd extra runs can cause crossing (rare, but handle it)
    if ball_type == 'WIDE' and (extra_runs % 2 == 1):
        runs_caused_crossing = True

    # ── Reverse Batting Scorecard ──
    bat_sc = BattingScorecard.objects.filter(innings=innings, batsman=batsman).first()
    if bat_sc:
        if ball_type not in ["WIDE", "BYE", "LEG_BYE"]:
            bat_sc.runs = max(0, bat_sc.runs - runs_off_bat)
        if was_legal:
            bat_sc.balls_faced = max(0, bat_sc.balls_faced - 1)
        if runs_off_bat == 4:
            bat_sc.fours = max(0, bat_sc.fours - 1)
        elif runs_off_bat == 6:
            bat_sc.sixes = max(0, bat_sc.sixes - 1)
        if was_wicket and (dismissed_player is None or dismissed_player == batsman):
            bat_sc.status = "NOT_OUT"
            bat_sc.dismissal_info = ""
        bat_sc.save()

    # Restore dismissed non-striker (run-out of non-striker)
    if was_wicket and dismissed_player and dismissed_player != batsman:
        dis_sc = BattingScorecard.objects.filter(innings=innings, batsman=dismissed_player).first()
        if dis_sc:
            dis_sc.status = "NOT_OUT"
            dis_sc.dismissal_info = ""
            dis_sc.save()

    # ── Reverse Bowling Scorecard ──
    bowl_sc = BowlingScorecard.objects.filter(innings=innings, bowler=bowler).first()
    if bowl_sc:
        if ball_type not in ["BYE", "LEG_BYE"]:
            bowl_sc.runs_given = max(0, bowl_sc.runs_given - last_ball.total_runs)
        if ball_type == "WIDE":
            bowl_sc.wides = max(0, bowl_sc.wides - 1)
        elif ball_type == "NO_BALL":
            bowl_sc.no_balls = max(0, bowl_sc.no_balls - 1)
        if was_wicket and wicket_type not in ["RUN_OUT"]:
            bowl_sc.wickets = max(0, bowl_sc.wickets - 1)
        legal_balls_after = (
            Ball.objects
            .filter(over__innings=innings, over__bowler=bowler, is_legal_ball=True)
            .exclude(id=last_ball.id)
            .count()
        )
        bowl_sc.overs_bowled = round(legal_balls_after // 6 + (legal_balls_after % 6) / 10, 1)
        bowl_sc.save()

    # ── Re-open over if it was completed ──
    if over.is_completed:
        over.is_completed = False
        over.save()

    # ── Delete the ball ──
    last_ball.delete()

    # ── Recompute innings totals ──
    remaining_balls = Ball.objects.filter(over__innings=innings)
    innings.total_runs    = sum(b.total_runs for b in remaining_balls)
    innings.total_wickets = remaining_balls.filter(is_wicket=True).count()
    innings.total_balls   = remaining_balls.filter(is_legal_ball=True).count()
    innings.extras        = sum(b.extra_runs for b in remaining_balls)
    if innings.status == "COMPLETED":
        innings.status = "IN_PROGRESS"
    innings.save()

    legal_count_after = Ball.objects.filter(over=over, is_legal_ball=True).count()

    return {
        'success': True,
        'total_runs': innings.total_runs,
        'total_wickets': innings.total_wickets,
        'overs': innings.overs_completed,
        'total_balls': innings.total_balls,
        'over_number': over.over_number,
        'legal_ball_count': legal_count_after,
        'was_wicket': was_wicket,
        'was_last_ball_of_over': was_last_ball_of_over,
        'dismissed_player_id': dismissed_player.id if dismissed_player else None,
        # Who was on strike FOR this ball (pre-ball striker)
        'pre_ball_striker_id': pre_ball_striker_id,
        # Did runs cause a crossing?
        'runs_caused_crossing': runs_caused_crossing,
        # Was it a run-out of the non-striker?
        'runout_nonstriker': was_wicket and dismissed_player and dismissed_player != batsman,
        'runout_nonstriker_id': dismissed_player.id if (was_wicket and dismissed_player and dismissed_player != batsman) else None,
    }