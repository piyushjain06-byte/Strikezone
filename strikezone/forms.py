from django import forms
from tournaments.models import TournamentDetails
from teams.models import TeamDetails, PlayerDetails, TournamentTeam, TournamentRoster
from matches.models import CreateMatch


class TournamentForm(forms.ModelForm):
    # Venue fields — venue_lat and venue_lng are hidden, filled by Google Maps JS
    venue = forms.CharField(
        max_length=300,
        required=False,
        widget=forms.TextInput(attrs={
            'id': 'venue-autocomplete',
            'placeholder': 'Search for a ground or address…',
            'autocomplete': 'off',
        }),
        label='Venue / Ground Address',
    )
    venue_lat = forms.DecimalField(
        max_digits=10, decimal_places=7, required=False,
        widget=forms.HiddenInput(attrs={'id': 'venue-lat'}),
    )
    venue_lng = forms.DecimalField(
        max_digits=10, decimal_places=7, required=False,
        widget=forms.HiddenInput(attrs={'id': 'venue-lng'}),
    )

    class Meta:
        model = TournamentDetails
        fields = [
            'tournament_name',
            'tournament_type',
            'number_of_overs',
            'number_of_teams',
            'start_date',
            'end_date',
            'venue',
            'venue_lat',
            'venue_lng',
        ]


class TeamForm(forms.Form):
    tournament = forms.ModelChoiceField(queryset=TournamentDetails.objects.all())
    team_code = forms.CharField(
        max_length=12,
        required=False,
        help_text="If team already exists, enter Team ID (e.g. TM000123) to register it in this tournament.",
    )
    team_name = forms.CharField(
        max_length=100,
        required=False,
        help_text="If creating a brand new team, enter team name (can be same as others).",
    )
    team_created_date = forms.DateField(required=False)

    def clean(self):
        cleaned = super().clean()
        code = (cleaned.get("team_code") or "").strip()
        name = (cleaned.get("team_name") or "").strip()
        if not code and not name:
            raise forms.ValidationError("Enter Team ID or Team Name.")
        return cleaned


class _TeamChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        code = getattr(obj, "team_code", "") or ""
        if code:
            return f"{code} · {obj.team_name}"
        return obj.team_name


class PlayerForm(forms.Form):
    tournament = forms.ModelChoiceField(queryset=TournamentDetails.objects.all())
    team = _TeamChoiceField(queryset=TeamDetails.objects.all())

    player_name = forms.CharField(max_length=100, required=False)
    mobile_number = forms.CharField(
        max_length=15,
        required=True,
        error_messages={"required": "Mobile number is required to add a player."}
    )
    photo = forms.ImageField(required=False)

    role = forms.ChoiceField(choices=PlayerDetails.PLAYER_ROLE, initial="BATSMAN")
    is_captain = forms.BooleanField(required=False)
    is_vice_captain = forms.BooleanField(required=False)
    jersey_number = forms.IntegerField(required=False, min_value=0)

    def clean_mobile_number(self):
        mobile = (self.cleaned_data.get("mobile_number") or "").strip()
        if not mobile:
            raise forms.ValidationError("Mobile number is required to add a player.")
        if not mobile.isdigit():
            raise forms.ValidationError("Mobile number must contain digits only.")
        if len(mobile) < 10 or len(mobile) > 15:
            raise forms.ValidationError("Enter a valid mobile number (10–15 digits).")
        return mobile

    def clean(self):
        cleaned = super().clean()
        # Name is optional — if not provided, view will auto-set it from mobile number.
        return cleaned


class MatchForm(forms.ModelForm):
    class Meta:
        model = CreateMatch
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['team1'].queryset = TeamDetails.objects.none()
        self.fields['team2'].queryset = TeamDetails.objects.none()
        # Show team code in dropdown labels
        self.fields['team1'].label_from_instance = lambda obj: f"{obj.team_code} · {obj.team_name}" if getattr(obj, "team_code", None) else obj.team_name
        self.fields['team2'].label_from_instance = lambda obj: f"{obj.team_code} · {obj.team_name}" if getattr(obj, "team_code", None) else obj.team_name

        if 'tournament' in self.data:
            try:
                tournament_id = int(self.data.get('tournament'))
                teams = TeamDetails.objects.filter(tournament_entries__tournament_id=tournament_id).distinct()
                self.fields['team1'].queryset = teams
                self.fields['team2'].queryset = teams
            except (ValueError, TypeError):
                pass
        elif self.instance.pk:
            tournament = self.instance.tournament
            teams = TeamDetails.objects.filter(tournament_entries__tournament=tournament).distinct()
            self.fields['team1'].queryset = teams
            self.fields['team2'].queryset = teams