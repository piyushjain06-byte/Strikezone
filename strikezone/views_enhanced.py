# Enhanced Views - Integrating all new features
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from django.db.models import Q

from .search_utils import global_search, search_players_advanced
from .analytics_utils import (
    get_player_form, get_player_comparison,
    get_team_head_to_head, get_tournament_progression,
    get_strike_rate_trends, get_tournament_leaderboard_enhanced
)
from .admin_utils import (
    bulk_upload_players, export_scorecard_pdf,
    export_tournament_csv, get_tournament_calendar_data
)
from .mobile_utils import is_mobile_request, get_device_type

# Import security utils but handle if cache not configured
try:
    from .security_utils import rate_limit, validate_field, sanitize_input
except Exception:
    # Fallback if cache not configured
    def rate_limit(key, limit=10, period=60):
        def decorator(func):
            return func
        return decorator
    
    def sanitize_input(text, max_length=500):
        return text[:max_length].strip()
    
    def validate_field(field_name, value):
        return True, value

# ═══════════════════════════════════════════════════════════
# SEARCH VIEWS
# ═══════════════════════════════════════════════════════════

@rate_limit('search', limit=30, period=60)
def enhanced_search_view(request):
    """Enhanced global search - navbar dropdown with players, teams, tournaments, matches"""
    from django.db.models import Count, Q
    from teams.models import PlayerDetails, TeamDetails
    from tournaments.models import TournamentDetails
    from matches.models import CreateMatch

    query = request.GET.get('q', '').strip()

    if len(query) < 1:
        return JsonResponse({'tournaments': [], 'teams': [], 'players': [], 'matches': []})

    query = sanitize_input(query, max_length=100)

    # Tournaments
    tournaments_qs = TournamentDetails.objects.filter(
        tournament_name__icontains=query
    ).order_by('tournament_name')[:5]
    t_results = [
        {
            'id': t.id,
            'name': t.tournament_name,
            'sub': f"{t.get_tournament_type_display()} · {t.number_of_overs} overs",
        }
        for t in tournaments_qs
    ]

    # Teams
    teams_qs = TeamDetails.objects.filter(
        team_name__icontains=query
    ).annotate(
        match_count=Count('team1_matches', distinct=True) + Count('team2_matches', distinct=True)
    ).order_by('-match_count', 'team_name')[:5]
    team_results = [
        {
            'id': t.id,
            'name': t.team_name,
            'sub': f"{t.match_count} matches played",
        }
        for t in teams_qs
    ]

    # Players
    players_qs = PlayerDetails.objects.filter(
        player_name__icontains=query
    ).annotate(
        match_count=Count(
            'tournament_rosters__tournament_team__tournament__matches',
            distinct=True
        )
    ).order_by('-match_count', 'player_name')[:5]

    player_results = []
    for p in players_qs:
        photo_url = p.photo.url if p.photo else None
        roster = p.tournament_rosters.select_related('tournament_team__team').order_by('-id').first()
        team_name = roster.tournament_team.team.team_name if roster else 'No team'
        player_results.append({
            'id': p.id,
            'name': p.player_name,
            'photo': photo_url,
            'sub': team_name,
        })

    # Matches — search by venue or team name
    matches_qs = CreateMatch.objects.filter(
        Q(venue__icontains=query) |
        Q(team1__team_name__icontains=query) |
        Q(team2__team_name__icontains=query)
    ).select_related('team1', 'team2').order_by('-match_date')[:5]

    match_results = []
    for m in matches_qs:
        status = getattr(m, 'status', '') or ''
        status_label = 'Live' if status == 'live' else ('Done' if status == 'completed' else m.match_date.strftime('%d %b %Y') if m.match_date else '')
        match_results.append({
            'id': m.id,
            'name': f"{m.team1.team_name} vs {m.team2.team_name}",
            'sub': f"{m.venue} · {status_label}",
            'status': status,
        })

    return JsonResponse({
        'tournaments': t_results,
        'teams': team_results,
        'players': player_results,
        'matches': match_results,
    })



