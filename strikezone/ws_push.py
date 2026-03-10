"""
ws_push.py — push helpers called from views after every DB write.
Never crashes a scoring view — all errors silently swallowed.
"""
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def _push(group, message):
    try:
        layer = get_channel_layer()
        if layer is None:
            return
        async_to_sync(layer.group_send)(group, message)
    except Exception:
        pass


def _innings_payload(inn):
    if not inn:
        return None
    from scoring.models import BattingScorecard, BowlingScorecard
    batting = []
    for b in BattingScorecard.objects.filter(innings=inn).order_by('batting_position').select_related('batsman'):
        if b.status == 'DNB':
            continue
        batting.append({'name': b.batsman.player_name, 'runs': b.runs, 'balls': b.balls_faced,
                        'fours': b.fours, 'sixes': b.sixes, 'sr': float(b.strike_rate),
                        'status': b.status, 'dismissal': b.dismissal_info or b.status,
                        'player_id': b.batsman.id})
    bowling = []
    for b in BowlingScorecard.objects.filter(innings=inn).select_related('bowler'):
        bowling.append({'name': b.bowler.player_name, 'overs': str(b.overs_bowled),
                        'runs': b.runs_given, 'wickets': b.wickets, 'economy': float(b.economy),
                        'wides': b.wides, 'no_balls': b.no_balls, 'player_id': b.bowler.id})
    cur_over = inn.overs.filter(is_completed=False).first()
    over_balls = []
    if cur_over:
        for ball in cur_over.balls.order_by('ball_number'):
            if ball.is_wicket:             over_balls.append('W')
            elif ball.ball_type == 'WIDE': over_balls.append('Wd')
            elif ball.ball_type == 'NO_BALL': over_balls.append('Nb')
            else:                          over_balls.append(str(ball.runs_off_bat))

    # Determine striker — account for strike rotation on odd runs
    striker_id = None
    non_striker_id = None
    try:
        last_over = inn.overs.order_by('-over_number').first()
        if last_over:
            lb = last_over.balls.order_by('-ball_number').first()
            if lb:
                faced_id = lb.batsman_id
                strike_rotated = lb.is_legal_ball and (lb.runs_off_bat % 2 == 1)
                not_out_ids = list(
                    BattingScorecard.objects.filter(
                        innings=inn, status='NOT_OUT'
                    ).order_by('batting_position').values_list('batsman_id', flat=True)
                )
                if strike_rotated:
                    striker_id     = next((pid for pid in not_out_ids if pid != faced_id), faced_id)
                    non_striker_id = faced_id if faced_id in not_out_ids else (not_out_ids[0] if not_out_ids else None)
                else:
                    striker_id     = faced_id if faced_id in not_out_ids else (not_out_ids[0] if not_out_ids else None)
                    non_striker_id = next((pid for pid in not_out_ids if pid != striker_id), None)
    except Exception:
        pass
    return {'team': str(inn.batting_team), 'team_id': inn.batting_team.id,
            'bowling_team': str(inn.bowling_team), 'bowling_team_id': inn.bowling_team.id,
            'total_runs': inn.total_runs, 'total_wickets': inn.total_wickets,
            'overs': str(inn.overs_completed), 'extras': inn.extras, 'status': inn.status,
            'batting': batting, 'bowling': bowling, 'over_balls': over_balls,
            'current_over_num': cur_over.over_number if cur_over else None,
            'current_bowler': cur_over.bowler.player_name if cur_over else None,
            'striker_id': striker_id, 'non_striker_id': non_striker_id}


def _commentary(ball, innings):
    try:
        over = ball.over
        legal = over.balls.filter(is_legal_ball=True).count()
        ref = f"Over {over.over_number}.{legal}"
        bowler = over.bowler.player_name if over.bowler else "Bowler"
        bat = ball.batsman.player_name
        if ball.is_wicket:            return f"🔴 WICKET! {ref} — {bowler} to {bat}, {ball.wicket_type or 'OUT'}!"
        if ball.ball_type == 'WIDE':  return f"Wide. {ref}"
        if ball.ball_type == 'NO_BALL': return f"No Ball! {ref} — {bat} hits {ball.runs_off_bat}"
        if ball.runs_off_bat == 6:    return f"⚡ SIX! {ref} — {bat} sixes {bowler}!"
        if ball.runs_off_bat == 4:    return f"🔵 FOUR! {ref} — {bat} finds the boundary!"
        if ball.runs_off_bat == 0:    return f"Dot. {ref} — {bowler} beats {bat}."
        return f"{ref} — {bat} runs {ball.runs_off_bat}."
    except Exception:
        return None


NON_BOWLER_WICKETS = {"RUN_OUT", "OBSTRUCTING", "RETIRED", "RETIRED_HURT"}


