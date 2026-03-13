# Analytics & Insights
from django.db.models import Sum, Avg, Count, Q, F
from scoring.models import BattingScorecard, BowlingScorecard, Innings
from matches.models import CreateMatch
from datetime import datetime, timedelta


def get_player_form(player_id, last_n_matches=5):
    """Get player's recent form (last N matches)"""
    batting = BattingScorecard.objects.filter(
        batsman_id=player_id
    ).select_related('innings__match').order_by('-innings__match__match_date')[:last_n_matches]
    
    form_data = []
    for score in batting:
        form_data.append({
            'match': f"{score.innings.match.team1.team_name} vs {score.innings.match.team2.team_name}",
            'date': score.innings.match.match_date,
            'runs': score.runs,
            'balls': score.balls_faced,
            'sr': score.strike_rate,
            'fours': score.fours,
            'sixes': score.sixes,
            'status': score.status
        })
    
    return form_data


def get_player_comparison(player1_id, player2_id, tournament_id=None):
    """Compare two players' statistics"""
    
    def get_stats(player_id):
        bat_filter = Q(batsman_id=player_id)
        bowl_filter = Q(bowler_id=player_id)
        
        if tournament_id:
            bat_filter &= Q(innings__match__tournament_id=tournament_id)
            bowl_filter &= Q(innings__match__tournament_id=tournament_id)
        
        batting = BattingScorecard.objects.filter(bat_filter).aggregate(
            matches=Count('innings', distinct=True),
            runs=Sum('runs'),
            balls=Sum('balls_faced'),
            fours=Sum('fours'),
            sixes=Sum('sixes'),
            avg=Avg('runs'),
            highest=Max('runs')
        )
        
        bowling = BowlingScorecard.objects.filter(bowl_filter).aggregate(
            wickets=Sum('wickets'),
            runs_given=Sum('runs_given'),
            overs=Sum('overs_bowled')
        )
        
        # Calculate strike rate and economy
        if batting['balls'] and batting['balls'] > 0:
            batting['strike_rate'] = round((batting['runs'] / batting['balls']) * 100, 2)
        else:
            batting['strike_rate'] = 0
        
        if bowling['overs'] and float(bowling['overs']) > 0:
            bowling['economy'] = round(bowling['runs_given'] / float(bowling['overs']), 2)
        else:
            bowling['economy'] = 0
        
        return {'batting': batting, 'bowling': bowling}
    
    from django.db.models import Max
    
    return {
        'player1': get_stats(player1_id),
        'player2': get_stats(player2_id)
    }


def get_team_head_to_head(team1_id, team2_id):
    """Get head-to-head statistics between two teams"""
    matches = CreateMatch.objects.filter(
        Q(team1_id=team1_id, team2_id=team2_id) |
        Q(team1_id=team2_id, team2_id=team1_id)
    ).select_related('result__winner')
    
    total_matches = matches.count()
    team1_wins = matches.filter(result__winner_id=team1_id).count()
    team2_wins = matches.filter(result__winner_id=team2_id).count()
    ties = matches.filter(result__result_type='TIE').count()
    
    # Recent form (last 5 matches)
    recent = matches.order_by('-match_date')[:5]
    recent_results = []
    for match in recent:
        if hasattr(match, 'result'):
            recent_results.append({
                'date': match.match_date,
                'winner': match.result.winner.team_name if match.result.winner else 'Tie',
                'margin': match.result.win_margin,
                'venue': match.venue
            })
    
    return {
        'total_matches': total_matches,
        'team1_wins': team1_wins,
        'team2_wins': team2_wins,
        'ties': ties,
        'recent_results': recent_results
    }


def get_tournament_progression(tournament_id):
    """Get tournament progression data for charts"""
    matches = CreateMatch.objects.filter(
        tournament_id=tournament_id
    ).order_by('match_date')
    
    progression = []
    for match in matches:
        if hasattr(match, 'result'):
            innings = match.innings.all()
            if innings:
                progression.append({
                    'match_number': match.id,
                    'date': match.match_date,
                    'teams': f"{match.team1.team_name} vs {match.team2.team_name}",
                    'total_runs': sum(inn.total_runs for inn in innings),
                    'total_wickets': sum(inn.total_wickets for inn in innings),
                    'winner': match.result.winner.team_name if match.result.winner else 'Tie'
                })
    
    return progression


def get_strike_rate_trends(player_id, tournament_id=None):
    """Get player's strike rate trends over time"""
    filter_q = Q(batsman_id=player_id)
    if tournament_id:
        filter_q &= Q(innings__match__tournament_id=tournament_id)
    
    scores = BattingScorecard.objects.filter(
        filter_q
    ).select_related('innings__match').order_by('innings__match__match_date')
    
    trends = []
    for score in scores:
        if score.balls_faced > 0:
            trends.append({
                'date': score.innings.match.match_date,
                'match': f"{score.innings.match.team1.team_name} vs {score.innings.match.team2.team_name}",
                'strike_rate': score.strike_rate,
                'runs': score.runs,
                'balls': score.balls_faced
            })
    
    return trends


def get_tournament_leaderboard_enhanced(tournament_id):
    """Enhanced leaderboard with multiple categories"""
    
    # Most runs
    top_batsmen = BattingScorecard.objects.filter(
        innings__match__tournament_id=tournament_id
    ).values('batsman__id', 'batsman__player_name').annotate(
        total_runs=Sum('runs'),
        matches=Count('innings', distinct=True),
        avg=Avg('runs'),
        highest=Max('runs'),
        total_balls=Sum('balls_faced'),
        fours=Sum('fours'),
        sixes=Sum('sixes')
    ).order_by('-total_runs')[:10]
    
    # Most wickets
    top_bowlers = BowlingScorecard.objects.filter(
        innings__match__tournament_id=tournament_id
    ).values('bowler__id', 'bowler__player_name').annotate(
        total_wickets=Sum('wickets'),
        matches=Count('innings', distinct=True),
        runs_given=Sum('runs_given'),
        overs=Sum('overs_bowled')
    ).order_by('-total_wickets')[:10]
    
    # Best strike rate (min 50 balls)
    best_sr = BattingScorecard.objects.filter(
        innings__match__tournament_id=tournament_id
    ).values('batsman__id', 'batsman__player_name').annotate(
        total_runs=Sum('runs'),
        total_balls=Sum('balls_faced')
    ).filter(total_balls__gte=50).order_by('-total_runs')[:10]
    
    for player in best_sr:
        if player['total_balls'] > 0:
            player['strike_rate'] = round((player['total_runs'] / player['total_balls']) * 100, 2)
    
    best_sr = sorted(best_sr, key=lambda x: x.get('strike_rate', 0), reverse=True)[:10]
    
    # Best economy (min 10 overs)
    best_economy = BowlingScorecard.objects.filter(
        innings__match__tournament_id=tournament_id
    ).values('bowler__id', 'bowler__player_name').annotate(
        runs_given=Sum('runs_given'),
        overs=Sum('overs_bowled'),
        wickets=Sum('wickets')
    ).filter(overs__gte=10).order_by('runs_given')[:10]
    
    for bowler in best_economy:
        if bowler['overs'] and float(bowler['overs']) > 0:
            bowler['economy'] = round(bowler['runs_given'] / float(bowler['overs']), 2)
    
    best_economy = sorted(best_economy, key=lambda x: x.get('economy', 999))[:10]
    
    from django.db.models import Max
    
    return {
        'top_batsmen': list(top_batsmen),
        'top_bowlers': list(top_bowlers),
        'best_strike_rate': best_sr,
        'best_economy': best_economy
    }