# ═══════════════════════════════════════════════════════════
# ANALYTICS VIEWS
# ═══════════════════════════════════════════════════════════

def player_form_view(request, player_id):
    """Player form tracker — renders a beautiful page"""
    from django.shortcuts import get_object_or_404
    from teams.models import PlayerDetails

    player = get_object_or_404(PlayerDetails, id=player_id)
    raw = get_player_form(player_id, last_n_matches=5)

    # Enrich each entry with performance class, bar height, label
    best = max((e['runs'] for e in raw), default=1) or 1
    total_runs = sum(e['runs'] for e in raw)
    total_balls = sum(e['balls'] or 0 for e in raw)
    total_fours = sum(e['fours'] or 0 for e in raw)
    total_sixes = sum(e['sixes'] or 0 for e in raw)
    count = len(raw)
    avg_runs = round(total_runs / count, 1) if count else 0
    avg_sr   = round((total_runs / total_balls) * 100, 1) if total_balls else 0

    def perf(runs):
        if runs >= 50: return ('hot',   '🔥 Fire',  '#ef4444')
        if runs >= 25: return ('good',  '✅ Good',  '#10b981')
        if runs >= 10: return ('avg',   '〜 Decent','#f59e0b')
        return           ('cold',  '❄️ Poor',  '#64748b')

    form_data = []
    for e in raw:
        cls, lbl, color = perf(e['runs'])
        pct = max(int((e['runs'] / best) * 100), 5)
        form_data.append({**e, 'perf_class': cls, 'perf_label': lbl,
                          'bar_pct': pct, 'bar_color': color,
                          'balls': e['balls'] or 0,
                          'sr': e['sr'] or 0,
                          'fours': e['fours'] or 0,
                          'sixes': e['sixes'] or 0})

    # Overall form score
    if avg_runs >= 40:  form_class, form_label, form_score = 'hot',  'HOT',  '🔥'
    elif avg_runs >= 22: form_class, form_label, form_score = 'good', 'GOOD', '✅'
    elif avg_runs >= 10: form_class, form_label, form_score = 'avg',  'AVG',  '〜'
    else:               form_class, form_label, form_score = 'cold', 'COLD', '❄️'

    headlines = {
        'hot':  (f"On Fire! Averaging {avg_runs} per innings",
                 f"{player.player_name} is in brilliant touch right now. Opponents beware."),
        'good': (f"Solid Form — {avg_runs} average",
                 f"Consistent and reliable. {player.player_name} is in good nick."),
        'avg':  (f"Decent — but room to grow",
                 f"{player.player_name} has had some starts but not converting big."),
        'cold': (f"Tough patch — averaging {avg_runs}",
                 f"{player.player_name} is going through a lean phase. A big innings due."),
    }
    form_headline, form_description = headlines[form_class]

    return render(request, 'player_form.html', {
        'player': player,
        'form_data': form_data,
        'form_class': form_class,
        'form_label': form_label,
        'form_score': form_score,
        'form_headline': form_headline,
        'form_description': form_description,
        'total_runs': total_runs,
        'avg_runs': avg_runs,
        'avg_sr': avg_sr,
        'best_score': best if raw else 0,
        'total_fours': total_fours,
        'total_sixes': total_sixes,
    })


