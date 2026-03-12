"""
strikezone/views.py  — ROUTER ONLY
───────────────────────────────────
Re-exports every view so urls.py works unchanged.

ACTUAL CODE LIVES IN:
  views_core.py      home, tournaments, tournamentdetails, teamdetails
  views_admin.py     manage_cricket, create_match, load_teams, start_tournament
  views_scoring.py   match_start → scoring → undo → next_over → 2nd innings
  views_match.py     match_result, restart, scorecard, history, awards
  views_auth.py      admin + player auth (login/register/OTP/logout)
  views_player.py    player_stats, player_matches, public_player_profile, edit/delete
  views_knockout.py  all knockout bracket views + leaderboard helpers
  views_awards.py    award engine, UII calculator, MOM
  views_public.py    live APIs, public team profile, global search
  views_analysis.py  player/team/match ML analysis
"""

from .views_core      import admin_required, home, tournaments, tournamentdetails, teamdetails, delete_tournament
from .views_admin     import manage_cricket, create_match, load_teams, start_tournament
from .views_scoring   import (match_start, start_innings_view, scoring_view, record_ball_view,
                               select_new_batsman, undo_ball_view, next_over_view, start_second_innings,
                               update_match_overs)
from .views_match     import match_result, restart_match, tournament_awards, tournament_history, match_scorecard, delete_match
from .views_auth      import (admin_login, admin_logout, player_login, send_otp_sms,
                               player_request_otp, player_verify_otp, player_register, player_logout)
from .views_player    import (player_stats, player_stats_api, player_matches,
                               public_player_profile, edit_player, delete_player,
                               toggle_follow, player_followers_list)
from .views_knockout  import (get_tournament_leaderboard, all_league_matches_completed,
                               knockout_bracket, setup_knockout_stage, start_knockout_match,
                               auto_advance_knockout, link_knockout_matches, public_knockout_bracket)
from .views_awards    import (_is_tournament_complete, _collect_player_stats, _best_batsman_score,
                               _best_bowler_score, award_tournament_awards, calculate_uii, award_man_of_the_match)
from .views_public    import (live_scores_api, flutter_live_matches_api, public_live_scorecard,
                               live_scorecard_api, public_team_profile, global_search_api)
from .views_analysis  import (player_analysis_view, player_analysis_api, team_analysis_view,
                               team_analysis_api, match_analysis_view, match_analysis_api,
                               _build_match_data_prompt)