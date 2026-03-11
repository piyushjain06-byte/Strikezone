from django.db import models
from matches.models import CreateMatch
from teams.models import TeamDetails, PlayerDetails


# ---------------------------------
# Innings Model
# ---------------------------------
class Innings(models.Model):

    INNINGS_NUMBER = [
        (1, "1st Innings"),
        (2, "2nd Innings"),
    ]

    STATUS_CHOICES = [
        ("NOT_STARTED", "Not Started"),
        ("IN_PROGRESS", "In Progress"),
        ("COMPLETED", "Completed"),
    ]

    match = models.ForeignKey(
        CreateMatch,
        on_delete=models.CASCADE,
        related_name="innings"
    )

    innings_number = models.PositiveIntegerField(choices=INNINGS_NUMBER)

    batting_team = models.ForeignKey(
        TeamDetails,
        on_delete=models.CASCADE,
        related_name="batting_innings"
    )

    bowling_team = models.ForeignKey(
        TeamDetails,
        on_delete=models.CASCADE,
        related_name="bowling_innings"
    )

    total_runs = models.PositiveIntegerField(default=0)
    total_wickets = models.PositiveIntegerField(default=0)
    total_balls = models.PositiveIntegerField(default=0)
    extras = models.PositiveIntegerField(default=0)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="NOT_STARTED"
    )

    target = models.PositiveIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Innings"
        unique_together = ("match", "innings_number")

    @property
    def overs_completed(self):
        balls = self.total_balls
        return f"{balls // 6}.{balls % 6}"

    @property
    def max_overs(self):
        # Use match-specific override if set, otherwise fall back to tournament default
        try:
            custom = self.match.match_start.custom_overs
            if custom:
                return custom
        except Exception:
            pass
        return self.match.tournament.number_of_overs

    @property
    def max_wickets(self):
        return 10

    def __str__(self):
        return f"{self.match} - Innings {self.innings_number} ({self.batting_team})"


# ---------------------------------
# Over Model
# ---------------------------------
class Over(models.Model):
    innings = models.ForeignKey(
        Innings,
        on_delete=models.CASCADE,
        related_name="overs"
    )

    over_number = models.PositiveIntegerField()

    bowler = models.ForeignKey(
        PlayerDetails,
        on_delete=models.CASCADE,
        related_name="bowled_overs"
    )

    is_completed = models.BooleanField(default=False)

    runs_in_over = models.PositiveIntegerField(default=0)
    wickets_in_over = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("innings", "over_number")

    def __str__(self):
        return f"Over {self.over_number} - {self.innings}"


# ---------------------------------
# Ball Model (Ball by Ball)
# ---------------------------------
class Ball(models.Model):

    BALL_TYPE_CHOICES = [
        ("NORMAL", "Normal"),
        ("WIDE", "Wide"),
        ("NO_BALL", "No Ball"),
        ("BYE", "Bye"),
        ("LEG_BYE", "Leg Bye"),
    ]

    WICKET_TYPE_CHOICES = [
        ("NONE", "None"),
        ("BOWLED", "Bowled"),
        ("CAUGHT", "Caught"),
        ("LBW", "LBW"),
        ("RUN_OUT", "Run Out"),
        ("STUMPED", "Stumped"),
        ("HIT_WICKET", "Hit Wicket"),
        ("CAUGHT_AND_BOWLED", "Caught and Bowled"),
    ]

    over = models.ForeignKey(
        Over,
        on_delete=models.CASCADE,
        related_name="balls"
    )

    ball_number = models.PositiveIntegerField()

    batsman = models.ForeignKey(
        PlayerDetails,
        on_delete=models.CASCADE,
        related_name="faced_balls"
    )

    bowler = models.ForeignKey(
        PlayerDetails,
        on_delete=models.CASCADE,
        related_name="bowled_balls"
    )

    runs_off_bat = models.PositiveIntegerField(default=0)
    extra_runs = models.PositiveIntegerField(default=0)
    total_runs = models.PositiveIntegerField(default=0)

    ball_type = models.CharField(
        max_length=10,
        choices=BALL_TYPE_CHOICES,
        default="NORMAL"
    )

    is_wicket = models.BooleanField(default=False)

    wicket_type = models.CharField(
        max_length=20,
        choices=WICKET_TYPE_CHOICES,
        default="NONE"
    )

    player_dismissed = models.ForeignKey(
        PlayerDetails,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dismissals"
    )

    fielder = models.ForeignKey(
        PlayerDetails,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fielding_dismissals"
    )

    # Wagon wheel — shot direction (for ML, not shown to players)
    SHOT_DIRECTION_CHOICES = [
        ('FINE_LEG',       'Fine Leg'),
        ('SQUARE_LEG',     'Square Leg'),
        ('MID_WICKET',     'Mid Wicket'),
        ('MID_ON',         'Mid On'),
        ('STRAIGHT',       'Straight'),
        ('MID_OFF',        'Mid Off'),
        ('COVER',          'Cover'),
        ('POINT',          'Point'),
        ('THIRD_MAN',      'Third Man'),
        ('LONG_ON',        'Long On'),
        ('LONG_OFF',       'Long Off'),
        ('FINE_LEG_DEEP',  'Fine Leg Deep'),
    ]
    shot_direction = models.CharField(
        max_length=20,
        choices=SHOT_DIRECTION_CHOICES,
        null=True, blank=True
    )

    is_legal_ball = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("over", "ball_number")
        ordering = ["over__over_number", "ball_number"]

    def clean(self):
        if self.ball_type in ["WIDE", "NO_BALL"]:
            self.is_legal_ball = False
        else:
            self.is_legal_ball = True

    def save(self, *args, **kwargs):
        self.clean()
        self.total_runs = self.runs_off_bat + self.extra_runs
        super().save(*args, **kwargs)
        self._update_over()
        self._update_innings()

    def _update_over(self):
        over = self.over
        balls = over.balls.all()
        over.runs_in_over = sum(b.total_runs for b in balls)
        over.wickets_in_over = sum(1 for b in balls if b.is_wicket)
        legal_balls = balls.filter(is_legal_ball=True).count()
        if legal_balls >= 6:
            over.is_completed = True
        over.save()

    def _update_innings(self):
        innings = self.over.innings
        balls = Ball.objects.filter(over__innings=innings)

        innings.total_runs = sum(b.total_runs for b in balls)
        innings.total_wickets = balls.filter(is_wicket=True).exclude(wicket_type="RETIRED_HURT").count()
        innings.total_balls = balls.filter(is_legal_ball=True).count()
        innings.extras = sum(b.extra_runs for b in balls)

        max_balls = innings.max_overs * 6

        if innings.total_wickets >= innings.max_wickets:
            innings.status = "COMPLETED"
        elif innings.total_balls >= max_balls:
            innings.status = "COMPLETED"
        elif innings.target and innings.total_runs >= innings.target:
            innings.status = "COMPLETED"
        else:
            innings.status = "IN_PROGRESS"

        innings.save()

    def __str__(self):
        return f"Over {self.over.over_number}, Ball {self.ball_number} - {self.over.innings}"


