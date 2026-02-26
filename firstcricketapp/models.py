from django.db import models
from django.core.exceptions import ValidationError


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
# In your models.py, update TournamentDetails to add number_of_teams:

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

    # ── NEW ──
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

# After updating models.py, run:
# python manage.py makemigrations
# python manage.py migrate

# ---------------------------------
# Team Model
# ---------------------------------
class TeamDetails(models.Model):
    team_name = models.CharField(max_length=100)

    tournament = models.ForeignKey(
        TournamentDetails,
        on_delete=models.CASCADE,
        related_name="teams"
    )

    team_created_date = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'Team Details'

    def __str__(self):
        return self.team_name

class PlayerDetails(models.Model):

    PLAYER_ROLE = [
        ('BATSMAN', 'Batsman'),
        ('BOWLER', 'Bowler'),
        ('ALLROUNDER', 'All-Rounder'),
        ('WICKETKEEPER', 'Wicket Keeper'),
    ]

    player_name = models.CharField(max_length=100)

    team = models.ForeignKey(
        TeamDetails,
        on_delete=models.CASCADE,
        related_name="players"
    )

    role = models.CharField(
        max_length=20,
        choices=PLAYER_ROLE,
        default='BATSMAN'
    )

    is_captain = models.BooleanField(default=False)
    is_vice_captain = models.BooleanField(default=False)

    jersey_number = models.PositiveIntegerField(null=True, blank=True)

    # ── NEW: Mobile number for player login ──
    mobile_number = models.CharField(
        max_length=15,
        unique=True,          # each player has one unique number
        null=True,            # null=True so existing players don't break
        blank=True,
        help_text="Player's mobile number used for login (e.g. 9876543210)"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'Player Details'

    def __str__(self):
        return self.player_name
    
    
class CreateMatch(models.Model):
    tournament = models.ForeignKey(TournamentDetails,on_delete=models.CASCADE,related_name='matches')
    team1 = models.ForeignKey(TeamDetails,on_delete=models.CASCADE,related_name='team1_matches')
    team2 = models.ForeignKey(TeamDetails,on_delete=models.CASCADE,related_name='team2_matches')
    match_date = models.DateField()
    venue = models.CharField(max_length=200)
    
    class Meta:
        verbose_name_plural = 'Create Matches for a Tournament'

    def clean(self):
        if self.team1 == self.team2:
            raise ValidationError("Both the Teams cannot be the Same")
        
        if self.team1.tournament != self.tournament:
            raise ValidationError(f"The selected Team1 must me of the {self.tournament}")
        
        if self.team2.tournament != self.tournament:
            raise ValidationError(f"The selected Team2 must me of the {self.tournament}")
        
    def __str__(self):
        return f"{self.team1} vs {self.team2}"
        
        
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
    



class MatchStart(models.Model):
    match = models.OneToOneField(
        CreateMatch,
        on_delete=models.CASCADE,
        related_name="match_start"
    )

    toss_winner = models.ForeignKey(
        TeamDetails,
        on_delete=models.CASCADE,
        related_name="won_toss_matches"
    )

    DECISION_CHOICES = [
        ("BAT", "BAT"),
        ("BOWL", "BOWL"),
    ]

    decision = models.CharField(
        max_length=10,
        choices=DECISION_CHOICES
    )

    # Automatically determined
    toss_loser = models.ForeignKey(
        TeamDetails,
        on_delete=models.CASCADE,
        related_name="lost_toss_matches",
        blank=True,
        null=True
    )

    batting_team = models.ForeignKey(
        TeamDetails,
        on_delete=models.CASCADE,
        related_name="batting_matches",
        blank=True,
        null=True
    )

    bowling_team = models.ForeignKey(
        TeamDetails,
        on_delete=models.CASCADE,
        related_name="bowling_matches",
        blank=True,
        null=True
    )

    is_match_started = models.BooleanField(default=False)
    started_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Match Start"

    def clean(self):
        # 1️⃣ Tournament must be started
        if self.match and hasattr(self.match.tournament, "start_info"):
            if not self.match.tournament.start_info.is_started:
                raise ValidationError(
                    "Cannot start match. Tournament is not started yet."
                )

        # 2️⃣ Toss winner must be one of the match teams
        if self.match and self.toss_winner:
            if self.toss_winner not in [self.match.team1, self.match.team2]:
                raise ValidationError(
                    "Toss winner must be one of the two teams playing."
                )

    def save(self, *args, **kwargs):

        # ✅ Safety check: only run logic if required fields exist
        if self.match and self.toss_winner and self.decision:

            # Determine toss loser safely
            if self.toss_winner == self.match.team1:
                self.toss_loser = self.match.team2
            elif self.toss_winner == self.match.team2:
                self.toss_loser = self.match.team1

            # Assign batting & bowling automatically
            if self.decision == "BAT":
                self.batting_team = self.toss_winner
                self.bowling_team = self.toss_loser
            elif self.decision == "BOWL":
                self.bowling_team = self.toss_winner
                self.batting_team = self.toss_loser

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.match} - {self.toss_winner} chose to {self.decision}"
    
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
    total_balls = models.PositiveIntegerField(default=0)  # legal balls only
    extras = models.PositiveIntegerField(default=0)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="NOT_STARTED"
    )

    # For 2nd innings: target to chase
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
        return self.match.tournament.number_of_overs

    @property
    def max_wickets(self):
        return 10  # 10 wickets per innings

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

    over_number = models.PositiveIntegerField()  # 1-based

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

    ball_number = models.PositiveIntegerField()  # ball within the over (can exceed 6 for extras)

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

    runs_off_bat = models.PositiveIntegerField(default=0)  # runs scored by batsman
    extra_runs = models.PositiveIntegerField(default=0)    # wides, no balls, byes, leg byes
    total_runs = models.PositiveIntegerField(default=0)    # runs_off_bat + extra_runs

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

    # Player dismissed (if any)
    player_dismissed = models.ForeignKey(
        PlayerDetails,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dismissals"
    )

    # Fielder involved in dismissal (catch, run out, stumping)
    fielder = models.ForeignKey(
        PlayerDetails,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fielding_dismissals"
    )

    is_legal_ball = models.BooleanField(default=True)  # Wides/NoBalls are not legal

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("over", "ball_number")
        ordering = ["over__over_number", "ball_number"]

    def clean(self):
        # Wides and No Balls are not legal deliveries
        if self.ball_type in ["WIDE", "NO_BALL"]:
            self.is_legal_ball = False
        else:
            self.is_legal_ball = True

    def save(self, *args, **kwargs):
        self.clean()
        self.total_runs = self.runs_off_bat + self.extra_runs
        super().save(*args, **kwargs)

        # Update over stats
        self._update_over()
        # Update innings stats
        self._update_innings()

    def _update_over(self):
        over = self.over
        balls = over.balls.all()
        over.runs_in_over = sum(b.total_runs for b in balls)
        over.wickets_in_over = sum(1 for b in balls if b.is_wicket)

        # Over is complete when 6 legal balls bowled
        legal_balls = balls.filter(is_legal_ball=True).count()
        if legal_balls >= 6:
            over.is_completed = True

        over.save()

    def _update_innings(self):
        innings = self.over.innings
        balls = Ball.objects.filter(over__innings=innings)

        innings.total_runs = sum(b.total_runs for b in balls)
        innings.total_wickets = balls.filter(is_wicket=True).count()
        innings.total_balls = balls.filter(is_legal_ball=True).count()
        innings.extras = sum(b.extra_runs for b in balls)

        max_balls = innings.max_overs * 6

        # Check innings end conditions
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

    batting_position = models.PositiveIntegerField()  # 1 = opener

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
        if float(self.overs_bowled) == 0:
            return 0.0
        return round(self.runs_given / float(self.overs_bowled), 2)

    def __str__(self):
        return f"{self.bowler.player_name} - {self.wickets}W/{self.runs_given}R"


