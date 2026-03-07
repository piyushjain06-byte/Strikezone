"""
StrikeZone Team ML Analysis Engine
====================================
Generates server-side charts for team analysis using:
  matplotlib, numpy, pandas

Charts:
  1. win_loss_bar       — W/L/T record per tournament or overall
  2. run_rate_chart     — team run rate per match (trend)
  3. phase_bar          — team batting by phase (powerplay/middle/death)
  4. top_bat_chart      — top batsmen bar chart
  5. top_bowl_chart     — top bowlers bar chart
  6. performance_radar  — team performance radar
"""

import io, base64
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
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
    for sp in ax.spines.values():
        sp.set_color(BORDER)
    ax.xaxis.label.set_color(MUTED)
    ax.yaxis.label.set_color(MUTED)
    ax.grid(True, color=BORDER, linewidth=0.4, alpha=0.6, zorder=0)
    if title:  ax.set_title(title, color=TEXT, fontsize=10, fontweight='bold', pad=8)
    if xlabel: ax.set_xlabel(xlabel, fontsize=8)
    if ylabel: ax.set_ylabel(ylabel, fontsize=8)


# ── 1. WIN/LOSS RECORD ───────────────────────────────────────
def win_loss_chart(summary, team_name):
    """Donut chart showing W/L/T breakdown."""
    wins = summary.get('wins', 0)
    losses = summary.get('losses', 0)
    ties = summary.get('ties', 0)

    if wins + losses + ties == 0:
        return None

    labels, sizes, colors = [], [], []
    if wins > 0:    labels.append(f'Won ({wins})');    sizes.append(wins);    colors.append(GREEN)
    if losses > 0:  labels.append(f'Lost ({losses})'); sizes.append(losses); colors.append(RED)
    if ties > 0:    labels.append(f'Tied ({ties})');   sizes.append(ties);   colors.append(GOLD)

    fig, ax = plt.subplots(figsize=(5, 4.5), facecolor=BG)
    wedges, texts, autotexts = ax.pie(
        sizes, labels=labels, colors=colors,
        autopct='%1.0f%%', pctdistance=0.78,
        startangle=90,
        wedgeprops={'width': 0.55, 'edgecolor': BG, 'linewidth': 2},
        textprops={'color': MUTED, 'fontsize': 9}
    )
    for at in autotexts:
        at.set_color(TEXT)
        at.set_fontsize(9)
        at.set_fontweight('bold')

    total = wins + losses + ties
    win_pct = round(wins / total * 100) if total else 0
    ax.text(0, 0.12, f'{win_pct}%', ha='center', va='center',
            fontsize=22, fontweight='bold', color=GREEN if win_pct >= 50 else RED)
    ax.text(0, -0.18, 'Win Rate', ha='center', va='center',
            fontsize=8, color=MUTED)
    ax.set_facecolor(BG)
    ax.set_title(f'{team_name} — Overall Record', color=TEXT,
                 fontsize=10, fontweight='bold', pad=10)
    plt.tight_layout()
    return _fig(fig)


# ── 2. RUN RATE TREND ────────────────────────────────────────
def run_rate_chart(match_records, team_name):
    """Line chart showing team run rate per match."""
    if not match_records:
        return None

    data = []
    for m in match_records[-12:]:
        balls = m.get('team_balls', 0)
        runs = m.get('team_runs', 0)
        if balls > 0:
            rr = round(runs / balls * 6, 2)
            data.append({'label': f"vs {m.get('opponent', '')[:6]}", 'rr': rr, 'won': m.get('won', False)})

    if not data:
        return None

    labels = [d['label'] for d in data]
    rrs = [d['rr'] for d in data]
    colors = [GREEN if d['won'] else RED for d in data]
    x = np.arange(len(rrs))

    fig, ax = plt.subplots(figsize=(9, 3.8), facecolor=BG)

    ax.bar(x, rrs, color=colors, alpha=0.7, zorder=3, width=0.6)
    ax.plot(x, rrs, color=ACCENT, linewidth=2, marker='o', markersize=5, zorder=4)

    avg_rr = np.mean(rrs)
    ax.axhline(avg_rr, color=GOLD, linewidth=1.2, linestyle='--', alpha=0.7,
               label=f'Avg RR: {avg_rr:.2f}')

    if len(rrs) >= 3:
        z = np.polyfit(x, rrs, 1)
        p = np.poly1d(z)
        ax.plot(x, p(x), color=PURPLE, linewidth=1.5, linestyle=':', alpha=0.6,
                label=f'Trend ({"↑" if z[0] >= 0 else "↓"})')

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=7, rotation=35, ha='right', color=MUTED)
    ax.legend(fontsize=7, facecolor=SURFACE2, edgecolor=BORDER, labelcolor=TEXT)

    patches = [mpatches.Patch(color=GREEN, label='Won'), mpatches.Patch(color=RED, label='Lost')]
    ax.legend(handles=patches + [
        mpatches.Patch(color=GOLD, label=f'Avg RR: {avg_rr:.2f}')
    ], fontsize=7, facecolor=SURFACE2, edgecolor=BORDER, labelcolor=TEXT)
    _ax(ax, f'{team_name} — Run Rate Per Match', '', 'Run Rate')
    plt.tight_layout()
    return _fig(fig)


