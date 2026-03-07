"""
StrikeZone Player ML Analysis Engine
=======================================
Generates server-side charts for a single player using:
  - matplotlib, numpy, pandas, scikit-learn

Charts:
  1. run_history      — runs per innings bar chart with trend line
  2. sr_history       — strike rate per innings line chart
  3. phase_bat        — avg runs in Powerplay / Middle / Death
  4. shot_zones       — wagon wheel (where player scores)
  5. dismissal_pie    — how player gets out
  6. bowling_economy  — economy per match line chart
  7. bowling_wickets  — wickets per match bar chart
  8. bowl_phase       — wickets in each phase
  9. strengths_radar  — spider chart across 6 metrics
"""

import io, base64, math
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.colors import LinearSegmentedColormap
import warnings
warnings.filterwarnings('ignore')

# ── PALETTE ───────────────────────────────────────────────────
BG      = '#07090f'
SURFACE = '#0e1117'
SURFACE2= '#161b26'
BORDER  = '#1e2d40'
ACCENT  = '#6366f1'
GOLD    = '#f59e0b'
GREEN   = '#22c55e'
RED     = '#ef4444'
BLUE    = '#38bdf8'
MUTED   = '#64748b'
TEXT    = '#e2e8f0'
PURPLE  = '#a78bfa'

def _fig(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=120, bbox_inches='tight',
                facecolor=BG, edgecolor='none')
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode()
    plt.close(fig)
    return b64

def _ax(ax, title='', xlabel='', ylabel=''):
    ax.set_facecolor(SURFACE)
    ax.tick_params(colors=MUTED, labelsize=8)
    for sp in ax.spines.values(): sp.set_color(BORDER)
    ax.xaxis.label.set_color(MUTED)
    ax.yaxis.label.set_color(MUTED)
    ax.grid(True, color=BORDER, linewidth=0.4, alpha=0.6, zorder=0)
    if title:  ax.set_title(title, color=TEXT, fontsize=10, fontweight='bold', pad=8)
    if xlabel: ax.set_xlabel(xlabel, fontsize=8)
    if ylabel: ax.set_ylabel(ylabel, fontsize=8)


# ── 1. RUNS PER INNINGS ───────────────────────────────────────
def run_history_chart(bat_records):
    """Bar chart of runs per innings with trend line."""
    if not bat_records:
        return None

    data = [(r['runs'], r['match_label']) for r in bat_records[-15:]]
    runs   = [d[0] for d in data]
    labels = [d[1] for d in data]
    x = np.arange(len(runs))

    fig, ax = plt.subplots(figsize=(9, 3.8), facecolor=BG)
    colors = [GREEN if r >= 30 else (GOLD if r >= 15 else ACCENT) for r in runs]
    ax.bar(x, runs, color=colors, alpha=0.85, zorder=3, width=0.6)

    # Trend line
    if len(runs) >= 3:
        z = np.polyfit(x, runs, 1)
        p = np.poly1d(z)
        ax.plot(x, p(x), color=RED if z[0] < 0 else GREEN,
                linewidth=1.8, linestyle='--', alpha=0.7,
                label=f'Trend ({"↑" if z[0]>=0 else "↓"})')

    # Avg line
    avg = np.mean(runs)
    ax.axhline(avg, color=PURPLE, linewidth=1.2, linestyle=':', alpha=0.7,
               label=f'Avg: {avg:.1f}')

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=7, rotation=35, ha='right', color=MUTED)
    ax.legend(fontsize=7, facecolor=SURFACE2, edgecolor=BORDER, labelcolor=TEXT)
    _ax(ax, 'Runs Per Innings', '', 'Runs')
    plt.tight_layout()
    return _fig(fig)