def player_comparison_view(request, p1_id, p2_id):
    """Full player comparison page with rich stats"""
    from django.shortcuts import get_object_or_404
    from django.db.models import Sum, Avg, Count, Max, Q
    from teams.models import PlayerDetails, TournamentRoster
    from scoring.models import BattingScorecard, BowlingScorecard

    p1 = get_object_or_404(PlayerDetails, id=p1_id)
    p2 = get_object_or_404(PlayerDetails, id=p2_id)

    def get_full_stats(player):
        bat = BattingScorecard.objects.filter(batsman=player).exclude(status='DNB')
        bowl = BowlingScorecard.objects.filter(bowler=player)

        bat_agg = bat.aggregate(
            innings=Count('id'),
            runs=Sum('runs'),
            balls=Sum('balls_faced'),
            fours=Sum('fours'),
            sixes=Sum('sixes'),
        )
        high_row = bat.order_by('-runs').values_list('runs', flat=True).first()
        outs   = bat.filter(status='OUT').count()
        runs   = bat_agg['runs'] or 0
        balls  = bat_agg['balls'] or 0
        fours  = bat_agg['fours'] or 0
        sixes  = bat_agg['sixes'] or 0
        inn    = bat_agg['innings'] or 0
        high   = high_row or 0
        avg    = round(runs / outs, 2)   if outs   else runs
        sr     = round((runs / balls) * 100, 2) if balls else 0
        fifties = bat.filter(runs__gte=50, runs__lt=100).count()
        hundreds= bat.filter(runs__gte=100).count()
        ducks   = bat.filter(runs=0, status='OUT').count()
        dot_pct = 0
        try:
            from scoring.models import DeliveryBall
            total_faced = DeliveryBall.objects.filter(
                batsman=player, extras_type__isnull=True).count()
            dots = DeliveryBall.objects.filter(
                batsman=player, runs_off_bat=0, extras_type__isnull=True).count()
            dot_pct = round((dots / total_faced)*100, 1) if total_faced else 0
        except Exception:
            pass

        bowl_agg = bowl.aggregate(
            wickets=Sum('wickets'),
            runs_given=Sum('runs_given'),
            overs=Sum('overs_bowled'),
            innings_b=Count('id'),
        )
        wkts       = bowl_agg['wickets'] or 0
        runs_given = bowl_agg['runs_given'] or 0
        overs_f    = float(bowl_agg['overs'] or 0)
        overs_balls= int(overs_f)*6 + round((overs_f % 1)*10)
        econ  = round(runs_given / overs_f, 2)     if overs_f  else 0
        bowl_avg  = round(runs_given / wkts, 2)    if wkts     else 0
        bowl_sr   = round(overs_balls / wkts, 2)   if wkts     else 0
        four_wkts = bowl.filter(wickets__gte=4).count()
        five_wkts = bowl.filter(wickets__gte=5).count()

        # Recent form last 5
        recent = list(bat.select_related('innings__match__team1','innings__match__team2')
                      .order_by('-innings__match__match_date')[:5]
                      .values_list('runs', flat=True))

        # Role from latest roster
        roster = TournamentRoster.objects.filter(player=player).order_by('-id').first()
        role = roster.get_role_display() if roster else 'Batsman'
        team = roster.tournament_team.team.team_name if roster else '—'

        return {
            'player': player,
            'role': role, 'team': team,
            # batting
            'innings': inn, 'runs': runs, 'avg': avg, 'sr': sr,
            'highest': high, 'fours': fours, 'sixes': sixes,
            'fifties': fifties, 'hundreds': hundreds, 'ducks': ducks,
            'dot_pct': dot_pct,
            # bowling
            'wickets': wkts, 'econ': econ, 'bowl_avg': bowl_avg,
            'bowl_sr': bowl_sr, 'four_wkts': four_wkts, 'five_wkts': five_wkts,
            'overs': round(overs_f, 1),
            # form sparkline
            'recent': recent,
        }

    s1 = get_full_stats(p1)
    s2 = get_full_stats(p2)

    def winner(v1, v2, higher_is_better=True):
        """Return 1, 2, or 0 (tie)"""
        try:
            f1, f2 = float(v1), float(v2)
            if f1 == f2: return 0
            return 1 if (f1 > f2) == higher_is_better else 2
        except Exception:
            return 0

    rows = [
        # (label, s1_val, s2_val, higher_is_better)
        ('Innings Played', s1['innings'],  s2['innings'],  True),
        ('Total Runs',     s1['runs'],     s2['runs'],     True),
        ('Batting Average',s1['avg'],      s2['avg'],      True),
        ('Strike Rate',    s1['sr'],       s2['sr'],       True),
        ('Highest Score',  s1['highest'],  s2['highest'],  True),
        ('50s',            s1['fifties'],  s2['fifties'],  True),
        ('100s',           s1['hundreds'], s2['hundreds'], True),
        ('Ducks',          s1['ducks'],    s2['ducks'],    False),
        ('Fours',          s1['fours'],    s2['fours'],    True),
        ('Sixes',          s1['sixes'],    s2['sixes'],    True),
        ('Wickets',        s1['wickets'],  s2['wickets'],  True),
        ('Economy Rate',   s1['econ'],     s2['econ'],     False),
        ('Bowling Average',s1['bowl_avg'], s2['bowl_avg'], False),
        ('Bowling SR',     s1['bowl_sr'],  s2['bowl_sr'],  False),
        ('4-Wkt Hauls',    s1['four_wkts'],s2['four_wkts'],True),
        ('5-Wkt Hauls',    s1['five_wkts'],s2['five_wkts'],True),
    ]

    comparison_rows = []
    p1_wins = p2_wins = 0
    for label, v1, v2, hib in rows:
        w = winner(v1, v2, hib)
        if w == 1: p1_wins += 1
        elif w == 2: p2_wins += 1
        comparison_rows.append({
            'label': label, 'v1': v1, 'v2': v2, 'winner': w
        })

    # Bar chart widths (0-100) for batting stats
    def bar(val, other):
        try:
            total = float(val) + float(other)
            return round((float(val)/total)*100) if total else 50
        except Exception:
            return 50

    return render(request, 'player_comparison_new.html', {
        's1': s1, 's2': s2,
        'rows': comparison_rows,
        'p1_wins': p1_wins, 'p2_wins': p2_wins,
        'bar': bar,
    })


