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
from .security_utils import rate_limit, validate_field, sanitize_input
from .mobile_utils import is_mobile_request, get_device_type


# ═══════════════════════════════════════════════════════════
# SEARCH VIEWS
# ═══════════════════════════════════════════════════════════

@rate_limit('search', limit=30, period=60)
def enhanced_search_view(request):
    """Enhanced global search"""
    query = request.GET.get('q', '').strip()
    
    if len(query) < 2:
        return JsonResponse({'error': 'Query too short'}, status=400)
    
    # Sanitize input
    query = sanitize_input(query, max_length=100)
    
    results = global_search(query, limit=10)
    
    # Format for JSON response
    data = {
        'query': query,
        'total': results['total'],
        'players': [
            {'id': p.id, 'name': p.player_name, 'mobile': p.mobile_number}
            for p in results['players']
        ],
        'teams': [
            {'id': t.id, 'name': t.team_name, 'code': t.team_code}
            for t in results['teams']
        ],
        'tournaments': [
            {'id': t.id, 'name': t.tournament_name}
            for t in results['tournaments']
        ],
        'matches': [
            {
                'id': m.id,
                'teams': f"{m.team1.team_name} vs {m.team2.team_name}",
                'date': str(m.match_date),
                'venue': m.venue
            }
            for m in results['matches']
        ]
    }
    
    return JsonResponse(data)



# ═══════════════════════════════════════════════════════════
# ANALYTICS VIEWS
# ═══════════════════════════════════════════════════════════

def player_form_view(request, player_id):
    """Player recent form"""
    form_data = get_player_form(player_id, last_n_matches=5)
    
    return JsonResponse({
        'player_id': player_id,
        'form': form_data
    })


def player_comparison_view(request):
    """Compare two players"""
    player1_id = request.GET.get('p1')
    player2_id = request.GET.get('p2')
    tournament_id = request.GET.get('tournament')
    
    if not player1_id or not player2_id:
        return JsonResponse({'error': 'Both players required'}, status=400)
    
    comparison = get_player_comparison(
        int(player1_id),
        int(player2_id),
        int(tournament_id) if tournament_id else None
    )
    
    return JsonResponse(comparison)


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
    """Export scorecard as PDF"""
    pdf_buffer = export_scorecard_pdf(match_id)
    
    response = HttpResponse(pdf_buffer, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="scorecard_match_{match_id}.pdf"'
    
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