# ── 2. STRIKE RATE HISTORY ────────────────────────────────────
def sr_history_chart(bat_records):
    """Line chart of strike rate per innings."""
    data = [(r['sr'], r['match_label']) for r in bat_records[-15:] if r['balls'] > 3]
    if not data:
        return None

    srs    = [d[0] for d in data]
    labels = [d[1] for d in data]
    x = np.arange(len(srs))

    fig, ax = plt.subplots(figsize=(9, 3.5), facecolor=BG)

    # Fill above/below 100
    ax.fill_between(x, srs, 100, where=[s >= 100 for s in srs],
                    color=GREEN, alpha=0.12, interpolate=True)
    ax.fill_between(x, srs, 100, where=[s < 100 for s in srs],
                    color=RED, alpha=0.12, interpolate=True)

    ax.plot(x, srs, color=ACCENT, linewidth=2, marker='o',
            markersize=5, zorder=4)
    ax.axhline(100, color=MUTED, linewidth=1, linestyle='--', alpha=0.5, label='SR=100')

    # Highlight best and worst
    if srs:
        best_i = int(np.argmax(srs))
        worst_i = int(np.argmin(srs))
        ax.annotate(f'Best\n{srs[best_i]:.0f}', (x[best_i], srs[best_i]),
                    textcoords='offset points', xytext=(0, 8),
                    fontsize=7, color=GREEN, ha='center', fontweight='bold')
        ax.annotate(f'Low\n{srs[worst_i]:.0f}', (x[worst_i], srs[worst_i]),
                    textcoords='offset points', xytext=(0, -18),
                    fontsize=7, color=RED, ha='center', fontweight='bold')

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=7, rotation=35, ha='right', color=MUTED)
    ax.legend(fontsize=7, facecolor=SURFACE2, edgecolor=BORDER, labelcolor=TEXT)
    _ax(ax, 'Strike Rate Per Innings', '', 'Strike Rate')
    plt.tight_layout()
    return _fig(fig)


# ── 3. PHASE BATTING ─────────────────────────────────────────
def phase_batting_chart(phase_data):
    """
    Avg runs and SR in each phase.
    phase_data = {'powerplay':{'runs':x,'balls':y}, 'middle':..., 'death':...}
    """
    phases = ['Powerplay', 'Middle', 'Death']
    keys   = ['powerplay', 'middle', 'death']
    colors = [ACCENT, GOLD, RED]

    avg_runs = []
    avg_sr   = []
    for k in keys:
        d = phase_data.get(k, {'runs': 0, 'balls': 0, 'innings': 1})
        innings = max(d.get('innings', 1), 1)
        balls   = d.get('balls', 0)
        runs    = d.get('runs', 0)
        avg_runs.append(round(runs / innings, 1))
        avg_sr.append(round(runs / balls * 100, 1) if balls > 0 else 0)

    fig, axes = plt.subplots(1, 2, figsize=(9, 3.5), facecolor=BG)

    for ax, vals, ylabel, title in zip(axes,
        [avg_runs, avg_sr],
        ['Avg Runs', 'Strike Rate'],
        ['Avg Runs by Phase', 'Strike Rate by Phase']):
        bars = ax.bar(phases, vals, color=colors, alpha=0.85, zorder=3, width=0.5)
        for bar, val in zip(bars, vals):
            if val > 0:
                ax.text(bar.get_x() + bar.get_width()/2,
                        bar.get_height() + 0.5,
                        str(val), ha='center', va='bottom',
                        fontsize=9, color=TEXT, fontweight='bold')
        _ax(ax, title, '', ylabel)

    plt.tight_layout(pad=2)
    return _fig(fig)


# ── 4. SHOT ZONES (WAGON WHEEL) ──────────────────────────────
def shot_zones_chart(zone_data, player_name):
    """
    Polar area chart of where the player scores.
    zone_data = list of {'zone': 0-11, 'runs': int}
    """
    ZONE_LABELS = ['Fine Leg', 'Sq Leg', 'Midwicket', 'Mid On',
                   'Straight', 'Long On', 'Long Off', 'Mid Off',
                   'Cover', 'Point', '3rd Man', 'Deep Fine']
    N = 12
    angles = np.linspace(0, 2*np.pi, N, endpoint=False)
    zone_runs = [0] * N
    for z in zone_data:
        idx = int(z.get('zone', 0)) % N
        zone_runs[idx] += z.get('runs', 0)

    fig = plt.figure(figsize=(5.5, 5), facecolor=BG)
    ax = fig.add_subplot(111, projection='polar')
    ax.set_facecolor(SURFACE)
    ax.spines['polar'].set_color(BORDER)

    max_r = max(zone_runs) if max(zone_runs) > 0 else 1
    width = 2 * np.pi / N
    cmap  = plt.cm.RdYlGn(np.array(zone_runs) / max_r)

    for i, (angle, runs) in enumerate(zip(angles, zone_runs)):
        h = runs / max_r * 8 if max_r > 0 else 0.1
        ax.bar(angle, h, width=width*0.85, bottom=0,
               color=cmap[i], alpha=0.78, linewidth=0.5, edgecolor=BORDER)
        if runs > 0:
            ax.text(angle, h + 0.6, str(runs),
                    ha='center', va='bottom', fontsize=7,
                    color=TEXT, fontweight='bold')

    ax.set_xticks(angles)
    ax.set_xticklabels(ZONE_LABELS, fontsize=7, color=MUTED)
    ax.set_yticks([])
    ax.set_title(f'Shot Zones — {player_name}', color=TEXT,
                 fontsize=10, fontweight='bold', pad=14)

    total = sum(zone_runs)
    tagged = sum(1 for r in zone_runs if r > 0)
    ax.text(0.5, -0.07,
            f'Total: {total} runs across {tagged} zones' if total else 'No direction data yet',
            ha='center', transform=ax.transAxes,
            fontsize=7.5, color=MUTED, style='italic')

    plt.tight_layout()
    return _fig(fig)


