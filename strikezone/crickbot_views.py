"""
CrickBot — AI Cricket Chatbot powered by Groq
v6: Intent classification, session caching, coach persona, graceful fallbacks.
"""

import json
import os
import re
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render, redirect
from django.core.cache import cache

from groq import Groq
from subscriptions.decorators import require_plan

# ─── CONFIG ──────────────────────────────────────────────────────────────────
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
groq_client = Groq(api_key=GROQ_API_KEY)
# Primary model + fallbacks tried in order when rate limits hit
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_FALLBACK_MODELS = [
    "llama-3.1-8b-instant",                        # smaller Llama, separate TPD limit
    "meta-llama/llama-4-scout-17b-16e-instruct",   # Llama 4 Scout preview, separate limit
    "meta-llama/llama-4-maverick-17b-128e-instruct", # Llama 4 Maverick preview
    "qwen/qwen-3-32b",                             # Qwen 3 preview, separate limit
]
CACHE_TTL = 0    # always rebuild fresh — no stale data

# ─── INTENT CATEGORIES ───────────────────────────────────────────────────────
# Each maps to which context slice(s) to include
INTENT_CONTEXT_MAP = {
    "batting_stats":     "compact",
    "bowling_stats":     "compact",
    "fielding_stats":    "compact",
    "match_detail":      "detailed",
    "teammate_info":     "compact",
    "strategy":          "compact",
    "tournament_info":   "compact",
    "tournament_wide":   "tournament_wide",
    "social_stats":      "compact",       # followers, fans, who follows whom
    "general":           "compact",
}


# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _sr(runs, balls):
    return round((runs / balls) * 100, 2) if balls > 0 else 0.0

def _avg(runs, outs):
    return round(runs / outs, 2) if outs > 0 else float(runs)

def _eco(runs_given, balls_bowled):
    return round((runs_given / balls_bowled) * 6, 2) if balls_bowled > 0 else 0.0

def _extract_bowler_from_dismissal(dismissal_info):
    """
    Parse cricket dismissal strings to extract the bowler's name.
    Examples:
      'b Rohit Sharma'           → 'Rohit Sharma'
      'c Virat Kohli b MS Dhoni' → 'MS Dhoni'
      'lbw b Bumrah'             → 'Bumrah'
      'run out (Jadeja)'         → None  (no bowler)
      'st Dhoni b Bumrah'        → 'Bumrah'
    """
    if not dismissal_info:
        return None
    # Match '... b <Name>' — bowler always follows ' b '
    match = re.search(r'\bb\s+([A-Z][a-zA-Z\s]+?)(?:\s*$)', dismissal_info.strip())
    if match:
        return match.group(1).strip()
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  INTENT CLASSIFIER  — cheap single Groq call, ~200 tokens
# ─────────────────────────────────────────────────────────────────────────────
# Keywords that always mean tournament_wide — checked BEFORE the Groq call
TOURNAMENT_WIDE_KEYWORDS = [
    "mom", "man of the match", "man of match",
    "top scorer", "top run", "most runs", "highest run",
    "most wickets", "top wicket", "best bowler", "best batsman",
    "leaderboard", "points table", "standings",
    "all match", "all matches", "every match",
    "all player", "all team", "all result",
    "who won", "which team won",
    "best performer", "tournament winner",
    "orange cap", "purple cap",
    "from sunrisers", "from mumbai", "from chennai", "from kolkata",
    "from delhi", "from rajasthan", "from punjab", "from gujarat",
    "from lucknow", "from hyderabad", "from bangalore", "from rcb",
    "from kkr", "from csk", "from mi ", "from srh", "from dc ",
    "from rr ", "from pbks", "from gt ", "from lsg",
    "any player from", "anyone from", "any one from",
    "which player", "other team", "another team",
    "across tournament", "in the tournament", "in ipl",
    "tournament stats", "tournament data",
]

SOCIAL_KEYWORDS = [
    "follower", "followers", "following", "follow",
    "fan", "fans", "who follows", "how many followers",
    "my followers", "his followers", "her followers",
    "popular", "most followed", "social",
]

THIRD_PERSON_TRIGGERS = [
    # pronouns meaning "about someone else"
    " him ", " his ", " her ", " their ", " he ", " she ",
    "about rohit", "about virat", "about dhoni", "about hardik",
    "about bumrah", "about jadeja", "about kohli", "about sharma",
    "give me rohit", "give me virat", "give me kohli", "give me sharma",
    # "percentage of him/her/rohit" type questions
    "percentage of ", "% of ", "how does rohit", "how does virat",
    "is rohit", "is virat", "is kohli", "is dhoni", "is hardik",
    "rohit's", "virat's", "kohli's", "dhoni's", "bumrah's", "sharma's",
    # comparison follow-ups
    "who is better", "who is best", "who performed", "who has more",
    "better among", "best among", "compare them", "between them",
    "among them", "which one", "who scored more", "who took more",
    "better player", "worse player", "stronger player",
    "what about kl", "what about rohit", "what about virat",
    "and what about", "how about him", "how about her",
]

def _is_tournament_wide(message):
    """Fast keyword pre-check — returns True if message is clearly tournament-wide."""
    msg = message.lower()
    if any(kw in msg for kw in TOURNAMENT_WIDE_KEYWORDS):
        return True
    # Third-person questions about another player → need full tournament data
    if any(kw in msg for kw in THIRD_PERSON_TRIGGERS):
        return True
    return False

def _is_social(message):
    msg = message.lower()
    return any(kw in msg for kw in SOCIAL_KEYWORDS)


def classify_intent(message, last_user_msg=None):
    """
    Classify the user's message into one of the intent categories.
    Accepts optional last_user_msg for follow-up context resolution.
    Returns one of the keys in INTENT_CONTEXT_MAP.
    Falls back to 'general' on any error.
    """
    # 1. Fast keyword pre-check — no Groq call needed
    if _is_tournament_wide(message):
        return "tournament_wide"
    if _is_social(message):
        return "social_stats"

    # 2. Combine with last message for follow-ups so classifier has full context
    combined = message
    if last_user_msg and len(message.split()) <= 10:
        combined = f"{last_user_msg} / follow-up: {message}"
        # Re-check keywords on combined
        if _is_tournament_wide(combined):
            return "tournament_wide"

    prompt = (
        "Classify this cricket chatbot message into exactly one category. "
        "Reply with ONLY the category name, nothing else.\n\n"
        "Categories:\n"
        "- batting_stats (my scores, my runs, my average, my strike rate, my best innings)\n"
        "- bowling_stats (my wickets, my economy, who I dismissed, my dot balls)\n"
        "- fielding_stats (my catches, my run-outs)\n"
        "- match_detail (full scorecard, ball-by-ball, specific match recap)\n"
        "- teammate_info (squad, who should open, my teammates, my partner)\n"
        "- strategy (my weaknesses, how to improve, coach advice, suggestions for me)\n"
        "- tournament_wide (MOM awards, top scorers, all match results, best performers, "
        "players from other teams, anyone from a specific team, leaderboard, points table, "
        "who won matches, tournament stats, other teams stats)\n"
        "- tournament_info (standings, other teams, other matches)\n"
        "- social_stats (followers, fans, who follows a player, how many followers, most followed)\n"
        "- general (greetings, other)\n\n"
        f"Message: {combined}\n\nCategory:"
    )
    try:
        resp = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=10,
            temperature=0.0,
        )
        intent = resp.choices[0].message.content.strip().lower()
        # Sanitize — only accept known categories
        for key in INTENT_CONTEXT_MAP:
            if key in intent:
                return key
    except Exception:
        pass
    return "general"