def team_head_to_head_view(request, team1_id, team2_id):
    """Team head-to-head statistics"""
    h2h = get_team_head_to_head(team1_id, team2_id)
    return JsonResponse(h2h)


def tournament_stats_view(request, tournament_id):
    """Enhanced tournament statistics"""
    leaderboard = get_tournament_leaderboard_enhanced(tournament_id)
    progression = get_tournament_progression(tournament_id)
    
    return render(request, 'tournament_stats_enhanced.html', {
        'tournament_id': tournament_id,
        'leaderboard': leaderboard,
        'progression': progression
    })



# ═══════════════════════════════════════════════════════════
# ADMIN ENHANCEMENT VIEWS
# ═══════════════════════════════════════════════════════════

@require_http_methods(["POST"])
def bulk_upload_players_view(request):
    """Bulk upload players from CSV"""
    if not request.user.is_staff:
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    
    csv_file = request.FILES.get('csv_file')
    tournament_id = request.POST.get('tournament_id')
    team_id = request.POST.get('team_id')
    
    if not csv_file:
        return JsonResponse({'error': 'No file uploaded'}, status=400)
    
    result = bulk_upload_players(csv_file, tournament_id, team_id)
    
    if result['success']:
        return JsonResponse({
            'success': True,
            'message': f"Created: {result['created']}, Updated: {result['updated']}",
            'errors': result['errors']
        })
    else:
        return JsonResponse({'error': result['error']}, status=400)