# ── 5. DISMISSAL PIE ─────────────────────────────────────────
def dismissal_chart(dismissals):
    """
    Donut chart of how player gets out.
    dismissals = {'Caught': 5, 'Bowled': 2, ...}
    """
    if not dismissals or sum(dismissals.values()) == 0:
        return None

    labels = list(dismissals.keys())
    sizes  = list(dismissals.values())
    colors = [RED, ACCENT, GOLD, PURPLE, BLUE, GREEN,
              '#ff6b6b', '#ffd93d', '#6bcb77'][:len(labels)]

    fig, ax = plt.subplots(figsize=(5.5, 4.5), facecolor=BG)
    wedges, texts, autotexts = ax.pie(
        sizes, labels=labels, colors=colors,
        autopct='%1.0f%%', pctdistance=0.8,
        startangle=90,
        wedgeprops={'width': 0.55, 'edgecolor': BG, 'linewidth': 2},
        textprops={'color': MUTED, 'fontsize': 8}
    )
    for at in autotexts:
        at.set_color(TEXT)
        at.set_fontsize(7)
        at.set_fontweight('bold')

    total = sum(sizes)
    ax.text(0, 0.1, str(total), ha='center', va='center',
            fontsize=22, fontweight='bold', color=TEXT)
    ax.text(0, -0.22, 'Dismissals', ha='center', va='center',
            fontsize=8, color=MUTED)
    ax.set_facecolor(BG)
    ax.set_title('How Does This Player Get Out?', color=TEXT,
                 fontsize=10, fontweight='bold', pad=10)
    plt.tight_layout()
    return _fig(fig)


# ── 6. BOWLING ECONOMY HISTORY ────────────────────────────────
def economy_history_chart(bowl_records):
    """Line chart of economy rate per spell."""
    data = [(r['economy'], r['match_label']) for r in bowl_records[-12:] if r['overs'] > 0]
    if not data:
        return None

    ecos   = [d[0] for d in data]
    labels = [d[1] for d in data]
    x = np.arange(len(ecos))

    fig, ax = plt.subplots(figsize=(9, 3.5), facecolor=BG)

    # Color-fill under reference line (7 = par economy in T20)
    par = 7.0
    ax.fill_between(x, ecos, par, where=[e <= par for e in ecos],
                    color=GREEN, alpha=0.15, interpolate=True, label='Under par')
    ax.fill_between(x, ecos, par, where=[e > par for e in ecos],
                    color=RED, alpha=0.15, interpolate=True, label='Over par')

    ax.plot(x, ecos, color=BLUE, linewidth=2, marker='o', markersize=5, zorder=4)
    ax.axhline(par, color=MUTED, linewidth=1, linestyle='--', alpha=0.5, label=f'Par ({par})')
    avg = np.mean(ecos)
    ax.axhline(avg, color=GOLD, linewidth=1, linestyle=':', alpha=0.7,
               label=f'Career avg: {avg:.2f}')

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=7, rotation=35, ha='right', color=MUTED)
    ax.legend(fontsize=7, facecolor=SURFACE2, edgecolor=BORDER, labelcolor=TEXT)
    _ax(ax, 'Economy Rate Per Spell', '', 'Economy')
    plt.tight_layout()
    return _fig(fig)


# ── 7. WICKETS PER MATCH ─────────────────────────────────────
def wickets_history_chart(bowl_records):
    """Bar chart of wickets per match."""
    data = [(r['wickets'], r['match_label']) for r in bowl_records[-12:]]
    if not data:
        return None

    wkts   = [d[0] for d in data]
    labels = [d[1] for d in data]
    x = np.arange(len(wkts))

    fig, ax = plt.subplots(figsize=(9, 3.5), facecolor=BG)
    colors = [GREEN if w >= 3 else (GOLD if w >= 1 else MUTED) for w in wkts]
    bars = ax.bar(x, wkts, color=colors, alpha=0.85, zorder=3, width=0.6)

    for bar, val in zip(bars, wkts):
        ax.text(bar.get_x() + bar.get_width()/2,
                bar.get_height() + 0.05,
                str(val), ha='center', va='bottom',
                fontsize=9, color=TEXT, fontweight='bold')

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=7, rotation=35, ha='right', color=MUTED)
    ax.yaxis.set_major_locator(plt.MaxNLocator(integer=True))
    _ax(ax, 'Wickets Per Match', '', 'Wickets')

    patches = [
        mpatches.Patch(color=GREEN, label='3+ wickets'),
        mpatches.Patch(color=GOLD,  label='1-2 wickets'),
        mpatches.Patch(color=MUTED, label='0 wickets'),
    ]
    ax.legend(handles=patches, fontsize=7, facecolor=SURFACE2,
              edgecolor=BORDER, labelcolor=TEXT)
    plt.tight_layout()
    return _fig(fig)