# ---------------------------------
# Match Result Model
# ---------------------------------
class MatchResult(models.Model):

    RESULT_TYPE = [
        ("WIN_BY_RUNS", "Win by Runs"),
        ("WIN_BY_WICKETS", "Win by Wickets"),
        ("TIE", "Tie"),
        ("NO_RESULT", "No Result"),
    ]

    match = models.OneToOneField(
        CreateMatch,
        on_delete=models.CASCADE,
        related_name="result"
    )

    winner = models.ForeignKey(
        TeamDetails,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="won_matches"
    )

    result_type = models.CharField(max_length=20, choices=RESULT_TYPE)

    win_margin = models.PositiveIntegerField(null=True, blank=True)  # runs or wickets

    result_summary = models.CharField(max_length=300, blank=True)  # e.g. "Team A won by 5 wickets"

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.result_summary
    
# ── Add this model to your models.py ──

class GuestUser(models.Model):
    mobile_number = models.CharField(max_length=15, unique=True)
    password      = models.CharField(max_length=128)  # stored as plain text (simple app)
    created_at    = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Guest: {self.mobile_number}"
    
# ---------------------------------
# Knockout Stage Model
# ---------------------------------
class KnockoutStage(models.Model):

    STAGE_CHOICES = [
        ('PQF', 'Pre Quarter Final'),
        ('QF',  'Quarter Final'),
        ('SF',  'Semi Final'),
        ('F',   'Final'),
    ]

    tournament = models.ForeignKey(
        TournamentDetails,
        on_delete=models.CASCADE,
        related_name='knockout_stages'
    )

    stage = models.CharField(max_length=10, choices=STAGE_CHOICES)

    # Order in which stages are played (1=PQF, 2=QF, 3=SF, 4=Final)
    stage_order = models.PositiveIntegerField(default=1)

    is_completed = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('tournament', 'stage')
        ordering = ['stage_order']
        verbose_name_plural = 'Knockout Stages'

    def __str__(self):
        return f"{self.tournament.tournament_name} - {self.get_stage_display()}"


# ---------------------------------
# Knockout Match Model
# ---------------------------------
class KnockoutMatch(models.Model):

    stage = models.ForeignKey(
        KnockoutStage,
        on_delete=models.CASCADE,
        related_name='matches'
    )

    match_number = models.PositiveIntegerField()  # e.g. QF1, QF2, QF3

    # Teams — can be null initially (filled after previous round completes)
    team1 = models.ForeignKey(
        TeamDetails,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='knockout_team1_matches'
    )

    team2 = models.ForeignKey(
        TeamDetails,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='knockout_team2_matches'
    )

    # Label shown on bracket e.g. "TOP 1" or "PQF1 Winner"
    team1_label = models.CharField(max_length=50, blank=True)
    team2_label = models.CharField(max_length=50, blank=True)

    # Links to actual CreateMatch when match is played
    match = models.OneToOneField(
        CreateMatch,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='knockout_match'
    )

    winner = models.ForeignKey(
        TeamDetails,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='knockout_wins'
    )

    # Which next match does winner feed into
    next_match = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='fed_by'
    )

    venue = models.CharField(max_length=200, blank=True)
    match_date = models.DateField(null=True, blank=True)

    is_completed = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('stage', 'match_number')
        ordering = ['match_number']
        verbose_name_plural = 'Knockout Matches'

    def __str__(self):
        return f"{self.stage.get_stage_display()} Match {self.match_number} - {self.stage.tournament.tournament_name}"
    