def export_scorecard_pdf_view(request, match_id):
    """Export scorecard as PDF — downloads file then user stays on scorecard page"""
    from matches.models import CreateMatch
    try:
        match = CreateMatch.objects.select_related('team1', 'team2').get(id=match_id)
        filename = f"scorecard_{match.team1.team_name}_vs_{match.team2.team_name}.pdf"
        filename = filename.replace(' ', '_').replace('/', '-')
    except Exception:
        filename = f"scorecard_match_{match_id}.pdf"

    pdf_buffer = export_scorecard_pdf(match_id)

    response = HttpResponse(pdf_buffer, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def export_tournament_data_view(request, tournament_id):
    """Export tournament data as CSV"""
    return export_tournament_csv(tournament_id)


def tournament_calendar_view(request, tournament_id):
    """Tournament calendar view"""
    calendar_data = get_tournament_calendar_data(tournament_id)
    
    return render(request, 'tournament_calendar.html', {
        'tournament_id': tournament_id,
        'calendar_data': calendar_data
    })



# ═══════════════════════════════════════════════════════════
# PWA & MOBILE VIEWS
# ═══════════════════════════════════════════════════════════

def pwa_manifest_view(request):
    """PWA manifest.json"""
    from .mobile_utils import PWA_MANIFEST
    return JsonResponse(PWA_MANIFEST)


def service_worker_view(request):
    """Service worker for offline support"""
    from .mobile_utils import SERVICE_WORKER_JS
    return HttpResponse(SERVICE_WORKER_JS, content_type='application/javascript')


def install_pwa_view(request):
    """PWA installation page"""
    return render(request, 'install_pwa.html')

def player_comparison_pdf_view(request, p1_id, p2_id):
    """Download comparison as styled PDF"""
    from django.shortcuts import get_object_or_404
    from django.db.models import Sum, Count
    from teams.models import PlayerDetails, TournamentRoster
    from scoring.models import BattingScorecard, BowlingScorecard
    from io import BytesIO
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from datetime import datetime

    p1 = get_object_or_404(PlayerDetails, id=p1_id)
    p2 = get_object_or_404(PlayerDetails, id=p2_id)

    def get_stats(player):
        bat  = BattingScorecard.objects.filter(batsman=player).exclude(status='DNB')
        bowl = BowlingScorecard.objects.filter(bowler=player)
        agg  = bat.aggregate(innings=Count('id'), runs=Sum('runs'),
                             balls=Sum('balls_faced'), fours=Sum('fours'), sixes=Sum('sixes'))
        high_qs = bat.order_by('-runs').values_list('runs', flat=True).first()
        outs  = bat.filter(status='OUT').count()
        runs  = agg['runs'] or 0
        balls = agg['balls'] or 0
        avg   = round(runs / outs, 2) if outs else runs
        sr    = round((runs / balls) * 100, 2) if balls else 0
        fifties  = bat.filter(runs__gte=50, runs__lt=100).count()
        hundreds = bat.filter(runs__gte=100).count()
        bagg = bowl.aggregate(wickets=Sum('wickets'), runs_given=Sum('runs_given'), overs=Sum('overs_bowled'))
        wkts = bagg['wickets'] or 0
        rg   = bagg['runs_given'] or 0
        ov   = float(bagg['overs'] or 0)
        econ = round(rg / ov, 2) if ov else 0
        roster = TournamentRoster.objects.filter(player=player).order_by('-id').first()
        role = roster.get_role_display() if roster else 'Batsman'
        return {
            'innings': agg['innings'] or 0, 'runs': runs, 'avg': avg, 'sr': sr,
            'highest': high_qs or 0, 'fours': agg['fours'] or 0,
            'sixes': agg['sixes'] or 0, 'fifties': fifties, 'hundreds': hundreds,
            'wickets': wkts, 'econ': econ, 'role': role,
        }

    s1 = get_stats(p1)
    s2 = get_stats(p2)

    C_AMBER  = colors.HexColor('#f59e0b')
    C_BLUE   = colors.HexColor('#1d4ed8')
    C_DARK   = colors.HexColor('#0f172a')
    C_MUTED  = colors.HexColor('#64748b')
    C_GOLD_BG= colors.HexColor('#fffbeb')
    C_BLUE_BG= colors.HexColor('#eff6ff')
    C_ALT    = colors.HexColor('#f8fafc')
    C_WIN1   = colors.HexColor('#fef3c7')
    C_WIN2   = colors.HexColor('#dbeafe')

    def S(n, **k): return ParagraphStyle(n, **k)

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            leftMargin=0.55*inch, rightMargin=0.55*inch,
                            topMargin=0.5*inch, bottomMargin=0.5*inch)
    els = []
    pw = A4[0] - 1.1*inch

    # ── Header ──
    els.append(HRFlowable(width='100%', thickness=5, color=C_AMBER, spaceAfter=8))
    els.append(Paragraph('STRIKEZONE  •  PLAYER COMPARISON',
        S('hd', fontSize=8, fontName='Helvetica-Bold', textColor=C_MUTED, alignment=TA_CENTER, spaceAfter=4)))
    els.append(Paragraph(f'{p1.player_name}  vs  {p2.player_name}',
        S('tt', fontSize=22, fontName='Helvetica-Bold', textColor=C_DARK, alignment=TA_CENTER, spaceAfter=2)))

    # ── Player header boxes ──
    els.append(Spacer(1, 8))
    hdr = Table([[
        Paragraph(f'<b>{p1.player_name}</b><br/><font size="9" color="#64748b">{s1["role"]}</font>',
                  S('p1h', fontSize=14, fontName='Helvetica-Bold', textColor=C_DARK, alignment=TA_CENTER)),
        Paragraph('<b>VS</b>', S('vs', fontSize=20, fontName='Helvetica-Bold', textColor=C_AMBER, alignment=TA_CENTER)),
        Paragraph(f'<b>{p2.player_name}</b><br/><font size="9" color="#64748b">{s2["role"]}</font>',
                  S('p2h', fontSize=14, fontName='Helvetica-Bold', textColor=C_DARK, alignment=TA_CENTER)),
    ]], colWidths=[pw*0.43, pw*0.14, pw*0.43])
    hdr.setStyle(TableStyle([
        ('BACKGROUND',    (0,0),(0,0), C_GOLD_BG),
        ('BACKGROUND',    (2,0),(2,0), C_BLUE_BG),
        ('BOX',           (0,0),(0,0), 2, C_AMBER),
        ('BOX',           (2,0),(2,0), 2, C_BLUE),
        ('TOPPADDING',    (0,0),(-1,-1), 12),
        ('BOTTOMPADDING', (0,0),(-1,-1), 12),
        ('VALIGN',        (0,0),(-1,-1), 'MIDDLE'),
    ]))
    els.append(hdr)
    els.append(Spacer(1, 14))

    # ── Stats table ──
    stat_defs = [
        ('Innings Played', s1['innings'],  s2['innings'],  True),
        ('Total Runs',     s1['runs'],     s2['runs'],     True),
        ('Batting Average',s1['avg'],      s2['avg'],      True),
        ('Strike Rate',    s1['sr'],       s2['sr'],       True),
        ('Highest Score',  s1['highest'],  s2['highest'],  True),
        ('Fifties (50s)',  s1['fifties'],  s2['fifties'],  True),
        ('Hundreds (100s)',s1['hundreds'], s2['hundreds'], True),
        ('Fours',          s1['fours'],    s2['fours'],    True),
        ('Sixes',          s1['sixes'],    s2['sixes'],    True),
        ('Wickets',        s1['wickets'],  s2['wickets'],  True),
        ('Economy Rate',   s1['econ'],     s2['econ'],     False),
    ]

    # Header row
    rows = [[
        Paragraph(f'<b>{p1.player_name[:14]}</b>', S('h1', fontSize=9, fontName='Helvetica-Bold', textColor=colors.white, alignment=TA_CENTER)),
        Paragraph('<b>STAT</b>',                   S('hs', fontSize=9, fontName='Helvetica-Bold', textColor=colors.white, alignment=TA_CENTER)),
        Paragraph(f'<b>{p2.player_name[:14]}</b>', S('h2', fontSize=9, fontName='Helvetica-Bold', textColor=colors.white, alignment=TA_CENTER)),
        Paragraph('<b>EDGE</b>',                   S('he', fontSize=9, fontName='Helvetica-Bold', textColor=colors.white, alignment=TA_CENTER)),
    ]]

    style_cmds = [
        ('BACKGROUND',    (0,0),(-1,0), C_DARK),
        ('LINEBELOW',     (0,0),(-1,0), 2, C_AMBER),
        ('ALIGN',         (0,0),(-1,-1), 'CENTER'),
        ('FONTNAME',      (0,1),(-1,-1), 'Helvetica'),
        ('FONTSIZE',      (0,1),(-1,-1), 9),
        ('TOPPADDING',    (0,0),(-1,-1), 7),
        ('BOTTOMPADDING', (0,0),(-1,-1), 7),
        ('GRID',          (0,0),(-1,-1), 0.3, colors.HexColor('#e2e8f0')),
    ]

    for i, (label, v1, v2, hib) in enumerate(stat_defs):
        row_i = i + 1
        bg = C_ALT if row_i % 2 == 0 else colors.white
        try:
            f1, f2 = float(v1), float(v2)
            if f1 != f2:
                win1 = (f1 > f2) == hib
                win_bg = C_WIN1 if win1 else bg
                los_bg = bg if win1 else C_WIN2
                edge_name = p1.player_name[:10] if win1 else p2.player_name[:10]
                edge_color = C_AMBER if win1 else C_BLUE
                style_cmds.append(('BACKGROUND', (0, row_i),(0, row_i), C_WIN1 if win1 else bg))
                style_cmds.append(('BACKGROUND', (2, row_i),(2, row_i), bg if win1 else C_WIN2))
                style_cmds.append(('FONTNAME',   (0 if win1 else 2, row_i),(0 if win1 else 2, row_i), 'Helvetica-Bold'))
                edge_para = Paragraph(f'<b>{edge_name}</b>',
                    S(f'e{i}', fontSize=8, fontName='Helvetica-Bold',
                      textColor=C_AMBER if win1 else C_BLUE, alignment=TA_CENTER))
            else:
                style_cmds.append(('BACKGROUND', (0,row_i),(-1,row_i), bg))
                edge_para = Paragraph('=', S(f'e{i}', fontSize=9, fontName='Helvetica', textColor=C_MUTED, alignment=TA_CENTER))
        except Exception:
            style_cmds.append(('BACKGROUND', (0,row_i),(-1,row_i), bg))
            edge_para = Paragraph('—', S(f'e{i}', fontSize=9, fontName='Helvetica', textColor=C_MUTED, alignment=TA_CENTER))

        rows.append([
            Paragraph(str(v1), S(f'v1{i}', fontSize=11, fontName='Helvetica', textColor=C_DARK, alignment=TA_CENTER)),
            Paragraph(label,   S(f'lb{i}', fontSize=8,  fontName='Helvetica-Bold', textColor=C_MUTED, alignment=TA_CENTER)),
            Paragraph(str(v2), S(f'v2{i}', fontSize=11, fontName='Helvetica', textColor=C_DARK, alignment=TA_CENTER)),
            edge_para,
        ])

    tbl = Table(rows, colWidths=[pw*0.28, pw*0.28, pw*0.28, pw*0.16])
    tbl.setStyle(TableStyle(style_cmds))
    els.append(tbl)

    # Footer
    els.append(Spacer(1, 14))
    els.append(HRFlowable(width='100%', thickness=1, color=C_AMBER, spaceAfter=6))
    els.append(Paragraph(f'Generated by StrikeZone  •  {datetime.now().strftime("%d %b %Y, %I:%M %p")}',
        S('ft', fontSize=7, fontName='Helvetica', textColor=C_MUTED, alignment=TA_CENTER)))

    doc.build(els)
    buffer.seek(0)
    fname = f"comparison_{p1.player_name}_vs_{p2.player_name}.pdf".replace(' ','_')
    from django.http import HttpResponse
    resp = HttpResponse(buffer, content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="{fname}"'
    return resp