def _check_and_push_hattrick(match, innings, ball):
    """
    Checks whether the current ball completes a hat-trick.
    A hat-trick = 3 consecutive bowler-credited wickets by the same bowler.
    Balls can span overs (e.g. last 2 of one over + first of next).
    Run-outs and obstructions are excluded.
    """
    try:
        from scoring.models import Ball as BallModel, HatTrick

        bowler = ball.over.bowler

        # The current ball must be a bowler-credited wicket
        if ball.wicket_type in NON_BOWLER_WICKETS or not ball.is_wicket:
            return

        # Get the last 3 bowler-credited wickets taken by this bowler in this innings
        # ordered by over_number, ball_number
        wicket_balls = list(
            BallModel.objects.filter(
                over__innings=innings,
                over__bowler=bowler,
                is_wicket=True,
            ).exclude(
                wicket_type__in=NON_BOWLER_WICKETS
            ).order_by('over__over_number', 'ball_number')
        )

        if len(wicket_balls) < 3:
            return

        # The last 3 wicket-balls — b1, b2, b3 (b3 is the current ball)
        b1, b2, b3 = wicket_balls[-3], wicket_balls[-2], wicket_balls[-1]

        if b3.id != ball.id:
            return  # current ball isn't the most recent one — skip

        # Verify the 3 are strictly consecutive deliveries (legal + illegal)
        # We collect ALL balls by this bowler in this innings in order
        all_bowler_balls = list(
            BallModel.objects.filter(
                over__innings=innings,
                over__bowler=bowler,
            ).order_by('over__over_number', 'ball_number').values_list('id', flat=True)
        )

        try:
            idx1 = all_bowler_balls.index(b1.id)
            idx2 = all_bowler_balls.index(b2.id)
            idx3 = all_bowler_balls.index(b3.id)
        except ValueError:
            return

        if idx2 != idx1 + 1 or idx3 != idx2 + 1:
            return  # not consecutive — not a hat-trick

        # Check not already recorded for b3
        if HatTrick.objects.filter(innings=innings, ball3=b3).exists():
            return

        # Save hat-trick
        ht = HatTrick.objects.create(
            innings=innings,
            match=match,
            bowler=bowler,
            ball1=b1,
            ball2=b2,
            ball3=b3,
            victim1=b1.player_dismissed,
            victim2=b2.player_dismissed,
            victim3=b3.player_dismissed,
        )

        victims = ht.victims_display()
        _push(f"match_{match.id}", {'type': 'hat_trick', 'data': {
            'bowler': bowler.player_name,
            'victims': victims,
            'text': f"🎩 HAT-TRICK! {bowler.player_name} takes 3 in 3! ({victims})",
        }})
        # Also push as milestone for broader listeners
        _push(f"match_{match.id}", {'type': 'milestone', 'data': {
            'type': 'hat_trick',
            'player': bowler.player_name,
            'value': 3,
            'text': f"🎩 HAT-TRICK! {bowler.player_name}!",
        }})
    except Exception:
        pass


def _milestones(match, innings, ball):
    from scoring.models import BattingScorecard, BowlingScorecard
    try:
        bat = BattingScorecard.objects.filter(innings=innings, batsman=ball.batsman).first()
        if bat:
            prev = bat.runs - ball.runs_off_bat
            for m in [50, 100, 150, 200]:
                if prev < m <= bat.runs:
                    _push(f"match_{match.id}", {'type': 'milestone', 'data': {
                        'type': 'batting', 'player': ball.batsman.player_name,
                        'value': m, 'text': f"🏏 {ball.batsman.player_name} reaches {m}!"}})
    except Exception:
        pass
    if ball.is_wicket:
        try:
            bowl = BowlingScorecard.objects.filter(innings=innings, bowler=ball.over.bowler).first()
            if bowl:
                for m, label in [(3, "3-wicket haul!"), (5, "Five-wicket haul! 🎯")]:
                    if bowl.wickets == m:
                        _push(f"match_{match.id}", {'type': 'milestone', 'data': {
                            'type': 'bowling', 'player': ball.over.bowler.player_name,
                            'value': m, 'text': f"{label} {ball.over.bowler.player_name} has {m} wickets!"}})
        except Exception:
            pass
        # ── Hat-trick detection ──
        _check_and_push_hattrick(match, innings, ball)


def _full_push(match):
    from scoring.models import Innings
    inn1 = Innings.objects.filter(match=match, innings_number=1).first()
    inn2 = Innings.objects.filter(match=match, innings_number=2).first()
    result_summary = None
    try:    result_summary = match.result.result_summary
    except: pass
    target = (inn1.total_runs + 1) if inn1 and inn2 else None
    payload = {
        'match_id': match.id, 'team1': str(match.team1), 'team2': str(match.team2),
        'inn1': _innings_payload(inn1), 'inn2': _innings_payload(inn2),
        'target': target, 'result': result_summary, 'commentary': None,
    }
    _push(f"match_{match.id}", {'type': 'score_update', 'data': payload})

    # compact push to home ticker
    _push("home_live", {'type': 'home_update', 'matches': [{
        'match_id': match.id,
        'inn1': {'team': str(inn1.batting_team), 'runs': inn1.total_runs,
                 'wickets': inn1.total_wickets, 'overs': str(inn1.overs_completed)} if inn1 else None,
        'inn2': {'team': str(inn2.batting_team), 'runs': inn2.total_runs,
                 'wickets': inn2.total_wickets, 'overs': str(inn2.overs_completed)} if inn2 else None}]})

    # push to tournament page
    _push(f"tournament_{match.tournament_id}", {'type': 'tournament_update', 'data': {
        'event': 'score_update',
        'match_id': match.id,
        'team1': str(match.team1), 'team2': str(match.team2),
        'inn1': _innings_payload(inn1), 'inn2': _innings_payload(inn2),
        'result': result_summary,
    }})