# ─────────────────────────────────────────────────────────────────────────────
#  ALL-PLAYER TOURNAMENT STATS  — one compact line per player
# ─────────────────────────────────────────────────────────────────────────────
def build_tournament_all_player_stats(tournament, my_team):
    """Compact batting+bowling summary for every player in the tournament."""
    from teams.models import TournamentTeam
    from scoring.models import BattingScorecard, BowlingScorecard

    lines = ["  ALL PLAYER STATS IN TOURNAMENT:"]
    try:
        all_tt = TournamentTeam.objects.filter(tournament=tournament).select_related(
            'team'
        ).prefetch_related('roster__player')

        for tt in all_tt:
            team_tag = " [MY TEAM]" if tt.team == my_team else ""
            lines.append(f"    {tt.team.team_name}{team_tag}:")

            for entry in tt.roster.select_related('player').all():
                p = entry.player
                role = entry.get_role_display()
                cap = " (C)" if entry.is_captain else " (VC)" if entry.is_vice_captain else ""

                bat_qs = list(BattingScorecard.objects.filter(
                    innings__match__tournament=tournament, batsman=p
                ))
                b_runs  = sum(b.runs for b in bat_qs)
                b_balls = sum(b.balls_faced for b in bat_qs)
                b_fours = sum(b.fours for b in bat_qs)
                b_sixes = sum(b.sixes for b in bat_qs)
                b_outs  = sum(1 for b in bat_qs if b.status == 'OUT')
                b_inns  = len(bat_qs)

                bowl_qs = list(BowlingScorecard.objects.filter(
                    innings__match__tournament=tournament, bowler=p
                ))
                wk_total   = sum(bw.wickets for bw in bowl_qs)
                runs_given = sum(bw.runs_given for bw in bowl_qs)
                balls_bowled = 0
                for bw in bowl_qs:
                    ov = float(bw.overs_bowled)
                    balls_bowled += int(ov) * 6 + round((ov - int(ov)) * 10)

                bat_str = ""
                if b_inns > 0:
                    bat_str = (
                        f"BAT {b_runs}R avg:{_avg(b_runs,b_outs)} "
                        f"SR:{_sr(b_runs,b_balls)} 4s:{b_fours} 6s:{b_sixes} ({b_inns}inn)"
                    )
                bowl_str = ""
                if balls_bowled > 0:
                    bowl_str = f"BOWL {wk_total}W/{runs_given}R eco:{_eco(runs_given,balls_bowled)}"

                # Hat-tricks
                ht_count = 0
                try:
                    from scoring.models import HatTrick as _HT
                    ht_count = _HT.objects.filter(
                        bowler=p, match__tournament=tournament
                    ).count()
                except Exception:
                    pass
                ht_str = f" HAT-TRICKS:{ht_count}" if ht_count else ""
                stats = " | ".join(filter(None, [bat_str, bowl_str])) or "No match data yet"
                lines.append(f"      {p.player_name}{cap} [{role}]: {stats}{ht_str}")

    except Exception as e:
        lines.append(f"    (Could not load full player stats: {e})")

    return "\n".join(lines)




def _build_ball_stats_for_tournament(tournament):
    """
    Returns a dict: player_name -> { batting: {...}, bowling: {...}, fielding: {...} }
    Computed from Ball model — gives percentages for boundary/running/dot/wicket type etc.
    """
    from scoring.models import Ball
    from django.db.models import Count, Sum, Q

    stats = {}  # player_name -> data

    def _ps(name):
        if name not in stats:
            stats[name] = {
                'bat_balls': 0, 'bat_runs': 0,
                'fours': 0, 'sixes': 0, 'ones': 0, 'twos': 0, 'threes': 0, 'dots': 0,
                'bowl_balls': 0, 'bowl_runs': 0,
                'dot_balls_bowled': 0, 'wides': 0, 'no_balls': 0,
                'bowl_wickets': 0,
                'wicket_types': {},   # how dismissed: {BOWLED:2, CAUGHT:3, ...}
                'catches': 0, 'runouts': 0, 'stumpings': 0,
            }
        return stats[name]

    balls = Ball.objects.filter(
        over__innings__match__tournament=tournament
    ).select_related('batsman', 'bowler', 'fielder').iterator()

    for b in balls:
        # ── BATTING ──
        bname = b.batsman.player_name if b.batsman else None
        if bname and b.is_legal_ball:
            p = _ps(bname)
            p['bat_balls'] += 1
            p['bat_runs']  += b.runs_off_bat
            r = b.runs_off_bat
            if r == 0:   p['dots']   += 1
            elif r == 1: p['ones']   += 1
            elif r == 2: p['twos']   += 1
            elif r == 3: p['threes'] += 1
            elif r == 4: p['fours']  += 1
            elif r >= 6: p['sixes']  += 1

        # ── BOWLING ──
        bowler = b.bowler.player_name if b.bowler else None
        if bowler:
            p = _ps(bowler)
            if b.ball_type == 'WIDE':
                p['wides'] += 1
            elif b.ball_type == 'NO_BALL':
                p['no_balls'] += 1
            if b.is_legal_ball:
                p['bowl_balls'] += 1
                p['bowl_runs']  += b.total_runs
                if b.runs_off_bat == 0 and b.extra_runs == 0:
                    p['dot_balls_bowled'] += 1
            if b.is_wicket and b.wicket_type not in ('RUN_OUT', 'NONE', None):
                p['bowl_wickets'] += 1
                wt = b.wicket_type or 'UNKNOWN'
                p['wicket_types'][wt] = p['wicket_types'].get(wt, 0) + 1

        # ── FIELDING ──
        if b.is_wicket and b.fielder:
            fname = b.fielder.player_name
            p = _ps(fname)
            if b.wicket_type == 'CAUGHT':    p['catches']   += 1
            elif b.wicket_type == 'RUN_OUT': p['runouts']   += 1
            elif b.wicket_type == 'STUMPED': p['stumpings'] += 1

    # Build formatted lines
    result = {}
    for name, d in stats.items():
        lines = []

        # Batting breakdown
        if d['bat_balls'] > 0:
            tb = d['bat_balls']
            boundary_runs = d['fours'] * 4 + d['sixes'] * 6
            running_runs  = d['bat_runs'] - boundary_runs
            bdry_pct  = round(boundary_runs / d['bat_runs'] * 100, 1) if d['bat_runs'] else 0
            run_pct   = round(running_runs  / d['bat_runs'] * 100, 1) if d['bat_runs'] else 0
            dot_pct   = round(d['dots'] / tb * 100, 1)
            bdry_ball_pct = round((d['fours'] + d['sixes']) / tb * 100, 1)
            lines.append(
                f"BAT: {d['bat_runs']}R off {tb}b | "
                f"Boundary runs {bdry_pct}% ({boundary_runs}R from {d['fours']}x4 {d['sixes']}x6) | "
                f"Running {run_pct}% ({running_runs}R via 1s:{d['ones']} 2s:{d['twos']} 3s:{d['threes']}) | "
                f"Dot ball faced {dot_pct}% | Boundary ball freq {bdry_ball_pct}%"
            )

        # Bowling breakdown
        if d['bowl_balls'] > 0:
            tb = d['bowl_balls']
            dot_pct  = round(d['dot_balls_bowled'] / tb * 100, 1)
            wkt_freq = round(d['bowl_wickets'] / tb * 6 * 100, 1) if tb else 0  # wickets per 100 balls
            wt_str   = " ".join(f"{k}:{v}" for k, v in d['wicket_types'].items()) if d['wicket_types'] else "none"
            lines.append(
                f"BOWL: {d['bowl_wickets']}W from {tb}b ({d['bowl_balls']//6}.{d['bowl_balls']%6}ov) {d['bowl_runs']}R | "
                f"Dot% {dot_pct}% | Wides:{d['wides']} NoBalls:{d['no_balls']} | "
                f"Wicket types: {wt_str} | Wicket every {round(tb/d['bowl_wickets'],1) if d['bowl_wickets'] else "N/A"} balls"
            )

        # Fielding
        total_fielding = d['catches'] + d['runouts'] + d['stumpings']
        if total_fielding > 0:
            lines.append(
                f"FIELD: Catches:{d['catches']} RunOuts:{d['runouts']} Stumpings:{d['stumpings']}"
            )

        if lines:
            result[name] = " || ".join(lines)

    return result

