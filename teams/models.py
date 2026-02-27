from django.db import models
from django.core.exceptions import ValidationError

from tournaments.models import TournamentDetails


# ---------------------------------
# Team Model
# ---------------------------------
class TeamDetails(models.Model):
    team_name = models.CharField(max_length=100)
    team_code = models.CharField(
        max_length=12,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
        help_text="Unique team ID (auto-generated). Use this to register team in new tournaments.",
    )

    team_created_date = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'Team Details'

    def __str__(self):
        return self.team_name

    def save(self, *args, **kwargs):
        creating = self.pk is None
        super().save(*args, **kwargs)
        # Generate code after we have a primary key
        if not self.team_code:
            self.team_code = f"TM{self.pk:06d}"
            # Avoid recursion / full save; only update this field
            super().save(update_fields=["team_code"])


# ---------------------------------
# Tournament Team (registration)
# ---------------------------------
class TournamentTeam(models.Model):
    tournament = models.ForeignKey(
        TournamentDetails,
        on_delete=models.CASCADE,
        related_name="tournament_teams",
    )
    team = models.ForeignKey(
        TeamDetails,
        on_delete=models.CASCADE,
        related_name="tournament_entries",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Tournament Teams"
        constraints = [
            models.UniqueConstraint(
                fields=["tournament", "team"],
                name="uniq_tournament_team",
            )
        ]

    def __str__(self):
        return f"{self.tournament} · {self.team}"


# ---------------------------------
# Player Model
# ---------------------------------
class PlayerDetails(models.Model):

    PLAYER_ROLE = [
        ('BATSMAN', 'Batsman'),
        ('BOWLER', 'Bowler'),
        ('ALLROUNDER', 'All-Rounder'),
        ('WICKETKEEPER', 'Wicket Keeper'),
    ]

    player_name = models.CharField(max_length=100)

    mobile_number = models.CharField(
        max_length=15,
        unique=True,
        null=True,
        blank=True,
        help_text="Player's mobile number used for login (e.g. 9876543210)"
    )

    photo = models.ImageField(
        upload_to='player_photos/',
        null=True,
        blank=True,
        help_text='Optional profile photo (jpg, png)'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'Player Details'

    def __str__(self):
        return self.player_name


# ---------------------------------
# Tournament Roster (player assignment)
# ---------------------------------
class TournamentRoster(models.Model):
    tournament_team = models.ForeignKey(
        TournamentTeam,
        on_delete=models.CASCADE,
        related_name="roster",
    )
    # Denormalized for constraints + faster querying
    tournament = models.ForeignKey(
        TournamentDetails,
        on_delete=models.CASCADE,
        related_name="roster_entries",
    )

    player = models.ForeignKey(
        PlayerDetails,
        on_delete=models.CASCADE,
        related_name="tournament_rosters",
    )

    role = models.CharField(
        max_length=20,
        choices=PlayerDetails.PLAYER_ROLE,
        default="BATSMAN",
    )
    is_captain = models.BooleanField(default=False)
    is_vice_captain = models.BooleanField(default=False)
    jersey_number = models.PositiveIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Tournament Roster"
        constraints = [
            # A player can be in only ONE team in the same tournament
            models.UniqueConstraint(
                fields=["tournament", "player"],
                name="uniq_player_per_tournament",
            ),
            models.UniqueConstraint(
                fields=["tournament_team", "player"],
                name="uniq_player_per_tournament_team",
            ),
        ]

    def clean(self):
        if self.tournament_team_id and self.tournament_id:
            if self.tournament_team.tournament_id != self.tournament_id:
                raise ValidationError("TournamentRoster.tournament must match tournament_team.tournament")

    def save(self, *args, **kwargs):
        # Keep tournament in sync automatically
        if self.tournament_team_id:
            self.tournament_id = self.tournament_team.tournament_id
        super().save(*args, **kwargs)