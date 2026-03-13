# Social Features
from django.db import models
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType


# Note: These models should be added to a new app or existing app
# For now, providing the structure

"""
# Add these models to accounts/models.py or create a new social app

class PlayerFollow(models.Model):
    '''Player following system'''
    follower = models.ForeignKey(
        'teams.PlayerDetails',
        on_delete=models.CASCADE,
        related_name='following'
    )
    following = models.ForeignKey(
        'teams.PlayerDetails',
        on_delete=models.CASCADE,
        related_name='followers'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ('follower', 'following')
        verbose_name_plural = 'Player Follows'
    
    def __str__(self):
        return f"{self.follower.player_name} follows {self.following.player_name}"


class MatchComment(models.Model):
    '''Comments on matches'''
    match = models.ForeignKey(
        'matches.CreateMatch',
        on_delete=models.CASCADE,
        related_name='comments'
    )
    player = models.ForeignKey(
        'teams.PlayerDetails',
        on_delete=models.CASCADE,
        related_name='comments'
    )
    comment = models.TextField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'Match Comments'
    
    def __str__(self):
        return f"Comment by {self.player.player_name} on {self.match}"


class MatchReaction(models.Model):
    '''Reactions to matches (like, love, fire, etc)'''
    REACTION_CHOICES = [
        ('LIKE', '👍'),
        ('LOVE', '❤️'),
        ('FIRE', '🔥'),
        ('CLAP', '👏'),
        ('WOW', '😮'),
    ]
    
    match = models.ForeignKey(
        'matches.CreateMatch',
        on_delete=models.CASCADE,
        related_name='reactions'
    )
    player = models.ForeignKey(
        'teams.PlayerDetails',
        on_delete=models.CASCADE,
        related_name='reactions'
    )
    reaction_type = models.CharField(max_length=10, choices=REACTION_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ('match', 'player')
        verbose_name_plural = 'Match Reactions'
    
    def __str__(self):
        return f"{self.player.player_name} reacted {self.get_reaction_type_display()} to {self.match}"


class PlayerAchievement(models.Model):
    '''Player achievements and badges'''
    ACHIEVEMENT_TYPES = [
        ('CENTURY', '💯 Century'),
        ('HALF_CENTURY', '5️⃣0️⃣ Half Century'),
        ('FIVE_WICKETS', '🎯 5 Wicket Haul'),
        ('HAT_TRICK', '🎩 Hat-Trick'),
        ('GOLDEN_BAT', '🏏 Golden Bat'),
        ('GOLDEN_BALL', '⚾ Golden Ball'),
        ('MOM', '🏅 Man of the Match'),
        ('TOURNAMENT_WINNER', '🏆 Tournament Winner'),
    ]
    
    player = models.ForeignKey(
        'teams.PlayerDetails',
        on_delete=models.CASCADE,
        related_name='achievements'
    )
    achievement_type = models.CharField(max_length=20, choices=ACHIEVEMENT_TYPES)
    match = models.ForeignKey(
        'matches.CreateMatch',
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )
    tournament = models.ForeignKey(
        'tournaments.TournamentDetails',
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )
    description = models.CharField(max_length=200)
    achieved_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-achieved_at']
        verbose_name_plural = 'Player Achievements'
    
    def __str__(self):
        return f"{self.player.player_name} - {self.get_achievement_type_display()}"
"""


def check_and_award_achievements(player_id, match_id):
    """
    Check if player earned any achievements in this match
    Call this after match completion
    """
    from scoring.models import BattingScorecard, BowlingScorecard
    
    achievements = []
    
    # Check batting achievements
    batting = BattingScorecard.objects.filter(
        batsman_id=player_id,
        innings__match_id=match_id
    ).first()
    
    if batting:
        if batting.runs >= 100:
            achievements.append({
                'type': 'CENTURY',
                'description': f'Scored {batting.runs} runs'
            })
        elif batting.runs >= 50:
            achievements.append({
                'type': 'HALF_CENTURY',
                'description': f'Scored {batting.runs} runs'
            })
    
    # Check bowling achievements
    bowling = BowlingScorecard.objects.filter(
        bowler_id=player_id,
        innings__match_id=match_id
    ).first()
    
    if bowling:
        if bowling.wickets >= 5:
            achievements.append({
                'type': 'FIVE_WICKETS',
                'description': f'Took {bowling.wickets} wickets'
            })
    
    # Check for hat-trick
    from scoring.models import HatTrick
    hat_tricks = HatTrick.objects.filter(
        bowler_id=player_id,
        match_id=match_id
    )
    
    if hat_tricks.exists():
        achievements.append({
            'type': 'HAT_TRICK',
            'description': 'Took a hat-trick'
        })
    
    return achievements


def get_player_feed(player_id, limit=20):
    """
    Get activity feed for a player
    Shows recent matches, achievements, followers, etc.
    """
    from matches.models import CreateMatch
    from teams.models import TournamentRoster
    
    # Get recent matches
    roster_entries = TournamentRoster.objects.filter(
        player_id=player_id
    ).values_list('tournament_team__team_id', flat=True)
    
    recent_matches = CreateMatch.objects.filter(
        Q(team1_id__in=roster_entries) | Q(team2_id__in=roster_entries)
    ).select_related('team1', 'team2', 'tournament').order_by('-match_date')[:limit]
    
    feed = []
    for match in recent_matches:
        feed.append({
            'type': 'match',
            'date': match.match_date,
            'data': match
        })
    
    return feed


def generate_share_card_data(match_id):
    """
    Generate data for social media share cards
    Returns: dict with Open Graph meta tags
    """
    from matches.models import CreateMatch
    
    match = CreateMatch.objects.select_related(
        'team1', 'team2', 'tournament', 'result__winner'
    ).get(id=match_id)
    
    title = f"{match.team1.team_name} vs {match.team2.team_name}"
    
    if hasattr(match, 'result') and match.result.winner:
        description = f"{match.result.result_summary} | {match.tournament.tournament_name}"
    else:
        description = f"{match.tournament.tournament_name} | {match.venue}"
    
    return {
        'og:title': title,
        'og:description': description,
        'og:type': 'article',
        'og:url': f'/match/{match_id}/scorecard/',
        'twitter:card': 'summary_large_image',
        'twitter:title': title,
        'twitter:description': description
    }