def _build_points_table(tournament, all_matches):
    """
    Build a points table from completed match results.
    Returns list of dicts sorted by points desc, then NRR desc.
    """
    from matches.models import MatchResult
    from scoring.models import Innings

    table = {}  # team_name -> {played, won, lost, tied, no_result, points, nrr_runs_for, nrr_balls_for, nrr_runs_against, nrr_balls_against}

    # Seed all teams
    from teams.models import TournamentTeam
    for tt in TournamentTeam.objects.filter(tournament=tournament).select_related('team'):
        name = tt.team.team_name
        table[name] = dict(played=0, won=0, lost=0, tied=0, nr=0, points=0,
                           rf=0, bf=0, ra=0, ba=0)  # runs/balls for/against

    for m in all_matches:
        try:
            mr = m.result
        except Exception:
            continue  # not completed

        t1 = m.team1.team_name
        t2 = m.team2.team_name
        for tn in [t1, t2]:
            if tn not in table:
                table[tn] = dict(played=0, won=0, lost=0, tied=0, nr=0, points=0,
                                 rf=0, bf=0, ra=0, ba=0)

        winner = mr.winner.team_name if mr.winner else None
        table[t1]['played'] += 1
        table[t2]['played'] += 1

        if winner == t1:
            table[t1]['won'] += 1; table[t1]['points'] += 2
            table[t2]['lost'] += 1
        elif winner == t2:
            table[t2]['won'] += 1; table[t2]['points'] += 2
            table[t1]['lost'] += 1
        elif winner is None:
            # tie or no result
            table[t1]['tied'] += 1; table[t1]['points'] += 1
            table[t2]['tied'] += 1; table[t2]['points'] += 1

        # NRR — use innings scores
        try:
            inns = list(Innings.objects.filter(match=m).select_related('batting_team').order_by('innings_number'))
            if len(inns) == 2:
                inn1, inn2 = inns[0], inns[1]
                def _b2f(inn):
                    # balls to overs as float for NRR
                    return inn.total_balls / 6.0 if inn.total_balls else 1.0
                t_inn1 = inn1.batting_team.team_name
                t_inn2 = inn2.batting_team.team_name
                # team batting in inn1
                if t_inn1 in table:
                    table[t_inn1]['rf'] += inn1.total_runs
                    table[t_inn1]['bf'] += inn1.total_balls or 1
                    table[t_inn1]['ra'] += inn2.total_runs
                    table[t_inn1]['ba'] += inn2.total_balls or 1
                if t_inn2 in table:
                    table[t_inn2]['rf'] += inn2.total_runs
                    table[t_inn2]['bf'] += inn2.total_balls or 1
                    table[t_inn2]['ra'] += inn1.total_runs
                    table[t_inn2]['ba'] += inn1.total_balls or 1
        except Exception:
            pass

    # Compute NRR
    for row in table.values():
        rpo_for     = (row['rf'] / row['bf'] * 6) if row['bf'] else 0
        rpo_against = (row['ra'] / row['ba'] * 6) if row['ba'] else 0
        row['nrr'] = round(rpo_for - rpo_against, 3)

    sorted_table = sorted(table.items(), key=lambda x: (-x[1]['points'], -x[1]['nrr']))
    return sorted_table

# ─────────────────────────────────────────────────────────────────────────────
#  TOURNAMENT-WIDE CONTEXT — ALL matches, ALL MOM, ALL results, leaderboard
#  Used when player asks about the whole tournament, not just their own stats
# ─────────────────────────────────────────────────────────────────────────────
def build_tournament_wide_context(player, query_message=''):
    """
    Builds full tournament context covering every match, every result,
    every MOM award, and a leaderboard of all players' stats.
    Not filtered to the logged-in player — gives the whole picture.
    """
    from teams.models import TournamentRoster, TournamentTeam
    from matches.models import CreateMatch, ManOfTheMatch, MatchResult
    from scoring.models import BattingScorecard, BowlingScorecard, Innings
    from tournaments.models import TournamentDetails, TournamentAward

    lines = [f"TOURNAMENT-WIDE DATA (asked by: {player.player_name})\n"]

    # Find all tournaments this player is part of
    rosters = TournamentRoster.objects.filter(player=player).select_related(
        'tournament', 'tournament_team__team'
    ).order_by('-tournament__start_date')

    if not rosters.exists():
        return "No tournament data found."

    for roster in rosters:
        t = roster.tournament
        my_team = roster.tournament_team.team

        lines.append(f"{'='*60}")
        lines.append(f"TOURNAMENT: {t.tournament_name} | {t.get_tournament_type_display()} | {t.number_of_overs} overs/side")
        lines.append(f"{'='*60}")

        # ── ALL TEAMS IN TOURNAMENT ──
        all_teams = TournamentTeam.objects.filter(tournament=t).select_related('team')
        team_names = [tt.team.team_name for tt in all_teams]
        lines.append(f"Teams: {', '.join(team_names)}")
        lines.append("")

        # ── POINTS TABLE / STANDINGS ──
        all_matches_pts = list(CreateMatch.objects.filter(tournament=t).select_related('team1','team2'))
        standings = _build_points_table(t, all_matches_pts)
        lines.append("POINTS TABLE (current standings):")
        lines.append("  Pos  Team                    P   W   L   T  Pts   NRR")
        for pos, (tname, row) in enumerate(standings, 1):
            lines.append(
                f"  {pos:<4} {tname:<24} {row['played']:<3} {row['won']:<3} "
                f"{row['lost']:<3} {row['tied']:<3} {row['points']:<5} {row['nrr']:+.3f}"
            )
        lines.append("")

        # ── ALL MATCH RESULTS + MOM ──
        lines.append("ALL MATCH RESULTS & MAN OF THE MATCH:")
        all_matches = CreateMatch.objects.filter(tournament=t).select_related(
            'team1', 'team2'
        ).order_by('match_date')

        for i, m in enumerate(all_matches, 1):
            try:
                result_str = m.result.result_summary
                winner = m.result.winner.team_name if m.result.winner else "Tie/No result"
            except Exception:
                result_str = "Pending / Not played yet"
                winner = "TBD"

            # MOM
            mom_str = "No MOM awarded"
            try:
                mom = m.man_of_the_match
                bat = f"{mom.bat_runs}({mom.bat_balls}b)" if mom.bat_runs or mom.bat_balls else ""
                bowl = f"{mom.bowl_wickets}W/{mom.bowl_runs}R" if mom.bowl_wickets or mom.bowl_runs else ""
                perf = " | ".join(filter(None, [bat, bowl]))
                mom_str = f"{mom.player.player_name} [{perf}]" if perf else mom.player.player_name
            except Exception:
                pass

            lines.append(
                f"  Match {i}: {m.team1.team_name} vs {m.team2.team_name} ({m.match_date})"
            )
            lines.append(f"    Result: {result_str}")
            lines.append(f"    MOM: {mom_str}")

            # Hat-tricks in this match
            try:
                from scoring.models import HatTrick
                hts = HatTrick.objects.filter(match=m).select_related(
                    'bowler', 'victim1', 'victim2', 'victim3'
                )
                for ht in hts:
                    lines.append(
                        f"    HAT-TRICK: {ht.bowler.player_name} dismissed {ht.victims_display()}"
                    )
            except Exception:
                pass

            try:
                inns = list(Innings.objects.filter(match=m).select_related('batting_team').order_by('innings_number'))
                scores = [f"{inn.batting_team.team_name} {inn.total_runs}/{inn.total_wickets}" for inn in inns]
                if scores: lines.append(f"    Scores: {' | '.join(scores)}")
            except Exception:
                pass

        # ── PLAYER LEADERBOARD (batting) ──
        lines.append("BATTING LEADERBOARD (all players, all matches in this tournament):")
        bat_totals = {}  # player_name -> {runs, balls, fours, sixes, outs, inns}
        for bs in BattingScorecard.objects.filter(
            innings__match__tournament=t
        ).select_related('batsman'):
            n = bs.batsman.player_name
            if n not in bat_totals:
                bat_totals[n] = {"runs": 0, "balls": 0, "fours": 0, "sixes": 0, "outs": 0, "inns": 0}
            bat_totals[n]["runs"]  += bs.runs
            bat_totals[n]["balls"] += bs.balls_faced
            bat_totals[n]["fours"] += bs.fours
            bat_totals[n]["sixes"] += bs.sixes
            bat_totals[n]["inns"]  += 1
            if bs.status == "OUT":
                bat_totals[n]["outs"] += 1

        sorted_bat = sorted(bat_totals.items(), key=lambda x: x[1]["runs"], reverse=True)
        for rank, (name, s) in enumerate(sorted_bat[:10], 1):
            avg = _avg(s["runs"], s["outs"])
            sr  = _sr(s["runs"], s["balls"])
            lines.append(f"  {rank}. {name} {s['runs']}R avg:{avg} SR:{sr} 4s:{s['fours']} 6s:{s['sixes']}")

        lines.append("")

        # ── PLAYER LEADERBOARD (bowling) ──
        lines.append("BOWLING LEADERBOARD (all players, all matches in this tournament):")
        bowl_totals = {}  # player_name -> {wkts, runs, balls}
        for bws in BowlingScorecard.objects.filter(
            innings__match__tournament=t
        ).select_related('bowler'):
            n = bws.bowler.player_name
            if n not in bowl_totals:
                bowl_totals[n] = {"wkts": 0, "runs": 0, "balls": 0}
            bowl_totals[n]["wkts"] += bws.wickets
            bowl_totals[n]["runs"] += bws.runs_given
            ov = float(bws.overs_bowled)
            bowl_totals[n]["balls"] += int(ov) * 6 + round((ov % 1) * 10)

        sorted_bowl = sorted(bowl_totals.items(), key=lambda x: x[1]["wkts"], reverse=True)
        for rank, (name, s) in enumerate(sorted_bowl[:10], 1):
            eco = _eco(s["runs"], s["balls"])
            lines.append(f"  {rank}. {name} {s['wkts']}W {s['runs']}R eco:{eco}")

        lines.append("")

        # ── DETAILED BALL STATS (batting %, bowling %, fielding %) ──
        # Only build for players mentioned in message + top 5 to save tokens
        lines.append("PLAYER BALL-LEVEL BREAKDOWN (boundary%, running%, dot%, wicket types):")
        try:
            ball_stats = _build_ball_stats_for_tournament(t)
            # Always include players whose name appears in the user message
            msg_lower = (query_message or '').lower()
            priority = [n for n in ball_stats if any(
                part in msg_lower for part in n.lower().split() if len(part) > 3
            )]
            # Also include top 5 run-scorers and top 5 wicket-takers from leaderboards
            top_bat_names = [name for name, _ in sorted_bat[:5]]
            top_bowl_names = [name for name, _ in sorted_bowl[:5]]
            include = set(priority) | set(top_bat_names) | set(top_bowl_names)
            for pname in sorted(include):
                if pname in ball_stats:
                    lines.append(f"  {pname}: {ball_stats[pname]}")
            # If player asked about someone not in top lists, include them
            for pname, stat_line in ball_stats.items():
                if pname in priority and pname not in include:
                    lines.append(f"  {pname}: {stat_line}")
        except Exception as e:
            lines.append(f"  (ball stats unavailable: {e})")
        lines.append("")

        # ── HAT-TRICKS IN TOURNAMENT ──
        try:
            from scoring.models import HatTrick as _HT
            ht_all = _HT.objects.filter(match__tournament=t).select_related(
                'bowler','match__team1','match__team2','victim1','victim2','victim3'
            )
            if ht_all.exists():
                lines.append("HAT-TRICKS IN THIS TOURNAMENT:")
                for ht in ht_all:
                    lines.append(
                        f"  {ht.bowler.player_name} vs "
                        f"{ht.match.team1.team_name} vs {ht.match.team2.team_name} "
                        f"({ht.match.match_date}): dismissed {ht.victims_display()}"
                    )
                lines.append("")
        except Exception:
            pass

        # ── TOURNAMENT AWARDS ──
        awards = TournamentAward.objects.filter(tournament=t).select_related('player')
        if awards.exists():
            lines.append("TOURNAMENT AWARDS:")
            for aw in awards:
                lines.append(f"  {aw.get_award_type_display()}: {aw.player.player_name}")
            lines.append("")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
