from django import forms
from tournaments.models import TournamentDetails
from teams.models import TeamDetails, PlayerDetails
from matches.models import CreateMatch


class TournamentForm(forms.ModelForm):
    class Meta:
        model = TournamentDetails
        fields = [
            'tournament_name',
            'tournament_type',
            'number_of_overs',
            'number_of_teams',
            'start_date',
            'end_date',
        ]


class TeamForm(forms.ModelForm):
    class Meta:
        model = TeamDetails
        fields = "__all__"


class PlayerForm(forms.ModelForm):
    class Meta:
        model = PlayerDetails
        fields = ['player_name', 'team', 'role', 'is_captain',
                  'is_vice_captain', 'jersey_number', 'mobile_number']


class MatchForm(forms.ModelForm):
    class Meta:
        model = CreateMatch
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['team1'].queryset = TeamDetails.objects.none()
        self.fields['team2'].queryset = TeamDetails.objects.none()

        if 'tournament' in self.data:
            try:
                tournament_id = int(self.data.get('tournament'))
                teams = TeamDetails.objects.filter(tournament_id=tournament_id)
                self.fields['team1'].queryset = teams
                self.fields['team2'].queryset = teams
            except (ValueError, TypeError):
                pass
        elif self.instance.pk:
            tournament = self.instance.tournament
            teams = tournament.teams.all()
            self.fields['team1'].queryset = teams
            self.fields['team2'].queryset = teams