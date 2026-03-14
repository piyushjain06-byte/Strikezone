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
    """Export match scorecard as a beautiful styled PDF"""
    from matches.models import CreateMatch
    from scoring.models import Innings
    from reportlab.platypus import HRFlowable, KeepTogether
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

    match = CreateMatch.objects.select_related(
        'team1', 'team2', 'tournament', 'result__winner'
    ).get(id=match_id)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=0.6*inch, rightMargin=0.6*inch,
        topMargin=0.6*inch, bottomMargin=0.6*inch
    )
    elements = []

    # ── Colour palette ──────────────────────────────────────
    C_DARK      = colors.HexColor('#0a0f1e')
    C_SURFACE   = colors.HexColor('#111827')
    C_SURFACE2  = colors.HexColor('#1a2235')
    C_ACCENT    = colors.HexColor('#f59e0b')
    C_ACCENT2   = colors.HexColor('#b45309')
    C_TEXT      = colors.HexColor('#e2e8f0')
    C_MUTED     = colors.HexColor('#64748b')
    C_SUCCESS   = colors.HexColor('#10b981')
    C_INFO      = colors.HexColor('#3b82f6')
    C_HDR_BAT   = colors.HexColor('#1e3a8a')   # batting header
    C_HDR_BOWL  = colors.HexColor('#064e3b')   # bowling header
    C_ROW_ALT   = colors.HexColor('#f8fafc')
    C_ROW_EVEN  = colors.HexColor('#ffffff')
    C_BORDER    = colors.HexColor('#cbd5e1')

    # ── Custom styles ────────────────────────────────────────
    def S(name, **kw):
        return ParagraphStyle(name, **kw)

    sTitle = S('sTitle', fontSize=22, fontName='Helvetica-Bold',
               textColor=C_DARK, alignment=TA_CENTER, spaceAfter=4)
    sSubtitle = S('sSubtitle', fontSize=11, fontName='Helvetica',
                  textColor=C_MUTED, alignment=TA_CENTER, spaceAfter=2)
    sMeta = S('sMeta', fontSize=9, fontName='Helvetica',
              textColor=C_MUTED, alignment=TA_CENTER, spaceAfter=2)
    sResult = S('sResult', fontSize=13, fontName='Helvetica-Bold',
                textColor=C_DARK, alignment=TA_CENTER)
    sSection = S('sSection', fontSize=11, fontName='Helvetica-Bold',
                 textColor=C_SURFACE, spaceAfter=4, spaceBefore=10)
    sSectionSub = S('sSectionSub', fontSize=9, fontName='Helvetica',
                    textColor=C_MUTED, spaceAfter=6)
    sFooter = S('sFooter', fontSize=7, fontName='Helvetica',
                textColor=C_MUTED, alignment=TA_CENTER)

    def hr(color=C_ACCENT, thickness=2):
        return HRFlowable(width='100%', thickness=thickness,
                          color=color, spaceAfter=6, spaceBefore=6)

    # ── HEADER BANNER ────────────────────────────────────────
    # Top accent line
    elements.append(HRFlowable(width='100%', thickness=4,
                                color=C_ACCENT, spaceAfter=12, spaceBefore=0))

    # StrikeZone branding
    elements.append(Paragraph('STRIKEZONE', S('brand', fontSize=9,
        fontName='Helvetica-Bold', textColor=C_ACCENT,
        alignment=TA_CENTER, letterSpacing=3, spaceAfter=2)))

    # Match title
    elements.append(Paragraph(
        f"{match.team1.team_name}  vs  {match.team2.team_name}", sTitle))

    elements.append(Paragraph(match.tournament.tournament_name, sSubtitle))

    # Date · Venue · Overs
    overs = getattr(match, 'custom_overs', None) or match.tournament.number_of_overs
    date_str = match.match_date.strftime('%d %B %Y') if match.match_date else '—'
    elements.append(Paragraph(
        f"📅 {date_str}    📍 {match.venue}    🏏 {overs} Overs", sMeta))

    # Toss info
    try:
        toss = match.toss
        toss_line = f"Toss: {toss.toss_winner.team_name} won and chose to {toss.get_decision_display().lower()}"
        elements.append(Paragraph(toss_line, sMeta))
    except Exception:
        pass

    elements.append(Spacer(1, 6))
    elements.append(hr(C_ACCENT, 2))

    # ── RESULT BOX ───────────────────────────────────────────
    try:
        res = match.result
        result_text = res.result_summary or '—'
        mom_text = ''
        if res.man_of_the_match:
            mom_text = f"  ⭐ Man of the Match: {res.man_of_the_match.player_name}"
        result_tbl = Table(
            [[Paragraph(f"🏆  {result_text}{mom_text}", sResult)]],
            colWidths=['100%']
        )
        result_tbl.setStyle(TableStyle([
            ('BACKGROUND',  (0,0), (-1,-1), colors.HexColor('#fffbeb')),
            ('ROUNDEDCORNERS', [6]),
            ('BOX',         (0,0), (-1,-1), 1.5, C_ACCENT),
            ('TOPPADDING',  (0,0), (-1,-1), 10),
            ('BOTTOMPADDING',(0,0),(-1,-1), 10),
            ('LEFTPADDING', (0,0), (-1,-1), 14),
        ]))
        elements.append(result_tbl)
        elements.append(Spacer(1, 10))
    except Exception:
        pass

    # ── INNINGS ──────────────────────────────────────────────
    def styled_table(data, col_widths, header_color, is_batting=True):
        tbl = Table(data, colWidths=col_widths, repeatRows=1)
        style_cmds = [
            # Header row
            ('BACKGROUND',    (0,0), (-1,0), header_color),
            ('TEXTCOLOR',     (0,0), (-1,0), colors.white),
            ('FONTNAME',      (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE',      (0,0), (-1,0), 9),
            ('TOPPADDING',    (0,0), (-1,0), 7),
            ('BOTTOMPADDING', (0,0), (-1,0), 7),
            ('ALIGN',         (0,0), (-1,0), 'CENTER'),
            # Data rows
            ('FONTNAME',      (0,1), (-1,-1), 'Helvetica'),
            ('FONTSIZE',      (0,1), (-1,-1), 8.5),
            ('TOPPADDING',    (0,1), (-1,-1), 5),
            ('BOTTOMPADDING', (0,1), (-1,-1), 5),
            ('ALIGN',         (1,1), (-1,-1), 'CENTER'),
            ('ALIGN',         (0,1), (0,-1),  'LEFT'),
            # Grid
            ('GRID',          (0,0), (-1,-1), 0.4, C_BORDER),
            ('LINEBELOW',     (0,0), (-1,0),  1.2, header_color),
            # Alternating rows
        ]
        # Zebra rows
        for i in range(1, len(data)):
            bg = C_ROW_ALT if i % 2 == 0 else C_ROW_EVEN
            style_cmds.append(('BACKGROUND', (0,i), (-1,i), bg))
        # Bold the name col
        style_cmds.append(('FONTNAME', (0,1), (0,-1), 'Helvetica-Bold'))
        tbl.setStyle(TableStyle(style_cmds))
        return tbl

    page_w = A4[0] - 1.2*inch  # usable width

    for idx, innings in enumerate(match.innings.all()):
        bowling_team = innings.bowling_team.team_name if innings.bowling_team else '—'
        inn_label = f"{'1st' if idx == 0 else '2nd'} Innings — {innings.batting_team.team_name} batting"
        score_line = f"{innings.total_runs}/{innings.total_wickets}  ({innings.overs_completed} overs)"

        # Innings title block
        inn_header = Table(
            [[Paragraph(inn_label, S('ih', fontSize=11, fontName='Helvetica-Bold',
                                     textColor=colors.white)),
              Paragraph(score_line, S('is', fontSize=14, fontName='Helvetica-Bold',
                                      textColor=C_ACCENT, alignment=TA_RIGHT))]],
            colWidths=[page_w*0.6, page_w*0.4]
        )
        inn_header.setStyle(TableStyle([
            ('BACKGROUND',    (0,0), (-1,-1), C_SURFACE),
            ('TOPPADDING',    (0,0), (-1,-1), 9),
            ('BOTTOMPADDING', (0,0), (-1,-1), 9),
            ('LEFTPADDING',   (0,0), (0,-1),  12),
            ('RIGHTPADDING',  (-1,0),(-1,-1), 12),
            ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
        ]))
        elements.append(inn_header)
        elements.append(Spacer(1, 6))

        # ── BATTING ──
        elements.append(Paragraph('BATTING', S('bt', fontSize=8, fontName='Helvetica-Bold',
            textColor=C_INFO, letterSpacing=1.5, spaceAfter=3)))

        bat_rows = [['Batsman', 'Dismissal', 'R', 'B', '4s', '6s', 'SR']]
        for score in innings.batting_scorecard.select_related('batsman').order_by('id'):
            dismissal = score.status if score.status else '—'
            sr = f"{score.strike_rate:.1f}" if score.balls_faced else '—'
            bat_rows.append([
                score.batsman.player_name,
                dismissal,
                str(score.runs),
                str(score.balls_faced or 0),
                str(score.fours or 0),
                str(score.sixes or 0),
                sr,
            ])
        # Extras & total rows
        bat_rows.append(['', '', '', '', '', '', ''])
        bat_rows.append([
            Paragraph('<b>Total</b>', S('t', fontSize=8, fontName='Helvetica-Bold',
                                        textColor=C_DARK)),
            '', 
            Paragraph(f'<b>{innings.total_runs}</b>', S('tv', fontSize=9,
                fontName='Helvetica-Bold', textColor=C_DARK, alignment=TA_CENTER)),
            '', '', '', ''
        ])

        col_w = [page_w*0.28, page_w*0.22, page_w*0.08,
                 page_w*0.08, page_w*0.08, page_w*0.08, page_w*0.08]
        elements.append(styled_table(bat_rows, col_w, C_HDR_BAT, is_batting=True))
        elements.append(Spacer(1, 10))

        # ── BOWLING ──
        elements.append(Paragraph('BOWLING', S('bw', fontSize=8, fontName='Helvetica-Bold',
            textColor=C_SUCCESS, letterSpacing=1.5, spaceAfter=3)))

        bowl_rows = [['Bowler', 'O', 'M', 'R', 'W', 'Econ', 'SR']]
        for bowl in innings.bowling_scorecard.select_related('bowler').order_by('id'):
            econ = f"{bowl.economy:.2f}" if bowl.overs_bowled else '—'
            # Bowling SR = balls / wickets
            try:
                overs_f = float(bowl.overs_bowled)
                balls = int(overs_f) * 6 + round((overs_f % 1) * 10)
                bowl_sr = f"{balls / bowl.wickets:.1f}" if bowl.wickets else '—'
            except Exception:
                bowl_sr = '—'
            bowl_rows.append([
                bowl.bowler.player_name,
                str(bowl.overs_bowled),
                str(getattr(bowl, 'maidens', 0) or 0),
                str(bowl.runs_given),
                str(bowl.wickets),
                econ,
                bowl_sr,
            ])

        col_w2 = [page_w*0.34, page_w*0.09, page_w*0.09,
                  page_w*0.09, page_w*0.09, page_w*0.12, page_w*0.10]
        elements.append(styled_table(bowl_rows, col_w2, C_HDR_BOWL, is_batting=False))
        elements.append(Spacer(1, 16))
        if idx == 0:
            elements.append(hr(C_SURFACE2, 1))

    # ── FOOTER ───────────────────────────────────────────────
    elements.append(hr(C_ACCENT, 1.5))
    gen_time = datetime.now().strftime('%d %b %Y, %I:%M %p')
    elements.append(Paragraph(
        f"Generated by StrikeZone  ·  {gen_time}  ·  strikezone.live",
        sFooter))

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