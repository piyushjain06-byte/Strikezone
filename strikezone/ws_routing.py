from django.urls import re_path
from . import ws_consumers

websocket_urlpatterns = [
    re_path(r'^ws/match/(?P<match_id>\d+)/live/$',         ws_consumers.MatchConsumer.as_asgi()),
    re_path(r'^ws/live/$',                                 ws_consumers.HomeLiveConsumer.as_asgi()),
    re_path(r'^ws/tournament/(?P<tournament_id>\d+)/live/$', ws_consumers.TournamentConsumer.as_asgi()),
]