def push_ball(match, innings, ball, striker_id=None, non_striker_id=None):
    commentary = _commentary(ball, innings)
    from scoring.models import Innings
    inn1 = Innings.objects.filter(match=match, innings_number=1).first()
    inn2 = Innings.objects.filter(match=match, innings_number=2).first()
    result_summary = None
    try:    result_summary = match.result.result_summary
    except: pass
    target = (inn1.total_runs + 1) if inn1 and inn2 else None

    # Build payloads, then override striker IDs with the correct session values
    inn1_payload = _innings_payload(inn1)
    inn2_payload = _innings_payload(inn2)

    # The active innings gets the correct striker IDs passed in from the session
    # (which already has them rotated correctly for odd runs)
    if striker_id is not None:
        active_payload = inn2_payload if (inn2_payload and inn2_payload.get('status') == 'IN_PROGRESS') else inn1_payload
        if active_payload:
            active_payload['striker_id']     = striker_id
            active_payload['non_striker_id'] = non_striker_id

    payload = {
        'match_id': match.id, 'team1': str(match.team1), 'team2': str(match.team2),
        'inn1': inn1_payload, 'inn2': inn2_payload,
        'target': target, 'result': result_summary, 'commentary': commentary,
    }
    _push(f"match_{match.id}", {'type': 'score_update', 'data': payload})
    _push("home_live", {'type': 'home_update', 'matches': [{
        'match_id': match.id,
        'inn1': {'team': str(inn1.batting_team), 'runs': inn1.total_runs,
                 'wickets': inn1.total_wickets, 'overs': str(inn1.overs_completed)} if inn1 else None,
        'inn2': {'team': str(inn2.batting_team), 'runs': inn2.total_runs,
                 'wickets': inn2.total_wickets, 'overs': str(inn2.overs_completed)} if inn2 else None}]})
    _push(f"tournament_{match.tournament_id}", {'type': 'tournament_update', 'data': {
        'event': 'score_update', 'match_id': match.id,
        'team1': str(match.team1), 'team2': str(match.team2),
        'inn1': _innings_payload(inn1), 'inn2': _innings_payload(inn2), 'result': result_summary,
    }})
    _milestones(match, innings, ball)


def push_undo(match, innings):
    _full_push(match)


def push_innings_complete(match, innings):
    _push(f"match_{match.id}", {'type': 'innings_complete', 'data': {
        'match_id': match.id, 'innings_number': innings.innings_number,
        'team': str(innings.batting_team), 'runs': innings.total_runs,
        'wickets': innings.total_wickets, 'overs': str(innings.overs_completed)}})


def push_match_complete(match, result_summary):
    _push(f"match_{match.id}", {'type': 'match_complete', 'data': {
        'match_id': match.id, 'result': result_summary,
        'team1': str(match.team1), 'team2': str(match.team2)}})
    _push("home_live", {'type': 'match_status_change', 'data': {
        'match_id': match.id, 'status': 'completed', 'result': result_summary}})
    _push(f"tournament_{match.tournament_id}", {'type': 'tournament_update', 'data': {
        'event': 'match_completed', 'match_id': match.id,
        'team1': str(match.team1), 'team2': str(match.team2), 'result': result_summary}})


def push_match_started(match):
    _push("home_live", {'type': 'match_status_change', 'data': {
        'match_id': match.id, 'status': 'live',
        'team1': str(match.team1), 'team2': str(match.team2)}})
    _push(f"tournament_{match.tournament_id}", {'type': 'tournament_update', 'data': {
        'event': 'match_started', 'match_id': match.id,
        'team1': str(match.team1), 'team2': str(match.team2)}})


def push_new_batsman(match, player):
    photo_url = None
    try:
        if player.photo:
            photo_url = player.photo.url
    except Exception:
        pass
    _push(f"match_{match.id}", {'type': 'new_batsman', 'data': {
        'name': player.player_name,
        'photo': photo_url,
        'initial': player.player_name[0].upper(),
    }})


def push_new_over(match, over_number, bowler):
    photo_url = None
    try:
        if bowler.photo:
            photo_url = bowler.photo.url
    except Exception:
        pass
    _push(f"match_{match.id}", {'type': 'new_over', 'data': {
        'over_number': over_number,
        'bowler_name': bowler.player_name,
        'photo': photo_url,
        'initial': bowler.player_name[0].upper(),
    }})