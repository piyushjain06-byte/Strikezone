# Search Functionality
from django.db.models import Q
from teams.models import PlayerDetails, TeamDetails
from matches.models import CreateMatch
from tournaments.models import TournamentDetails


def global_search(query, limit=10):
    """
    Search across players, teams, tournaments, and matches
    Returns: dict with categorized results
    """
    if not query or len(query) < 2:
        return {
            'players': [],
            'teams': [],
            'tournaments': [],
            'matches': []
        }
    
    query = query.strip()
    
    # Search players
    players = PlayerDetails.objects.filter(
        Q(player_name__icontains=query) |
        Q(mobile_number__icontains=query)
    ).select_related().order_by('player_name')[:limit]
    
    # Search teams
    teams = TeamDetails.objects.filter(
        Q(team_name__icontains=query) |
        Q(team_code__icontains=query)
    ).order_by('team_name')[:limit]
    
    # Search tournaments
    tournaments = TournamentDetails.objects.filter(
        tournament_name__icontains=query
    ).order_by('-start_date')[:limit]
    
    # Search matches (by venue or team names)
    matches = CreateMatch.objects.filter(
        Q(venue__icontains=query) |
        Q(team1__team_name__icontains=query) |
        Q(team2__team_name__icontains=query)
    ).select_related('team1', 'team2', 'tournament').order_by('-match_date')[:limit]
    
    return {
        'players': list(players),
        'teams': list(teams),
        'tournaments': list(tournaments),
        'matches': list(matches),
        'total': len(players) + len(teams) + len(tournaments) + len(matches)
    }


def search_players_advanced(filters):
    """
    Advanced player search with filters
    filters = {
        'name': str,
        'role': str,
        'tournament_id': int,
        'team_id': int,
        'min_runs': int,
        'min_wickets': int
    }
    """
    from teams.models import TournamentRoster
    from scoring.models import BattingScorecard, BowlingScorecard
    from django.db.models import Sum
    
    qs = PlayerDetails.objects.all()
    
    if filters.get('name'):
        qs = qs.filter(player_name__icontains=filters['name'])
    
    if filters.get('tournament_id'):
        qs = qs.filter(tournament_rosters__tournament_id=filters['tournament_id'])
    
    if filters.get('team_id'):
        qs = qs.filter(tournament_rosters__tournament_team__team_id=filters['team_id'])
    
    if filters.get('role'):
        qs = qs.filter(tournament_rosters__role=filters['role'])
    
    # Performance filters require aggregation
    if filters.get('min_runs'):
        player_ids = BattingScorecard.objects.values('batsman_id').annotate(
            total=Sum('runs')
        ).filter(total__gte=filters['min_runs']).values_list('batsman_id', flat=True)
        qs = qs.filter(id__in=player_ids)
    
    if filters.get('min_wickets'):
        player_ids = BowlingScorecard.objects.values('bowler_id').annotate(
            total=Sum('wickets')
        ).filter(total__gte=filters['min_wickets']).values_list('bowler_id', flat=True)
        qs = qs.filter(id__in=player_ids)
    
    return qs.distinct()
