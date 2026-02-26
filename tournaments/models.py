from django.db import models

# Create your models here.
from django.db import models


# ---------------------------------
# Upper Category Model
# ---------------------------------
class UpperCategory(models.Model):
    category_name = models.CharField(max_length=30, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'Categories'

    def __str__(self):
        return self.category_name


# ---------------------------------
# Tournament Model
# ---------------------------------
class TournamentDetails(models.Model):

    TOURNAMENT_TYPE = [
        ('BOX_TURF', 'Box Turf'),
        ('OPEN_GROUND', 'Open Ground'),
        ('BOX_GROUND', 'Box Ground'),
    ]

    tournament_name = models.CharField(max_length=100)
    tournament_type = models.CharField(
        max_length=20,
        choices=TOURNAMENT_TYPE,
        default='BOX_TURF'
    )

    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    number_of_overs = models.PositiveIntegerField(default=20)

    number_of_teams = models.PositiveIntegerField(
        default=2,
        help_text="How many teams will participate in this tournament"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'Tournaments'

    def __str__(self):
        return self.tournament_name


# ---------------------------------
# Start Tournament Model
# ---------------------------------
class StartTournament(models.Model):
    tournament = models.OneToOneField(
        TournamentDetails,
        on_delete=models.CASCADE,
        related_name="start_info"
    )

    is_started = models.BooleanField(default=False)
    started_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Start Tournament"

    def __str__(self):
        return f"{self.tournament.tournament_name} - Started: {self.is_started}"