# ── 3. PHASE BATTING ─────────────────────────────────────────
def phase_batting_chart(phase_data, team_name):
    """Grouped bar chart: runs + wickets per phase."""
    phases = ['Powerplay', 'Middle', 'Death']
    keys = ['powerplay', 'middle', 'death']
    colors = [ACCENT, GOLD, RED]

    avg_rrs, avg_wkts = [], []
    for k in keys:
        d = phase_data.get(k, {'runs': 0, 'wickets': 0, 'overs': 1})
        overs = max(d.get('overs', 1), 1)
        runs = d.get('runs', 0)
        avg_rrs.append(round(runs / overs, 1))
        avg_wkts.append(round(d.get('wickets', 0) / max(overs / 6, 1), 2))

    fig, axes = plt.subplots(1, 2, figsize=(9, 3.5), facecolor=BG)

    for ax, vals, ylabel, title in zip(axes,
            [avg_rrs, avg_wkts],
            ['Avg Runs/Over', 'Wickets Lost/Match'],
            ['Scoring Rate by Phase', 'Wickets Lost by Phase']):
        bars = ax.bar(phases, vals, color=colors, alpha=0.85, zorder=3, width=0.5)
        for bar, val in zip(bars, vals):
            if val > 0:
                ax.text(bar.get_x() + bar.get_width() / 2,
                        bar.get_height() + 0.05,
                        str(val), ha='center', va='bottom',
                        fontsize=9, color=TEXT, fontweight='bold')
        _ax(ax, title, '', ylabel)

    plt.tight_layout(pad=2)
    return _fig(fig)


# ── 4. TOP BATSMEN ───────────────────────────────────────────
def top_batsmen_chart(batsmen, team_name):
    """Horizontal bar chart of top batsmen by runs."""
    if not batsmen:
        return None

    data = batsmen[:7]
    names = [b['name'][:10] for b in data]
    runs = [b['runs'] for b in data]
    avgs = [b.get('avg', 0) for b in data]
    y = np.arange(len(names))

    fig, axes = plt.subplots(1, 2, figsize=(10, max(3.5, len(names) * 0.55 + 1)), facecolor=BG)

    # Runs bar
    ax1 = axes[0]
    colors_r = [GREEN if r == max(runs) else ACCENT for r in runs]
    bars = ax1.barh(y, runs, color=colors_r, alpha=0.85, zorder=3, height=0.6)
    for bar, val in zip(bars, runs):
        ax1.text(bar.get_width() + max(runs) * 0.01, bar.get_y() + bar.get_height() / 2,
                 str(val), va='center', fontsize=8, color=TEXT, fontweight='bold')
    ax1.set_yticks(y)
    ax1.set_yticklabels(names, fontsize=8, color=TEXT)
    ax1.invert_yaxis()
    _ax(ax1, 'Top Run Scorers', 'Runs', '')

    # Average bar
    ax2 = axes[1]
    colors_a = [GOLD if a == max(avgs) else ACCENT for a in avgs]
    bars2 = ax2.barh(y, avgs, color=colors_a, alpha=0.85, zorder=3, height=0.6)
    for bar, val in zip(bars2, avgs):
        ax2.text(bar.get_width() + max(avgs + [1]) * 0.01,
                 bar.get_y() + bar.get_height() / 2,
                 str(val), va='center', fontsize=8, color=TEXT, fontweight='bold')
    ax2.set_yticks(y)
    ax2.set_yticklabels(names, fontsize=8, color=TEXT)
    ax2.invert_yaxis()
    _ax(ax2, 'Batting Average', 'Average', '')

    plt.suptitle(f'{team_name} — Batting Leaders', color=TEXT,
                 fontsize=11, fontweight='bold', y=1.02)
    plt.tight_layout(pad=2)
    return _fig(fig)