#  FOLLOWER CONTEXT  — who follows each player in the tournament
# ─────────────────────────────────────────────────────────────────────────────
def build_follower_context(player):
    """
    Returns a summary of follower counts for the logged-in player
    and all players in their tournament(s).
    """
    from accounts.models import PlayerFollow, GuestFollow
    from teams.models import TournamentRoster, PlayerDetails

    lines = ["FOLLOWER DATA:"]

    # Logged-in player's own followers
    pf_count = PlayerFollow.objects.filter(following=player).count()
    gf_count = GuestFollow.objects.filter(following=player).count()
    total    = pf_count + gf_count
    lines.append(
        f"  {player.player_name} has {total} follower(s) "
        f"({pf_count} registered player(s), {gf_count} guest fan(s))."
    )

    # Who follows the logged-in player
    player_followers = list(
        PlayerFollow.objects.filter(following=player)
        .select_related('follower').values_list('follower__player_name', flat=True)
    )
    if player_followers:
        lines.append(f"  Followed by: {', '.join(player_followers)}")

    # All other players in same tournament(s) with their counts
    rosters = TournamentRoster.objects.filter(player=player).select_related(
        'tournament_team__tournament'
    )
    seen_ids = {player.id}
    for roster in rosters:
        tournament = roster.tournament_team.tournament
        peers = TournamentRoster.objects.filter(
            tournament_team__tournament=tournament
        ).select_related('player').exclude(player__id__in=seen_ids)

        for p in peers:
            seen_ids.add(p.player.id)
            pc = PlayerFollow.objects.filter(following=p.player).count()
            gc = GuestFollow.objects.filter(following=p.player).count()
            t  = pc + gc
            lines.append(
                f"  {p.player.player_name}: {t} follower(s) "
                f"({pc} player(s), {gc} guest(s))"
            )

    return "\n".join(lines)



