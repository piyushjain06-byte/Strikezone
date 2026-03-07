import json
from channels.generic.websocket import AsyncWebsocketConsumer


class MatchConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.match_id   = self.scope['url_route']['kwargs']['match_id']
        self.group_name = f"match_{self.match_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def score_update(self, event):
        await self.send(text_data=json.dumps({'type': 'score_update', 'data': event['data']}))

    async def innings_complete(self, event):
        await self.send(text_data=json.dumps({'type': 'innings_complete', 'data': event['data']}))

    async def match_complete(self, event):
        await self.send(text_data=json.dumps({'type': 'match_complete', 'data': event['data']}))

    async def milestone(self, event):
        await self.send(text_data=json.dumps({'type': 'milestone', 'data': event['data']}))

    async def commentary(self, event):
        await self.send(text_data=json.dumps({'type': 'commentary', 'data': event['data']}))

    async def new_batsman(self, event):
        await self.send(text_data=json.dumps({'type': 'new_batsman', 'data': event['data']}))

    async def new_over(self, event):
        await self.send(text_data=json.dumps({'type': 'new_over', 'data': event['data']}))


class HomeLiveConsumer(AsyncWebsocketConsumer):
    GROUP = "home_live"

    async def connect(self):
        await self.channel_layer.group_add(self.GROUP, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        await self.channel_layer.group_discard(self.GROUP, self.channel_name)

    async def home_update(self, event):
        await self.send(text_data=json.dumps({'type': 'home_update', 'matches': event['matches']}))

    async def match_status_change(self, event):
        await self.send(text_data=json.dumps({'type': 'match_status_change', 'data': event['data']}))


class TournamentConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.tournament_id = self.scope['url_route']['kwargs']['tournament_id']
        self.group_name    = f"tournament_{self.tournament_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def tournament_update(self, event):
        await self.send(text_data=json.dumps({'type': 'tournament_update', 'data': event['data']}))