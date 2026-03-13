# Performance Optimization Utilities
from django.core.cache import cache
from django.db.models import Prefetch, Count, Sum, Avg, Q
from functools import wraps
import hashlib
import json

def cache_result(timeout=300):
    """
    Decorator to cache function results
    Usage: @cache_result(timeout=600)
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Create cache key from function name and arguments
            cache_key = f"{func.__name__}_{hashlib.md5(str(args).encode() + str(kwargs).encode()).hexdigest()}"
            
            result = cache.get(cache_key)
            if result is None:
                result = func(*args, **kwargs)
                cache.set(cache_key, result, timeout)
            return result
        return wrapper
    return decorator


def invalidate_cache_pattern(pattern):
    """Invalidate all cache keys matching pattern"""
    # Note: This requires Redis backend for pattern matching
    # For simple cache, you'll need to track keys manually
    pass


def get_optimized_matches(tournament_id=None):
    """Get matches with optimized queries"""
    from matches.models import CreateMatch
    
    qs = CreateMatch.objects.select_related(
        'tournament',
        'team1',
        'team2',
        'match_start__toss_winner',
        'match_start__batting_team',
        'match_start__bowling_team',
        'result__winner'
    ).prefetch_related(
        'innings__batting_scorecard__batsman',
        'innings__bowling_scorecard__bowler'
    )
    
    if tournament_id:
        qs = qs.filter(tournament_id=tournament_id)
    
    return qs.order_by('-match_date')


def get_player_stats_optimized(player_id):
    """Get player statistics with single query"""
    from scoring.models import BattingScorecard, BowlingScorecard
    from django.db.models import Sum, Avg, Count
    
    batting_stats = BattingScorecard.objects.filter(
        batsman_id=player_id
    ).aggregate(
        total_runs=Sum('runs'),
        total_balls=Sum('balls_faced'),
        total_fours=Sum('fours'),
        total_sixes=Sum('sixes'),
        matches=Count('innings', distinct=True),
        avg_runs=Avg('runs')
    )
    
    bowling_stats = BowlingScorecard.objects.filter(
        bowler_id=player_id
    ).aggregate(
        total_wickets=Sum('wickets'),
        total_runs_given=Sum('runs_given'),
        total_overs=Sum('overs_bowled'),
        matches=Count('innings', distinct=True)
    )
    
    return {
        'batting': batting_stats,
        'bowling': bowling_stats
    }