def build_player_context_compact(player):
    from teams.models import TournamentRoster, TournamentTeam
    from matches.models import CreateMatch
    from scoring.models import BattingScorecard, BowlingScorecard, Ball
    from tournaments.models import TournamentAward

    lines = [f"PLAYER: {player.player_name}"]

    rosters = TournamentRoster.objects.filter(player=player).select_related(
        'tournament', 'tournament_team__team'
    ).order_by('-tournament__start_date')

    if not rosters.exists():
        lines.append("No tournament history.")
        return "\n".join(lines)

    career = dict(runs=0, balls=0, fours=0, sixes=0, outs=0,
                  wickets=0, balls_bowled=0, runs_given=0,
                  catches=0, runouts=0, mom=0, matches=0)

    # Track dismissals by bowler across career
    career_dismissed_by = {}  # bowler_name -> count

    for roster in rosters:
        t = roster.tournament
        my_team = roster.tournament_team.team

        lines.append(
            f"\nTOURNAMENT: {t.tournament_name} | "
            f"{t.get_tournament_type_display()} | {t.number_of_overs}ov | "
            f"Team: {my_team.team_name}"
        )

        # Squad
        tt_obj = TournamentTeam.objects.filter(tournament=t, team=my_team)\
                     .prefetch_related('roster__player').first()
        if tt_obj:
            names = [
                r.player.player_name
                + (" (C)" if r.is_captain else " (VC)" if r.is_vice_captain else "")
                for r in tt_obj.roster.select_related('player').all()
            ]
            lines.append(f"  Squad: {', '.join(names)}")

        # Points table — so player can ask about standings
        try:
            _all_m = list(CreateMatch.objects.filter(tournament=t).select_related('team1','team2'))
            _standings = _build_points_table(t, _all_m)
            pts_lines = []
            for pos, (tname, row) in enumerate(_standings, 1):
                marker = " <MY TEAM>" if tname == my_team.team_name else ""
                pts_lines.append(
                    f"  {pos}. {tname}{marker}: {row['played']}P {row['won']}W {row['lost']}L "
                    f"{row['points']}pts NRR:{row['nrr']:+.3f}"
                )
            lines.append("  STANDINGS: " + " | ".join(pts_lines))
        except Exception:
            pass

        tourn = dict(runs=0, balls=0, fours=0, sixes=0, outs=0,
                     wickets=0, balls_bowled=0, runs_given=0,
                     catches=0, runouts=0, mom=0, matches=0)
        opp_stats = {}
        match_lines = []

        all_matches = CreateMatch.objects.filter(tournament=t).select_related(
            'team1', 'team2', 'result', 'man_of_the_match__player'
        ).order_by('match_date')
        my_matches = [m for m in all_matches if m.team1 == my_team or m.team2 == my_team]

        team_match_count = 0  # all matches the TEAM played (completed/pending)
        player_match_count = 0  # matches where THIS PLAYER actually batted or bowled

        for m in my_matches:
            team_match_count += 1
            opponent = m.team2 if m.team1 == my_team else m.team1
            oname = opponent.team_name
            if oname not in opp_stats:
                opp_stats[oname] = dict(runs=0, balls=0, fours=0, sixes=0,
                                        outs=0, wickets=0, catches=0, matches=0)
            opp_stats[oname]['matches'] += 1

            try:
                result_str = m.result.result_summary
            except Exception:
                result_str = "Pending"

            mom_str = ""
            try:
                if m.man_of_the_match.player_id == player.id:
                    tourn['mom'] += 1
                    mom_str = " ⭐MOM"
            except Exception:
                pass

            # My batting
            bat_parts = []
            player_batted = False
            for bs in BattingScorecard.objects.filter(
                innings__match=m, batsman=player
            ).select_related('innings'):
                # Only count innings where player actually faced balls (not DNB)
                if bs.status == 'DNB':
                    bat_parts.append(f"DNB")
                    continue
                player_batted = True
                tourn['runs']  += bs.runs
                tourn['balls'] += bs.balls_faced
                tourn['fours'] += bs.fours
                tourn['sixes'] += bs.sixes
                opp_stats[oname]['runs']  += bs.runs
                opp_stats[oname]['balls'] += bs.balls_faced
                opp_stats[oname]['fours'] += bs.fours
                opp_stats[oname]['sixes'] += bs.sixes
                if bs.status == 'OUT':
                    tourn['outs'] += 1
                    opp_stats[oname]['outs'] += 1
                    bowler_name = _extract_bowler_from_dismissal(bs.dismissal_info)
                    if bowler_name:
                        career_dismissed_by[bowler_name] = career_dismissed_by.get(bowler_name, 0) + 1
                dismissal = f" dismissed:{bs.dismissal_info}" if bs.dismissal_info else ""
                bat_parts.append(
                    f"{bs.runs}({bs.balls_faced}b) "
                    f"4s:{bs.fours} 6s:{bs.sixes} [{bs.status}{dismissal}]"
                )

            # My bowling
            bowl_parts = []
            player_bowled = False
            for bws in BowlingScorecard.objects.filter(
                innings__match=m, bowler=player
            ).select_related('innings'):
                if float(bws.overs_bowled) == 0:
                    continue
                player_bowled = True
                tourn['wickets']    += bws.wickets
                tourn['runs_given'] += bws.runs_given
                ov = float(bws.overs_bowled)
                tourn['balls_bowled'] += int(ov) * 6 + round((ov - int(ov)) * 10)
                opp_stats[oname]['wickets'] += bws.wickets

                wkt_balls = Ball.objects.filter(
                    over__innings=bws.innings, bowler=player, is_wicket=True
                ).select_related('player_dismissed')
                wkt_names = [b.player_dismissed.player_name for b in wkt_balls if b.player_dismissed]
                wkts_str = f" wkts:[{', '.join(wkt_names)}]" if wkt_names else ""
                bowl_parts.append(
                    f"{bws.overs_bowled}ov {bws.wickets}W/{bws.runs_given}R "
                    f"eco:{bws.economy}{wkts_str}"
                )

            # Count as player's match only if they batted or bowled
            if player_batted or player_bowled:
                player_match_count += 1
                tourn['matches'] += 1

            # Fielding
            c_count = Ball.objects.filter(
                over__innings__match=m, fielder=player,
                is_wicket=True, wicket_type='CAUGHT'
            ).count()
            ro_count = Ball.objects.filter(
                over__innings__match=m, fielder=player,
                is_wicket=True, wicket_type='RUN_OUT'
            ).count()
            tourn['catches']  += c_count
            tourn['runouts']  += ro_count
            opp_stats[oname]['catches'] += c_count

            bat_str   = " | ".join(bat_parts) if bat_parts else "DNB"
            bowl_str  = " | ".join(bowl_parts) if bowl_parts else "-"
            field_str = (f" ct:{c_count}" if c_count else "") + (f" ro:{ro_count}" if ro_count else "")
            match_lines.append(
                f"  vs {oname} ({m.match_date}){mom_str}: "
                f"BAT {bat_str}  BOWL {bowl_str}{field_str}  [{result_str}]"
            )

        # Append team vs player match distinction clearly for the AI
        match_lines.insert(0,
            f"  NOTE: Team played {team_match_count} matches | "
            f"{player.player_name} personally played in {player_match_count} matches "
            f"(batted or bowled). Use player_match_count for career stats."
        )

        lines.extend(match_lines)

        # Stats vs each opponent
        opp_parts = []
        for oname, s in opp_stats.items():
            opp_parts.append(
                f"{oname}: R{s['runs']}({s['balls']}b) SR{_sr(s['runs'],s['balls'])} "
                f"4s:{s['fours']} 6s:{s['sixes']} Wk:{s['wickets']} Ct:{s['catches']}"
            )
        if opp_parts:
            lines.append(f"  VS TEAMS: {' | '.join(opp_parts)}")

        t_sr  = _sr(tourn['runs'], tourn['balls'])
        t_avg = _avg(tourn['runs'], tourn['outs'])
        t_eco = _eco(tourn['runs_given'], tourn['balls_bowled'])
        # innings_batted = number of non-DNB innings for strike rate context
        innings_batted = tourn['outs'] + max(0, tourn['matches'] - tourn['outs'])
        lines.append(
            f"  PLAYER TOTALS (only matches {player.player_name} personally participated in):"
        )
        lines.append(
            f"    Matches played: {tourn['matches']} "
            f"(team played {team_match_count} total in this tournament)"
        )
        lines.append(
            f"    BAT: {tourn['runs']} runs | "
            f"Avg: {t_avg} (runs / times OUT, times out = {tourn['outs']}) | "
            f"SR: {t_sr} | 4s: {tourn['fours']} | 6s: {tourn['sixes']}"
        )
        lines.append(
            f"    BOWL: {tourn['wickets']} wkts | "
            f"Runs given: {tourn['runs_given']} | Eco: {t_eco}"
        )
        lines.append(
            f"    FIELD: Catches: {tourn['catches']} | Run-outs: {tourn['runouts']} | MOM: {tourn['mom']}"
        )

        # Hat-tricks this player took in this tournament
        try:
            from scoring.models import HatTrick as _HT
            ht_qs = _HT.objects.filter(
                bowler=player, match__tournament=t
            ).select_related('match__team1','match__team2','victim1','victim2','victim3')
            if ht_qs.exists():
                lines.append(f"    HAT-TRICKS: {ht_qs.count()}")
                for ht in ht_qs:
                    lines.append(
                        f"      Hat-trick vs {ht.match.team1.team_name} vs {ht.match.team2.team_name} "
                        f"({ht.match.match_date}): dismissed {ht.victims_display()}"
                    )
        except Exception:
            pass

        for aw in TournamentAward.objects.filter(tournament=t, player=player):
            lines.append(f"  AWARD: {aw.get_award_type_display()}")

        for k in career:
            career[k] += tourn.get(k, 0)

    # Career totals
    c_sr  = _sr(career['runs'], career['balls'])
    c_avg = _avg(career['runs'], career['outs'])
    c_eco = _eco(career['runs_given'], career['balls_bowled'])
    lines.append(f"\nCAREER TOTALS (only matches {player.player_name} personally batted or bowled in):")
    lines.append(
        f"  Matches: {career['matches']} | "
        f"Runs: {career['runs']} | Avg: {c_avg} (times out={career['outs']}) | "
        f"SR: {c_sr} | 4s: {career['fours']} | 6s: {career['sixes']}"
    )
    lines.append(
        f"  Wickets: {career['wickets']} | Eco: {c_eco} | "
        f"Catches: {career['catches']} | Run-outs: {career['runouts']} | MOM: {career['mom']}"
    )

    # Career hat-tricks
    try:
        from scoring.models import HatTrick as _HT
        all_hts = _HT.objects.filter(bowler=player).select_related(
            'match__team1','match__team2','match__tournament',
            'victim1','victim2','victim3'
        )
        if all_hts.exists():
            lines.append(f"  HAT-TRICKS (career): {all_hts.count()}")
            for ht in all_hts:
                lines.append(
                    f"    Hat-trick in {ht.match.tournament.tournament_name} "
                    f"({ht.match.team1.team_name} vs {ht.match.team2.team_name}, {ht.match.match_date}): "
                    f"dismissed {ht.victims_display()}"
                )
    except Exception:
        pass

    # Ball-level career batting/bowling breakdown for this player
    try:
        from scoring.models import Ball as _Ball
        career_balls = list(_Ball.objects.filter(
            batsman=player, is_legal_ball=True,
            over__innings__match__tournament__in=[r.tournament for r in rosters]
        ).values_list('runs_off_bat', flat=True))
        if career_balls:
            total_b = len(career_balls)
            fours_c = sum(1 for r in career_balls if r == 4)
            sixes_c = sum(1 for r in career_balls if r >= 6)
            ones_c  = sum(1 for r in career_balls if r == 1)
            twos_c  = sum(1 for r in career_balls if r == 2)
            dots_c  = sum(1 for r in career_balls if r == 0)
            total_runs_c = sum(career_balls)
            bdry_runs = fours_c * 4 + sixes_c * 6
            run_runs  = total_runs_c - bdry_runs
            bdry_pct  = round(bdry_runs / total_runs_c * 100, 1) if total_runs_c else 0
            run_pct   = round(run_runs  / total_runs_c * 100, 1) if total_runs_c else 0
            dot_pct   = round(dots_c / total_b * 100, 1)
            lines.append(
                f"BATTING BREAKDOWN (career ball-level): "
                f"Boundary runs {bdry_pct}% ({bdry_runs}R: {fours_c}x4 {sixes_c}x6) | "
                f"Running {run_pct}% ({run_runs}R: 1s:{ones_c} 2s:{twos_c}) | "
                f"Dot faced {dot_pct}% ({dots_c}/{total_b} balls)"
            )
        bowl_balls_c = list(_Ball.objects.filter(
            bowler=player, is_legal_ball=True,
            over__innings__match__tournament__in=[r.tournament for r in rosters]
        ).values_list('runs_off_bat', 'extra_runs', 'is_wicket', 'wicket_type'))
        if bowl_balls_c:
            tb = len(bowl_balls_c)
            dots_b   = sum(1 for r, e, w, wt in bowl_balls_c if r == 0 and e == 0)
            wkts_b   = sum(1 for r, e, w, wt in bowl_balls_c if w and wt not in ('RUN_OUT','NONE'))
            dot_pct_b = round(dots_b / tb * 100, 1)
            lines.append(
                f"BOWLING BREAKDOWN (career ball-level): "
                f"Dot% {dot_pct_b}% ({dots_b}/{tb} balls) | "
                f"Wickets {wkts_b} | Wicket every {round(tb/wkts_b,1) if wkts_b else 'N/A'} balls"
            )
    except Exception:
        pass

    # Bowlers who dismissed the player most
    if career_dismissed_by:
        sorted_db = sorted(career_dismissed_by.items(), key=lambda x: x[1], reverse=True)
        db_str = ", ".join(f"{name} ({cnt}x)" for name, cnt in sorted_db[:5])
        lines.append(f"DISMISSED BY (most): {db_str}")

    # Follower data
    try:
        lines.append("")
        lines.append(build_follower_context(player))
    except Exception:
        pass

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
#  DETAILED PLAYER CONTEXT  — ball-by-ball + full scorecards
# ─────────────────────────────────────────────────────────────────────────────
def build_player_context_detailed(player):
    from teams.models import TournamentRoster
    from matches.models import CreateMatch
    from scoring.models import BattingScorecard, BowlingScorecard, Ball
    from tournaments.models import TournamentAward

    lines = [f"DETAILED STATS: {player.player_name}"]

    rosters = TournamentRoster.objects.filter(player=player).select_related(
        'tournament', 'tournament_team__team'
    ).order_by('-tournament__start_date')

    if not rosters.exists():
        return "No tournament history."

    for roster in rosters:
        t = roster.tournament
        my_team = roster.tournament_team.team
        lines.append(f"\n=== {t.tournament_name} | {my_team.team_name} ===")

        all_matches = CreateMatch.objects.filter(tournament=t).select_related(
            'team1', 'team2', 'result'
        ).prefetch_related(
            'innings__batting_team', 'innings__bowling_team',
            'innings__batting_scorecard__batsman',
            'innings__bowling_scorecard__bowler',
        ).order_by('match_date')
        my_matches = [m for m in all_matches if m.team1 == my_team or m.team2 == my_team]

        for m in my_matches:
            opponent = m.team2 if m.team1 == my_team else m.team1
            try:
                result_str = m.result.result_summary
            except Exception:
                result_str = "Pending"
            lines.append(f"\nMATCH vs {opponent.team_name} ({m.match_date}) [{result_str}]")

            for inn in m.innings.order_by('innings_number'):
                lines.append(
                    f"  INN{inn.innings_number}: {inn.batting_team.team_name} "
                    f"{inn.total_runs}/{inn.total_wickets} ({inn.overs_completed}ov)"
                )
                for bs in inn.batting_scorecard.select_related('batsman').order_by('batting_position'):
                    me = " <ME>" if bs.batsman_id == player.id else ""
                    d = f" [{bs.dismissal_info}]" if bs.dismissal_info else ""
                    bowler = _extract_bowler_from_dismissal(bs.dismissal_info)
                    bowler_str = f" bowled_by:{bowler}" if bowler else ""
                    lines.append(
                        f"    {bs.batsman.player_name}{me}: {bs.runs}({bs.balls_faced}b) "
                        f"4s:{bs.fours} 6s:{bs.sixes} SR:{bs.strike_rate} [{bs.status}]{d}{bowler_str}"
                    )
                for bws in inn.bowling_scorecard.select_related('bowler').all():
                    me = " <ME>" if bws.bowler_id == player.id else ""
                    lines.append(
                        f"    {bws.bowler.player_name}{me}: {bws.overs_bowled}ov "
                        f"{bws.wickets}W/{bws.runs_given}R eco:{bws.economy}"
                    )

            # Hat-tricks in this match
            try:
                from scoring.models import HatTrick as _HT
                for ht in _HT.objects.filter(match=m).select_related(
                    'bowler','victim1','victim2','victim3'
                ):
                    me = " <ME>" if ht.bowler_id == player.id else ""
                    lines.append(
                        f"  HAT-TRICK{me}: {ht.bowler.player_name} dismissed {ht.victims_display()}"
                    )
            except Exception:
                pass

            # My ball-by-ball detail
            for bs in BattingScorecard.objects.filter(
                innings__match=m, batsman=player
            ).select_related('innings__bowling_team'):
                inn = bs.innings
                lines.append(
                    f"  MY BAT vs {inn.bowling_team.team_name}: "
                    f"{bs.runs}({bs.balls_faced}b) [{bs.status}]"
                )
                if bs.dismissal_info:
                    bowler = _extract_bowler_from_dismissal(bs.dismissal_info)
                    lines.append(f"    Dismissal: {bs.dismissal_info}"
                                 + (f" → bowled by: {bowler}" if bowler else ""))
                six_map = {}
                for b in Ball.objects.filter(
                    over__innings=inn, batsman=player, runs_off_bat=6
                ).select_related('bowler'):
                    six_map[b.bowler.player_name] = six_map.get(b.bowler.player_name, 0) + 1
                if six_map:
                    lines.append("    6s off: " + ", ".join(f"{v} off {k}" for k, v in six_map.items()))
                four_map = {}
                for b in Ball.objects.filter(
                    over__innings=inn, batsman=player, runs_off_bat=4
                ).select_related('bowler'):
                    four_map[b.bowler.player_name] = four_map.get(b.bowler.player_name, 0) + 1
                if four_map:
                    lines.append("    4s off: " + ", ".join(f"{v} off {k}" for k, v in four_map.items()))
                dots = Ball.objects.filter(
                    over__innings=inn, batsman=player, runs_off_bat=0, ball_type='NORMAL'
                ).count()
                lines.append(f"    Dot balls faced: {dots}")

            for bws in BowlingScorecard.objects.filter(
                innings__match=m, bowler=player
            ).select_related('innings__batting_team'):
                inn = bws.innings
                lines.append(
                    f"  MY BOWL vs {inn.batting_team.team_name}: "
                    f"{bws.overs_bowled}ov {bws.wickets}W/{bws.runs_given}R eco:{bws.economy}"
                )
                for d in Ball.objects.filter(
                    over__innings=inn, bowler=player, is_wicket=True
                ).select_related('player_dismissed', 'fielder'):
                    dismissed = d.player_dismissed.player_name if d.player_dismissed else "?"
                    fielder = f" c.{d.fielder.player_name}" if d.fielder else ""
                    lines.append(f"    Wkt: {dismissed} {d.wicket_type}{fielder}")
                dots = Ball.objects.filter(
                    over__innings=inn, bowler=player, total_runs=0, ball_type='NORMAL'
                ).count()
                lines.append(f"    Dot balls bowled: {dots}")

            for c in Ball.objects.filter(
                over__innings__match=m, fielder=player,
                is_wicket=True, wicket_type='CAUGHT'
            ).select_related('player_dismissed'):
                lines.append(f"  FIELDING: Caught {c.player_dismissed.player_name if c.player_dismissed else '?'}")
            for r in Ball.objects.filter(
                over__innings__match=m, fielder=player,
                is_wicket=True, wicket_type='RUN_OUT'
            ).select_related('player_dismissed'):
                lines.append(f"  FIELDING: Run out {r.player_dismissed.player_name if r.player_dismissed else '?'}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
#  ADMIN CONTEXT
# ─────────────────────────────────────────────────────────────────────────────
def build_admin_context():
    from tournaments.models import TournamentDetails
    from teams.models import PlayerDetails

    lines = ["=== STRIKEZONE DATABASE ==="]
    for t in TournamentDetails.objects.prefetch_related(
        'tournament_teams__team', 'tournament_teams__roster__player',
        'matches__result', 'matches__man_of_the_match__player',
        'matches__innings__batting_scorecard__batsman',
        'matches__innings__bowling_scorecard__bowler',
        'matches__innings__batting_team', 'matches__innings__bowling_team',
        'awards__player',
    ).all():
        lines.append(f"\n=== {t.tournament_name} ({t.get_tournament_type_display()}, {t.number_of_overs}ov) ===")
        for tt in t.tournament_teams.all():
            pnames = [r.player.player_name for r in tt.roster.all()]
            lines.append(f"  TEAM {tt.team.team_name}: {', '.join(pnames) or 'No players'}")
        for m in t.matches.all():
            try:
                res = m.result.result_summary
            except Exception:
                res = "Pending"
            lines.append(f"\n  MATCH: {m.team1.team_name} vs {m.team2.team_name} ({m.match_date}) [{res}]")
            for inn in m.innings.all():
                lines.append(
                    f"    INN{inn.innings_number}: {inn.batting_team.team_name} "
                    f"{inn.total_runs}/{inn.total_wickets} ({inn.overs_completed}ov)"
                )
                for bs in inn.batting_scorecard.select_related('batsman').order_by('batting_position'):
                    lines.append(
                        f"      {bs.batsman.player_name}: {bs.runs}({bs.balls_faced}b) "
                        f"4s:{bs.fours} 6s:{bs.sixes} [{bs.status}]"
                    )
                for bws in inn.bowling_scorecard.select_related('bowler').all():
                    lines.append(
                        f"      {bws.bowler.player_name}: {bws.overs_bowled}ov "
                        f"{bws.wickets}W/{bws.runs_given}R eco:{bws.economy}"
                    )
        for aw in t.awards.select_related('player').all():
            lines.append(f"  AWARD {aw.get_award_type_display()}: {aw.player.player_name}")

    lines.append("\n=== PLAYERS ===")
    for p in PlayerDetails.objects.all():
        lines.append(f"  {p.player_name} (ID:{p.id})")

    # Follower counts for all players
    try:
        from accounts.models import PlayerFollow, GuestFollow
        lines.append("\n=== FOLLOWER COUNTS ===")
        for p in PlayerDetails.objects.all():
            pc = PlayerFollow.objects.filter(following=p).count()
            gc = GuestFollow.objects.filter(following=p).count()
            lines.append(f"  {p.player_name}: {pc + gc} total ({pc} players, {gc} guests)")
    except Exception:
        pass

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
#  CACHED CONTEXT BUILDER
# ─────────────────────────────────────────────────────────────────────────────
def get_cached_context(player, context_type):
    """
    Returns cached context string for this player+type.
    Rebuilds and caches if missing or expired (CACHE_TTL seconds).
    """
    cache_key = f"crickbot_ctx_{player.id}_{context_type}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    if context_type == "detailed":
        ctx = build_player_context_detailed(player)
    elif context_type == "tournament_wide":
        ctx = build_tournament_wide_context(player)  # query_message passed separately
    else:
        ctx = build_player_context_compact(player)

    cache.set(cache_key, ctx, CACHE_TTL)
    return ctx


def invalidate_player_cache(player_id):
    """Call this after a new ball/match is recorded to force context rebuild."""
    cache.delete(f"crickbot_ctx_{player_id}_compact")
    cache.delete(f"crickbot_ctx_{player_id}_detailed")
    cache.delete(f"crickbot_ctx_{player_id}_tournament_wide")


# ─────────────────────────────────────────────────────────────────────────────
#  PROACTIVE COACH INSIGHTS  — appended to every response
# ─────────────────────────────────────────────────────────────────────────────
def build_coach_insight(player):
    """
    Returns 1-2 data-driven proactive insights about the player.
    These get added to the system prompt so the AI can surface them naturally.
    """
    from scoring.models import BattingScorecard, BowlingScorecard

    insights = []

    # 1. Most dangerous bowler against this player
    dismissals = {}
    for bs in BattingScorecard.objects.filter(batsman=player, status='OUT'):
        bowler = _extract_bowler_from_dismissal(bs.dismissal_info)
        if bowler:
            dismissals[bowler] = dismissals.get(bowler, 0) + 1
    if dismissals:
        nemesis = max(dismissals, key=dismissals.get)
        insights.append(
            f"⚠️ COACH NOTE: {player.player_name.split()[0]}'s biggest nemesis is "
            f"{nemesis} who has dismissed them {dismissals[nemesis]} time(s). "
            f"Worth mentioning proactively in strategy questions."
        )

    # 2. Bowling economy trend
    bowl_qs = list(BowlingScorecard.objects.filter(bowler=player).order_by('innings__match__match_date'))
    if len(bowl_qs) >= 3:
        recent = bowl_qs[-3:]
        older  = bowl_qs[:-3]
        r_runs = sum(b.runs_given for b in recent)
        r_balls = sum(
            int(float(b.overs_bowled)) * 6 + round((float(b.overs_bowled) % 1) * 10)
            for b in recent
        )
        o_runs = sum(b.runs_given for b in older)
        o_balls = sum(
            int(float(b.overs_bowled)) * 6 + round((float(b.overs_bowled) % 1) * 10)
            for b in older
        )
        r_eco = _eco(r_runs, r_balls)
        o_eco = _eco(o_runs, o_balls)
        if r_eco > o_eco + 1.5:
            insights.append(
                f"📉 COACH NOTE: Bowling economy has worsened recently "
                f"(last 3 spells: {r_eco} vs earlier: {o_eco}). "
                f"Mention if asked about bowling or strategy."
            )
        elif r_eco < o_eco - 1.5:
            insights.append(
                f"📈 COACH NOTE: Bowling economy has improved recently "
                f"(last 3 spells: {r_eco} vs earlier: {o_eco}). Worth praising!"
            )

    # 3. Strike rate trend (last 3 innings vs rest)
    bat_qs = list(BattingScorecard.objects.filter(
        batsman=player
    ).order_by('innings__match__match_date'))
    if len(bat_qs) >= 4:
        recent_bat = bat_qs[-3:]
        older_bat  = bat_qs[:-3]
        r_sr = _sr(sum(b.runs for b in recent_bat), sum(b.balls_faced for b in recent_bat))
        o_sr = _sr(sum(b.runs for b in older_bat),  sum(b.balls_faced for b in older_bat))
        if r_sr < o_sr - 20:
            insights.append(
                f"📉 COACH NOTE: Strike rate has dropped in recent innings "
                f"(last 3: {r_sr} vs earlier: {o_sr}). "
                f"Mention proactively in batting/strategy questions."
            )
        elif r_sr > o_sr + 20:
            insights.append(
                f"🔥 COACH NOTE: Strike rate is trending UP recently "
                f"(last 3: {r_sr} vs earlier: {o_sr}). Great form — praise it!"
            )

    return "\n".join(insights) if insights else ""


# ─────────────────────────────────────────────────────────────────────────────
#  DJANGO VIEWS
# ─────────────────────────────────────────────────────────────────────────────

@require_plan('pro', 'pro_plus')
def crickbot_page(request):
    is_admin = request.user.is_authenticated and (
        request.user.is_staff or request.user.is_superuser
    )
    player_id = request.session.get('player_id')

    if not is_admin and not player_id:
        return redirect('player_login')

    player_name = 'Player'
    if player_id and not is_admin:
        if str(player_id).isdigit():
            from teams.models import PlayerDetails
            try:
                p = PlayerDetails.objects.get(id=int(player_id))
                player_name = p.player_name
            except PlayerDetails.DoesNotExist:
                return redirect('player_login')
        else:
            player_name = request.session.get('player_name', 'Player')

    return render(request, 'crickbot.html', {
        'is_admin': is_admin,
        'player_name': player_name,
    })


@csrf_exempt
@require_plan('pro', 'pro_plus')
def crickbot_chat_api(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)

    is_admin = request.user.is_authenticated and (
        request.user.is_staff or request.user.is_superuser
    )
    player_id = request.session.get('player_id')

    if not is_admin and not player_id:
        return JsonResponse({'error': 'Not authenticated'}, status=401)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    user_message = body.get('message', '').strip()
    history      = body.get('history', [])

    if not user_message:
        return JsonResponse({'error': 'Empty message'}, status=400)

    try:
        if is_admin:
            ctx = build_admin_context()
            system_prompt = (
                "You are CrickBot, the StrikeZone admin assistant. "
                "Answer only using the data below. Be precise.\n\nDATA:\n" + ctx
            )
        else:
            if not str(player_id).isdigit():
                return JsonResponse({'error': 'Guest players cannot use CrickBot'}, status=403)

            from teams.models import PlayerDetails
            player = PlayerDetails.objects.get(id=int(player_id))
            fname  = player.player_name.split()[0]

            # 1. Classify intent — pass last user msg for short follow-up resolution
            last_user_msg = next(
                (h.get('content') for h in reversed(history) if h.get('role') == 'user'),
                None
            )
            intent       = classify_intent(user_message, last_user_msg=last_user_msg)
            context_type = INTENT_CONTEXT_MAP.get(intent, "compact")

            # 2. Get context — social_stats and tournament_wide always fresh, others cached
            if intent == "social_stats":
                ctx = build_follower_context(player)
            elif intent == "tournament_wide":
                combined_q = user_message + (' ' + (last_user_msg or ''))
                ctx = build_tournament_wide_context(player, query_message=combined_q)
            else:
                ctx = get_cached_context(player, context_type)

            # 3. Build coach insights (lightweight, not cached — always fresh)
            coach_notes = ""
            try:
                coach_notes = build_coach_insight(player)
            except Exception:
                pass

            system_prompt = (
                f"You are CrickBot, {fname}'s personal cricket analyst and coach on StrikeZone.\n"
                "Rules:\n"
                "- Answer ONLY from the data provided. Never invent stats.\n"
                "- Be warm and engaging — use 🏏 ⭐ 🎯 🔥 🏆.\n"
                "- NEVER use markdown: no **, no *, no #, no __, no backticks. Plain text only.\n"
                "- CRITICAL: For career/match stats, always use 'Matches played' (matches the player "
                "personally batted or bowled in). NEVER use 'team played' count for the player's matches.\n"
                "- Batting Average = Runs / times OUT (not runs/matches). If never out, average = total runs.\n"
                "- For teammate suggestions: back every claim with actual stats from the data.\n"
                "- If data is missing say 'Not in StrikeZone yet.'\n"
                + (
                    "- You have FULL TOURNAMENT DATA below — answer for ALL players and ALL matches, not just the logged-in player.\n"
                    if intent == "tournament_wide" else
                    "- Proactively surface coach notes below when relevant.\n"
                )
                + "\n"
                + (f"COACH INSIGHTS:\n{coach_notes}\n\n" if coach_notes and intent != "tournament_wide" else "")
                + f"DATA:\n{ctx}"
            )

    except Exception as e:
        return JsonResponse({'error': f'Failed to load data: {str(e)}'}, status=500)

    # Keep last 2 user turns + last assistant reply to stay within token limits
    last_assistant = None
    trimmed_history = []
    for h in reversed(history):
        if h.get('role') == 'assistant' and last_assistant is None:
            last_assistant = h
        elif len(trimmed_history) < 2:
            trimmed_history.insert(0, h)

    messages = [{"role": "system", "content": system_prompt}]
    for h in trimmed_history:
        if h.get('role') in ('user', 'assistant') and h.get('content'):
            messages.append({"role": h['role'], "content": h['content']})
    if last_assistant and last_assistant not in trimmed_history:
        messages.append({"role": "assistant", "content": last_assistant['content']})
    messages.append({"role": "user", "content": user_message})

    # Try primary model first, fall back through alternatives on rate limit errors
    reply = None
    last_error = None
    for model_name in [GROQ_MODEL] + GROQ_FALLBACK_MODELS:
        try:
            resp = groq_client.chat.completions.create(
                model=model_name,
                messages=messages,
                max_tokens=700,
                temperature=0.6,
            )
            reply = resp.choices[0].message.content
            break  # success
        except Exception as e:
            last_error = e
            err_str = str(e)
            # Fall back on rate limits, token limits, or decommissioned models
            if any(x in err_str for x in ['429', '413', 'rate_limit', 'decommissioned', 'model_not_found', 'not supported']):
                continue  # try next model
            else:
                return JsonResponse({'error': f'Groq API error: {err_str}'}, status=500)

    if reply is None:
        return JsonResponse({
            'error': f'All models are currently rate-limited. Please try again in a few minutes. ({last_error})'
        }, status=429)

    return JsonResponse({'reply': reply, 'intent': intent if not is_admin else 'admin'})