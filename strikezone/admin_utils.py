# Admin Improvements
import csv
from io import StringIO, BytesIO
from django.http import HttpResponse
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from datetime import datetime


def bulk_upload_players(csv_file, tournament_id, team_id):
    """
    Bulk upload players from CSV
    CSV format: player_name, mobile_number, role, jersey_number, is_captain, is_vice_captain
    """
    from teams.models import PlayerDetails, TournamentRoster, TournamentTeam
    
    decoded_file = csv_file.read().decode('utf-8')
    io_string = StringIO(decoded_file)
    reader = csv.DictReader(io_string)
    
    created_count = 0
    updated_count = 0
    errors = []
    
    try:
        tournament_team = TournamentTeam.objects.get(
            tournament_id=tournament_id,
            team_id=team_id
        )
    except TournamentTeam.DoesNotExist:
        return {
            'success': False,
            'error': 'Tournament team not found'
        }
    
    for row_num, row in enumerate(reader, start=2):
        try:
            # Get or create player
            player, created = PlayerDetails.objects.get_or_create(
                mobile_number=row.get('mobile_number'),
                defaults={
                    'player_name': row.get('player_name', '').strip()
                }
            )
            
            if created:
                created_count += 1
            else:
                # Update player name if different
                if player.player_name != row.get('player_name', '').strip():
                    player.player_name = row.get('player_name', '').strip()
                    player.save()
                    updated_count += 1
            
            # Add to tournament roster
            roster, _ = TournamentRoster.objects.get_or_create(
                tournament_team=tournament_team,
                player=player,
                defaults={
                    'role': row.get('role', 'BATSMAN').upper(),
                    'jersey_number': int(row.get('jersey_number', 0)) or None,
                    'is_captain': row.get('is_captain', '').lower() == 'true',
                    'is_vice_captain': row.get('is_vice_captain', '').lower() == 'true'
                }
            )
            
        except Exception as e:
            errors.append(f"Row {row_num}: {str(e)}")
    
    return {
        'success': True,
        'created': created_count,
        'updated': updated_count,
        'errors': errors
    }


def export_scorecard_pdf(match_id):
    """Export match scorecard as PDF"""
    from matches.models import CreateMatch
    from scoring.models import Innings
    
    match = CreateMatch.objects.select_related(
        'team1', 'team2', 'tournament', 'result__winner'
    ).get(id=match_id)
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    elements = []
    styles = getSampleStyleSheet()
    
    # Title
    title = Paragraph(
        f"<b>{match.team1.team_name} vs {match.team2.team_name}</b>",
        styles['Title']
    )
    elements.append(title)
    elements.append(Spacer(1, 0.2*inch))
    
    # Match info
    info = Paragraph(
        f"{match.tournament.tournament_name}<br/>"
        f"Venue: {match.venue}<br/>"
        f"Date: {match.match_date}",
        styles['Normal']
    )
    elements.append(info)
    elements.append(Spacer(1, 0.3*inch))
    
    # Innings data
    for innings in match.innings.all():
        # Innings header
        inn_title = Paragraph(
            f"<b>{innings.batting_team.team_name} - {innings.total_runs}/{innings.total_wickets} ({innings.overs_completed} overs)</b>",
            styles['Heading2']
        )
        elements.append(inn_title)
        elements.append(Spacer(1, 0.1*inch))
        
        # Batting scorecard
        batting_data = [['Batsman', 'R', 'B', '4s', '6s', 'SR']]
        for score in innings.batting_scorecard.all():
            batting_data.append([
                score.batsman.player_name,
                str(score.runs),
                str(score.balls_faced),
                str(score.fours),
                str(score.sixes),
                f"{score.strike_rate:.2f}"
            ])
        
        batting_table = Table(batting_data)
        batting_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        elements.append(batting_table)
        elements.append(Spacer(1, 0.3*inch))
        
        # Bowling scorecard
        bowling_data = [['Bowler', 'O', 'R', 'W', 'Econ']]
        for bowl in innings.bowling_scorecard.all():
            bowling_data.append([
                bowl.bowler.player_name,
                str(bowl.overs_bowled),
                str(bowl.runs_given),
                str(bowl.wickets),
                f"{bowl.economy:.2f}"
            ])
        
        bowling_table = Table(bowling_data)
        bowling_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        elements.append(bowling_table)
        elements.append(Spacer(1, 0.4*inch))
    
    # Result
    if hasattr(match, 'result'):
        result = Paragraph(
            f"<b>Result: {match.result.result_summary}</b>",
            styles['Heading3']
        )
        elements.append(result)
    
    doc.build(elements)
    buffer.seek(0)
    return buffer


def export_tournament_csv(tournament_id):
    """Export tournament data as CSV"""
    from matches.models import CreateMatch
    
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="tournament_{tournament_id}_data.csv"'
    
    writer = csv.writer(response)
    writer.writerow(['Match', 'Date', 'Venue', 'Team1', 'Team2', 'Winner', 'Margin'])
    
    matches = CreateMatch.objects.filter(
        tournament_id=tournament_id
    ).select_related('team1', 'team2', 'result__winner')
    
    for match in matches:
        winner = match.result.winner.team_name if hasattr(match, 'result') and match.result.winner else 'N/A'
        margin = match.result.win_margin if hasattr(match, 'result') else 'N/A'
        
        writer.writerow([
            f"{match.team1.team_name} vs {match.team2.team_name}",
            match.match_date,
            match.venue,
            match.team1.team_name,
            match.team2.team_name,
            winner,
            margin
        ])
    
    return response


def get_tournament_calendar_data(tournament_id):
    """Get tournament matches in calendar format"""
    from matches.models import CreateMatch
    from collections import defaultdict
    
    matches = CreateMatch.objects.filter(
        tournament_id=tournament_id
    ).select_related('team1', 'team2').order_by('match_date')
    
    calendar_data = defaultdict(list)
    
    for match in matches:
        date_str = match.match_date.strftime('%Y-%m-%d')
        calendar_data[date_str].append({
            'id': match.id,
            'team1': match.team1.team_name,
            'team2': match.team2.team_name,
            'venue': match.venue,
            'time': match.match_date.strftime('%H:%M') if hasattr(match.match_date, 'hour') else '00:00'
        })
    
    return dict(calendar_data)