# ── 5. TOP BOWLERS ───────────────────────────────────────────
def top_bowlers_chart(bowlers, team_name):
    """Horizontal bar chart of top bowlers."""
    if not bowlers:
        return None

    data = [b for b in bowlers[:7] if b.get('wickets', 0) > 0]
    if not data:
        return None

    names = [b['name'][:10] for b in data]
    wkts = [b['wickets'] for b in data]
    ecos = [b.get('economy', 0) for b in data]
    y = np.arange(len(names))

    fig, axes = plt.subplots(1, 2, figsize=(10, max(3.5, len(names) * 0.55 + 1)), facecolor=BG)

    # Wickets
    ax1 = axes[0]
    colors_w = [GREEN if w == max(wkts) else BLUE for w in wkts]
    bars = ax1.barh(y, wkts, color=colors_w, alpha=0.85, zorder=3, height=0.6)
    for bar, val in zip(bars, wkts):
        ax1.text(bar.get_width() + 0.1, bar.get_y() + bar.get_height() / 2,
                 str(val), va='center', fontsize=8, color=TEXT, fontweight='bold')
    ax1.set_yticks(y)
    ax1.set_yticklabels(names, fontsize=8, color=TEXT)
    ax1.invert_yaxis()
    ax1.xaxis.set_major_locator(plt.MaxNLocator(integer=True))
    _ax(ax1, 'Wickets Taken', 'Wickets', '')

    # Economy
    ax2 = axes[1]
    colors_e = [GREEN if e == min(ecos) else (RED if e > 9 else GOLD) for e in ecos]
    bars2 = ax2.barh(y, ecos, color=colors_e, alpha=0.85, zorder=3, height=0.6)
    for bar, val in zip(bars2, ecos):
        ax2.text(bar.get_width() + 0.1, bar.get_y() + bar.get_height() / 2,
                 str(val), va='center', fontsize=8, color=TEXT, fontweight='bold')
    ax2.set_yticks(y)
    ax2.set_yticklabels(names, fontsize=8, color=TEXT)
    ax2.invert_yaxis()
    _ax(ax2, 'Economy Rate', 'Economy', '')

    plt.suptitle(f'{team_name} — Bowling Leaders', color=TEXT,
                 fontsize=11, fontweight='bold', y=1.02)
    plt.tight_layout(pad=2)
    return _fig(fig)


# ── 6. PERFORMANCE RADAR ─────────────────────────────────────
def team_radar_chart(data, team_name):
    """Spider chart for team performance dimensions."""
    summary = data.get('summary', {})
    total = summary.get('total', 1) or 1
    wins = summary.get('wins', 0)
    win_pct = wins / total * 100

    # Compute metrics (0–100)
    def norm(val, lo, hi):
        if hi <= lo:
            return 50
        return round(max(0, min(100, (val - lo) / (hi - lo) * 100)))

    match_records = data.get('match_records', [])
    top_bat = data.get('top_batsmen', [])
    top_bowl = data.get('top_bowlers', [])

    avg_runs = np.mean([m['team_runs'] for m in match_records]) if match_records else 0
    avg_conceded = np.mean([m['opp_runs'] for m in match_records]) if match_records else 0
    avg_bat = np.mean([b.get('avg', 0) for b in top_bat[:3]]) if top_bat else 0
    avg_eco = np.mean([b.get('economy', 10) for b in top_bowl[:3]]) if top_bowl else 10

    phase = data.get('phase_data', {})
    pp = phase.get('powerplay', {})
    death = phase.get('death', {})
    pp_rpo = pp.get('runs', 0) / max(pp.get('overs', 1), 1)
    death_rpo = death.get('runs', 0) / max(death.get('overs', 1), 1)

    metrics = {
        'Win Rate': norm(win_pct, 0, 100),
        'Batting Avg': norm(avg_bat, 0, 50),
        'Run Scoring': norm(avg_runs, 50, 180),
        'Bowling': norm(10 - avg_eco, 0, 10),
        'Powerplay': norm(pp_rpo, 3, 12),
        'Death Overs': norm(death_rpo, 4, 14),
    }

    labels = list(metrics.keys())
    values = list(metrics.values())
    N = len(labels)

    angles = np.linspace(0, 2 * np.pi, N, endpoint=False).tolist()
    values_plot = values + values[:1]
    angles += angles[:1]

    fig = plt.figure(figsize=(5.5, 5), facecolor=BG)
    ax = fig.add_subplot(111, projection='polar')
    ax.set_facecolor(SURFACE)
    ax.spines['polar'].set_color(BORDER)

    ax.fill(angles, values_plot, color=GOLD, alpha=0.15)
    ax.plot(angles, values_plot, color=GOLD, linewidth=2.2, zorder=4)
    ax.scatter(angles[:-1], values, s=40, color=GOLD, zorder=5)

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

    ax.set_title(f'{team_name} — Performance Profile', color=TEXT,
                 fontsize=11, fontweight='bold', pad=20)
    plt.tight_layout()
    return _fig(fig)


# ── MASTER FUNCTION ──────────────────────────────────────────
def generate_team_charts(data):
    """Entry point. Returns dict of chart_name -> base64."""
    charts = {}
    team_name = data.get('team', {}).get('name', 'Team')

    try:
        charts['win_loss'] = win_loss_chart(data.get('summary', {}), team_name)
    except Exception as e:
        charts['win_loss_err'] = str(e)

    try:
        charts['run_rate'] = run_rate_chart(data.get('match_records', []), team_name)
    except Exception as e:
        charts['run_rate_err'] = str(e)

    try:
        charts['phase_batting'] = phase_batting_chart(data.get('phase_data', {}), team_name)
    except Exception as e:
        charts['phase_batting_err'] = str(e)

    try:
        charts['top_batsmen'] = top_batsmen_chart(data.get('top_batsmen', []), team_name)
    except Exception as e:
        charts['top_batsmen_err'] = str(e)

    try:
        charts['top_bowlers'] = top_bowlers_chart(data.get('top_bowlers', []), team_name)
    except Exception as e:
        charts['top_bowlers_err'] = str(e)

    try:
        charts['radar'] = team_radar_chart(data, team_name)
    except Exception as e:
        charts['radar_err'] = str(e)

    return charts
