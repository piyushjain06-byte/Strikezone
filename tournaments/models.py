from django.db import models


# ---------------------------------
# Upper Category Model
# ---------------------------------
class UpperCategory(models.Model):
    category_name = models.CharField(max_length=30, unique=True)

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

    # ── Venue / Location ──────────────────────────────────────────────
    venue = models.CharField(
        max_length=300,
        blank=True,
        help_text="Full address of the venue (e.g. Wankhede Stadium, Mumbai)"
    )
    venue_lat = models.DecimalField(
        max_digits=10, decimal_places=7,
        null=True, blank=True,
        help_text="Latitude (auto-filled by Google Maps)"
    )
    venue_lng = models.DecimalField(
        max_digits=10, decimal_places=7,
        null=True, blank=True,
        help_text="Longitude (auto-filled by Google Maps)"
    )
    # ──────────────────────────────────────────────────────────────────

    # Who created this tournament
    created_by_player = models.ForeignKey(
        'teams.PlayerDetails',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='tournament_created_by_player',
        help_text='Pro Plus player who created this tournament'
    )
    created_by_admin = models.ForeignKey(
        'auth.User',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='tournament_created_by_admin',
        help_text='Admin/CEO who created this tournament'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Set to True when organiser manually completes the tournament
    is_force_completed = models.BooleanField(
        default=False,
        help_text='Manually mark tournament as completed (e.g. league-only or early completion).'
    )

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

# ---------------------------------
# Tournament Awards Model
# ---------------------------------
class TournamentAward(models.Model):

    AWARD_TYPE = [
        ('MOT',  'Man of the Tournament'),
        ('BBAT', 'Best Batsman'),
        ('BBOL', 'Best Bowler'),
    ]

    tournament = models.ForeignKey(
        TournamentDetails,
        on_delete=models.CASCADE,
        related_name='awards'
    )
    award_type = models.CharField(max_length=10, choices=AWARD_TYPE)
    player = models.ForeignKey(
        'teams.PlayerDetails',
        on_delete=models.CASCADE,
        related_name='tournament_awards'
    )
    score = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Snapshot stats
    total_runs = models.PositiveIntegerField(default=0)
    total_balls_faced = models.PositiveIntegerField(default=0)
    batting_avg = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    batting_sr = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    highest_score = models.PositiveIntegerField(default=0)
    total_wickets = models.PositiveIntegerField(default=0)
    bowling_avg = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    bowling_economy = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    best_bowling = models.CharField(max_length=10, default='0/0')
    matches_played = models.PositiveIntegerField(default=0)

    awarded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('tournament', 'award_type')
        verbose_name = 'Tournament Award'
        verbose_name_plural = 'Tournament Awards'

    def __str__(self):
        return f"{self.get_award_type_display()} — {self.player.player_name} ({self.tournament})"

# ── Tournament Hired Staff ──────────────────────────────────────────────────
class TournamentHire(models.Model):
    """
    Tracks pro_plus players hired by a tournament creator to co-manage
    a specific tournament. Hired players get full manage access for that
    tournament only.
    """
    tournament  = models.ForeignKey(
        TournamentDetails,
        on_delete=models.CASCADE,
        related_name='hired_staff'
    )
    hired_player = models.ForeignKey(
        'teams.PlayerDetails',
        on_delete=models.CASCADE,
        related_name='hired_for_tournaments'
    )
    hired_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('tournament', 'hired_player')
        verbose_name = 'Tournament Hire'
        verbose_name_plural = 'Tournament Hires'

    def __str__(self):
        return f"{self.hired_player.player_name} hired for {self.tournament.tournament_name}"