"""
URL configuration for StrikeZone — Cricket Tournament Management System.

Flow:
  1. Core / Public Pages
  2. Admin Authentication
  3. Tournament Management  (admin)
  4. Team & Player Setup    (admin)
  5. Match Lifecycle        (admin)
     5a. Pre-match setup
     5b. Live scoring (AJAX)
     5c. Post-match
  6. Knockout Stage         (admin)
  7. Player Account         (player-facing)
  8. Public Profile Pages   (anyone)
  9. APIs                   (JSON endpoints)
"""

from django.contrib import admin
from django.urls import path,include
from django.conf import settings
from django.conf.urls.static import static
from django.shortcuts import render
from . import views
from . import crickbot_views
from . import views_enhanced
from teams.models import PlayerDetails

urlpatterns = [

    # ─────────────────────────────────────────────────────────────
    # 1. DJANGO ADMIN & CORE PUBLIC PAGES
    # ─────────────────────────────────────────────────────────────
    path('admin/',                          admin.site.urls),
    path('',                                views.home,               name='home'),
    path('tournaments/',                    views.tournaments,        name='tournaments'),
    path('tournaments/<int:tournament_id>/delete/', views.delete_tournament, name='delete_tournament'),
    path('tournaments/<int:id>/',           views.tournamentdetails,  name='tournamentdetails'),

    # ─────────────────────────────────────────────────────────────
    # 2. ADMIN AUTHENTICATION
    # ─────────────────────────────────────────────────────────────
    path('admin-login/',                    views.admin_login,        name='admin_login'),
    path('admin-logout/',                   views.admin_logout,       name='admin_logout'),

    # ─────────────────────────────────────────────────────────────
    # 3. TOURNAMENT MANAGEMENT  (admin only)
    # ─────────────────────────────────────────────────────────────
    path('manage_cricket/',                 views.manage_cricket,     name='manage_cricket'),
    path('start_tournament/',               views.start_tournament,   name='start_tournament'),
    path('tournament/<int:tournament_id>/history/',  views.tournament_history, name='tournament_history'),
    path('tournament/<int:tournament_id>/awards/',   views.tournament_awards,  name='tournament_awards'),
    path('tournament/<int:tournament_id>/force-complete/', views.force_complete_tournament, name='force_complete_tournament'),

    # ─────────────────────────────────────────────────────────────
    # 4. TEAM & PLAYER SETUP  (admin only)
    # ─────────────────────────────────────────────────────────────
    path('tournaments/<int:tournament_id>/team/<int:team_id>/', views.teamdetails, name='teamdetails'),
    path('ajax/load-teams/',                views.load_teams,         name='ajax_load_teams'),

    # ─────────────────────────────────────────────────────────────
    # 5. MATCH LIFECYCLE  (admin only)
    # ─────────────────────────────────────────────────────────────

    # 5a. Pre-match setup — create → toss → open innings
    path('create_match/',                   views.create_match,       name='create_match'),
    path('match_start/',                    views.match_start,        name='match_start'),
    path('match/<int:match_id>/start/',     views.start_innings_view, name='start_innings'),

    # 5b. Live scoring — ball by ball
    path('match/<int:match_id>/scoring/',               views.scoring_view,         name='scoring'),
    path('match/<int:match_id>/record-ball/',            views.record_ball_view,     name='record_ball'),
    path('match/<int:match_id>/next-over/',              views.next_over_view,       name='next_over'),
    path('match/<int:match_id>/select-new-batsman/',     views.select_new_batsman,   name='select_new_batsman'),
    path('match/<int:match_id>/undo-ball/',              views.undo_ball_view,       name='undo_ball'),
    path('match/<int:match_id>/update-overs/',           views.update_match_overs,   name='update_match_overs'),
    path('match/<int:match_id>/start-second-innings/',   views.start_second_innings, name='start_second_innings'),
    path('match/<int:match_id>/restart/',                views.restart_match,        name='restart_match'),
    path('match/<int:match_id>/delete/',                 views.delete_match,         name='delete_match'),

    # 5c. Post-match — result & scorecard
    path('match/<int:match_id>/result/',                 views.match_result,         name='match_result'),
    path('match/<int:match_id>/scorecard/',              views.match_scorecard,      name='match_scorecard'),
    path('match/<int:match_id>/analysis/',               views.match_analysis_view,  name='match_analysis'),
    path('api/match/<int:match_id>/analysis/',           views.match_analysis_api,   name='match_analysis_api'),

    # ─────────────────────────────────────────────────────────────
    # 6. KNOCKOUT STAGE  (admin only)
    # ─────────────────────────────────────────────────────────────
    path('tournament/<int:tournament_id>/knockout/',          views.knockout_bracket,        name='knockout_bracket'),
    path('tournament/<int:tournament_id>/knockout/setup/',    views.setup_knockout_stage,    name='setup_knockout_stage'),
    path('tournament/<int:tournament_id>/knockout/link/',     views.link_knockout_matches,   name='link_knockout_matches'),
    path('tournament/<int:tournament_id>/knockout/public/',   views.public_knockout_bracket, name='public_knockout_bracket'),
    path('knockout-match/<int:knockout_match_id>/start/',     views.start_knockout_match,    name='start_knockout_match'),

    # ─────────────────────────────────────────────────────────────
    # 7. PLAYER ACCOUNT  (player-facing)
    # ─────────────────────────────────────────────────────────────
    path('player/register/',                views.player_register,    name='player_register'),
    path('player/login/',                   views.player_login,       name='player_login'),
    path('player/login/otp/',               views.player_request_otp, name='player_request_otp'),
    path('player/login/otp/verify/',        views.player_verify_otp,  name='player_verify_otp'),
    path('player/logout/',                  views.player_logout,      name='player_logout'),
    path('player/stats/',                   views.player_stats,       name='player_stats'),
    path('player/matches/',                 views.player_matches,     name='player_matches'),

    # ─────────────────────────────────────────────────────────────
    # 8. PUBLIC PROFILE PAGES  (anyone)
    # ─────────────────────────────────────────────────────────────
    path('team/<int:team_id>/',                     views.public_team_profile,   name='public_team_profile'),
    path('team/<int:team_id>/analysis/',            views.team_analysis_view,    name='team_analysis'),
    path('api/team/<int:team_id>/analysis/',        views.team_analysis_api,     name='team_analysis_api'),
    path('player/<int:player_id>/profile/',         views.public_player_profile,  name='public_player_profile'),
    path('player/<int:player_id>/follow/',          views.toggle_follow,          name='toggle_follow'),
    path('player/<int:player_id>/followers/',       views.player_followers_list,  name='player_followers_list'),
    path('player/<int:player_id>/analysis/',        views.player_analysis_view,   name='player_analysis'),
    path('admin/player/<int:player_id>/edit/',      views.edit_player,            name='edit_player'),
    path('admin/player/<int:player_id>/delete/',    views.delete_player,          name='delete_player'),
    path('api/player/<int:player_id>/analysis/',    views.player_analysis_api,    name='player_analysis_api'),
    path('match/<int:match_id>/live/',              views.public_live_scorecard, name='public_live_scorecard'),

    # ─────────────────────────────────────────────────────────────
    # 9. JSON / AJAX APIs
    # ─────────────────────────────────────────────────────────────
    path('api/search/',                             views.global_search_api,  name='global_search_api'),
    path('api/live-scores/',                        views.live_scores_api,          name='live_scores_api'),
    path('api/live-matches/',                       views.flutter_live_matches_api, name='flutter_live_matches_api'),
    path('api/match/<int:match_id>/live/',          views.live_scorecard_api, name='live_scorecard_api'),
    path('player/<int:player_id>/stats-api/',       views.player_stats_api,   name='player_stats_api'),

    # ─────────────────────────────────────────────────────────────
    # 10. CRICKBOT — AI Chat Assistant
    # ─────────────────────────────────────────────────────────────
    path('crickbot/',       crickbot_views.crickbot_page,     name='crickbot'),
    path('crickbot/chat/',  crickbot_views.crickbot_chat_api, name='crickbot_chat'),
    
    
    
     path('subscription/', include('subscriptions.urls')),
     path('employee/',     include('employee.urls')),
     path('ceo/',          include('ceo.urls')),

] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

    # ─────────────────────────────────────────────────────────────
    # 11. ENHANCED FEATURES - Search, Analytics, Admin Tools
    # ─────────────────────────────────────────────────────────────
    
    # Search
    path('search/', lambda request: render(request, 'search_results.html'), name='search_page'),
    path('api/search-enhanced/', views_enhanced.enhanced_search_view, name='search_enhanced'),
    
    # Analytics & Comparison
    path('player-comparison/', lambda request: render(request, 'player_comparison.html', {'all_players': PlayerDetails.objects.all()}), name='player_comparison'),
    path('api/player-comparison/', views_enhanced.player_comparison_view, name='api_player_comparison'),
    path('api/player/<int:player_id>/form/', views_enhanced.player_form_view, name='player_form'),
    path('api/team-h2h/<int:team1_id>/<int:team2_id>/', views_enhanced.team_head_to_head_view, name='team_h2h'),
    path('tournament/<int:tournament_id>/stats/', views_enhanced.tournament_stats_view, name='tournament_stats'),
    
    # Admin Tools
    path('admin/bulk-upload-players/', views_enhanced.bulk_upload_players_view, name='bulk_upload_players'),
    path('match/<int:match_id>/export-pdf/', views_enhanced.export_scorecard_pdf_view, name='export_scorecard_pdf'),
    path('tournament/<int:tournament_id>/export-csv/', views_enhanced.export_tournament_data_view, name='export_tournament_csv'),
    path('tournament/<int:tournament_id>/calendar/', views_enhanced.tournament_calendar_view, name='tournament_calendar'),
    
    # PWA Support
    path('manifest.json', views_enhanced.pwa_manifest_view, name='pwa_manifest'),
    path('service-worker.js', views_enhanced.service_worker_view, name='service_worker'),
    path('install-app/', views_enhanced.install_pwa_view, name='install_pwa'),