# ---------------------------------
# Batting Scorecard Model
# ---------------------------------
class BattingScorecard(models.Model):

    OUT_STATUS = [
        ("NOT_OUT", "Not Out"),
        ("OUT", "Out"),
        ("DNB", "Did Not Bat"),
    ]

    innings = models.ForeignKey(
        Innings,
        on_delete=models.CASCADE,
        related_name="batting_scorecard"
    )

    batsman = models.ForeignKey(
        PlayerDetails,
        on_delete=models.CASCADE,
        related_name="batting_scores"
    )

    runs = models.PositiveIntegerField(default=0)
    balls_faced = models.PositiveIntegerField(default=0)
    fours = models.PositiveIntegerField(default=0)
    sixes = models.PositiveIntegerField(default=0)

    status = models.CharField(
        max_length=10,
        choices=OUT_STATUS,
        default="NOT_OUT"
    )

    dismissal_info = models.CharField(max_length=200, blank=True, null=True)

    batting_position = models.PositiveIntegerField()

    class Meta:
        unique_together = ("innings", "batsman")
        ordering = ["batting_position"]

    @property
    def strike_rate(self):
        if self.balls_faced == 0:
            return 0.0
        return round((self.runs / self.balls_faced) * 100, 2)

    def __str__(self):
        return f"{self.batsman.player_name} - {self.runs} ({self.balls_faced}b)"


# ---------------------------------
# Bowling Scorecard Model
# ---------------------------------
class BowlingScorecard(models.Model):

    innings = models.ForeignKey(
        Innings,
        on_delete=models.CASCADE,
        related_name="bowling_scorecard"
    )

    bowler = models.ForeignKey(
        PlayerDetails,
        on_delete=models.CASCADE,
        related_name="bowling_scores"
    )

    overs_bowled = models.DecimalField(max_digits=4, decimal_places=1, default=0.0)
    runs_given = models.PositiveIntegerField(default=0)
    wickets = models.PositiveIntegerField(default=0)
    wides = models.PositiveIntegerField(default=0)
    no_balls = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("innings", "bowler")

    @property
    def economy(self):
        """Economy = (Runs Conceded x 6) / Total Legal Balls Bowled
        overs_bowled stored as X.Y where Y=extra balls. e.g. 4.3 = 27 balls.
        """
        overs = float(self.overs_bowled)
        if overs == 0:
            return 0.0
        full_overs = int(overs)
        extra_balls = round((overs - full_overs) * 10)
        total_balls = full_overs * 6 + extra_balls
        if total_balls == 0:
            return 0.0
        return round((self.runs_given * 6) / total_balls, 2)

    def __str__(self):
        return f"{self.bowler.player_name} - {self.wickets}W/{self.runs_given}R"