# ── 8. STRENGTHS RADAR ───────────────────────────────────────
def strengths_radar_chart(metrics, player_name):
    """
    Spider chart across batting/bowling metrics (0-100 normalised).
    Falls back to a minimal 3-point chart if fewer metrics available.
    """
    # Ensure we always have at least 3 points to draw a polygon
    if not metrics:
        metrics = {'Average': 0, 'Strike Rate': 0, 'Consistency': 0}
    labels = list(metrics.keys())
    values = list(metrics.values())
    N = len(labels)
    # Pad to at least 3
    while N < 3:
        labels.append(f'—')
        values.append(0)
        N += 1

    angles = np.linspace(0, 2*np.pi, N, endpoint=False).tolist()
    values_plot = values + values[:1]
    angles += angles[:1]

    fig = plt.figure(figsize=(5.5, 5), facecolor=BG)
    ax = fig.add_subplot(111, projection='polar')
    ax.set_facecolor(SURFACE)
    ax.spines['polar'].set_color(BORDER)

    # Draw fill
    ax.fill(angles, values_plot, color=ACCENT, alpha=0.18)
    ax.plot(angles, values_plot, color=ACCENT, linewidth=2.2, zorder=4)

    # Dots on each vertex
    ax.scatter(angles[:-1], values[:],   s=40, color=ACCENT, zorder=5)

    # Reference circles
    for ref in [25, 50, 75, 100]:
        ref_vals = [ref] * (N + 1)
        ax.plot(angles, ref_vals, color=BORDER, linewidth=0.5, zorder=1)

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, fontsize=8.5, color=TEXT, fontweight='600')
    ax.set_yticks([25, 50, 75, 100])
    ax.set_yticklabels(['25', '50', '75', '100'], fontsize=6, color=MUTED)
    ax.set_ylim(0, 110)
    ax.yaxis.grid(True, color=BORDER, linewidth=0.4)
    ax.xaxis.grid(True, color=BORDER, linewidth=0.4)

    ax.set_title(f'{player_name} — Skill Profile', color=TEXT,
                 fontsize=11, fontweight='bold', pad=20)
    plt.tight_layout()
    return _fig(fig)


# ── MASTER FUNCTION ──────────────────────────────────────────
def generate_player_charts(player_data):
    """
    Entry point. Takes structured player data, returns dict of chart_name -> base64.
    """
    charts = {}
    name = player_data.get('name', 'Player')

    # Batting charts
    bat = player_data.get('bat_records', [])
    if bat:
        try: charts['run_history']  = run_history_chart(bat)
        except Exception as e: charts['run_history_err'] = str(e)
        try: charts['sr_history']   = sr_history_chart(bat)
        except Exception as e: charts['sr_history_err'] = str(e)

    phase = player_data.get('phase_batting', {})
    if phase:
        try: charts['phase_batting'] = phase_batting_chart(phase)
        except Exception as e: charts['phase_batting_err'] = str(e)

    zones = player_data.get('shot_zones', [])
    try: charts['shot_zones'] = shot_zones_chart(zones, name)
    except Exception as e: charts['shot_zones_err'] = str(e)

    dismissals = player_data.get('dismissals', {})
    if dismissals:
        try: charts['dismissals'] = dismissal_chart(dismissals)
        except Exception as e: charts['dismissals_err'] = str(e)

    # Bowling charts
    bowl = player_data.get('bowl_records', [])
    if bowl:
        try: charts['economy_history'] = economy_history_chart(bowl)
        except Exception as e: charts['economy_err'] = str(e)
        try: charts['wickets_history'] = wickets_history_chart(bowl)
        except Exception as e: charts['wickets_err'] = str(e)

    # Radar
    metrics = player_data.get('radar_metrics', {})
    if metrics:
        try: charts['radar'] = strengths_radar_chart(metrics, name)
        except Exception as e: charts['radar_err'] = str(e)

    return charts