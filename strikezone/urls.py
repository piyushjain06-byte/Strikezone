"""
URL configuration for strikezone project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from . import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('',views.home,name='home'),
    path('tournaments/',views.tournaments, name='tournaments',),
    path('tournaments/<int:id>/', views.tournamentdetails, name ='tournamentdetails'),
    path(
    'tournaments/<int:tournament_id>/team/<int:team_id>/',views.teamdetails,name='teamdetails'),
    path('ajax/load-teams/', views.load_teams, name='ajax_load_teams'),

    path('manage_cricket/', views.manage_cricket, name='manage_cricket'),
     path('create_match/', views.create_match, name='create_match'),
     path('start_tournament/', views.start_tournament,name='start_tournament'),
     path('match_start/', views.match_start,name="match_start"),
     
     # Step 1: Start innings - pick openers and first bowler
    path('match/<int:match_id>/start/', views.start_innings_view, name='start_innings'),

    # Step 2: Live scoring page
    path('match/<int:match_id>/scoring/', views.scoring_view, name='scoring'),

    # Step 3: AJAX - record each ball
    path('match/<int:match_id>/record-ball/', views.record_ball_view, name='record_ball'),

    # Step 4: AJAX - start next over (pick new bowler)
    path('match/<int:match_id>/next-over/', views.next_over_view, name='next_over'),
    
    path('match/<int:match_id>/result/', views.match_result, name='match_result'),
    path(
    'match/<int:match_id>/start-second-innings/',
    views.start_second_innings,
    name='start_second_innings'
),
    path('match/<int:match_id>/select-new-batsman/', views.select_new_batsman, name='select_new_batsman'),
    path('match/<int:match_id>/restart/', views.restart_match, name='restart_match'),
    
    path('tournament/<int:tournament_id>/history/', views.tournament_history, name='tournament_history'),
    path('match/<int:match_id>/scorecard/', views.match_scorecard, name='match_scorecard'),
    
    path('player/register/', views.player_register, name='player_register'),
    path('player/login/',  views.player_login,  name='player_login'),
    path('player/logout/', views.player_logout, name='player_logout'),
    path('player/stats/',  views.player_stats,  name='player_stats'),
    path('player/matches/', views.player_matches, name='player_matches'),
    
    path('player/<int:player_id>/stats-api/', views.player_stats_api, name='player_stats_api'),

    path('admin-login/',  views.admin_login,  name='admin_login'),
    path('admin-logout/', views.admin_logout, name='admin_logout'),
    
    # ── KNOCKOUT BRACKET URLS ──
path('tournament/<int:tournament_id>/knockout/', views.knockout_bracket, name='knockout_bracket'),
path('tournament/<int:tournament_id>/knockout/setup/', views.setup_knockout_stage, name='setup_knockout_stage'),
path('tournament/<int:tournament_id>/knockout/link/', views.link_knockout_matches, name='link_knockout_matches'),
path('tournament/<int:tournament_id>/knockout/public/', views.public_knockout_bracket, name='public_knockout_bracket'),
path('knockout-match/<int:knockout_match_id>/start/', views.start_knockout_match, name='start_knockout_match'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)