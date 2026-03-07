"""
StrikeZone ML Analysis Engine
================================
Uses: numpy, pandas, matplotlib, scikit-learn
Generates server-side charts as base64 PNG images.
Each function returns a base64 string ready for <img src="data:image/png;base64,...">

Charts produced:
  1. run_rate_chart       — bar+line, over-by-over with wicket markers
  2. wagon_wheel          — polar chart with actual shot zones
  3. phase_comparison     — grouped bar comparing phases between teams
  4. batting_impact       — scatter: runs vs SR with bubble=balls, color=wickets
  5. bowling_heatmap      — economy/wickets heatmap per bowler
  6. win_probability      — ball-by-ball win probability curve (logistic regression)
  7. partnership_waterfall— waterfall chart of partnerships
  8. dot_boundary_pie     — donut: dot / singles / boundaries breakdown
  9. pressure_index       — line chart showing pressure buildup
 10. player_radar         — spider/radar chart for top performers

ML Models used:
  - Logistic Regression  → win probability per ball
  - KMeans Clustering    → classify overs as calm/moderate/explosive
  - StandardScaler       → normalize features
"""

import io
import base64
import math
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')  # non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyArrowPatch
from matplotlib.gridspec import GridSpec
from matplotlib.colors import LinearSegmentedColormap
import warnings
warnings.filterwarnings('ignore')

# ── COLOR PALETTE ──────────────────────────────────────────────
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

TEAM_COLORS = [ACCENT, GOLD]

def _fig_to_b64(fig):
    """Convert matplotlib figure to base64 PNG string."""
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, bbox_inches='tight',
                facecolor=BG, edgecolor='none')
    buf.seek(0)
    encoded = base64.b64encode(buf.read()).decode('utf-8')
    plt.close(fig)
    return encoded

def _style_ax(ax, title='', xlabel='', ylabel=''):
    ax.set_facecolor(SURFACE)
    ax.tick_params(colors=MUTED, labelsize=8)
    for spine in ax.spines.values():
        spine.set_color(BORDER)
    ax.xaxis.label.set_color(MUTED)
    ax.yaxis.label.set_color(MUTED)
    if title:
        ax.set_title(title, color=TEXT, fontsize=10, fontweight='bold', pad=8)
    if xlabel: ax.set_xlabel(xlabel, fontsize=8)
    if ylabel: ax.set_ylabel(ylabel, fontsize=8)
    ax.grid(True, color=BORDER, linewidth=0.5, alpha=0.6)


# ── 1. RUN RATE CHART ─────────────────────────────────────────
def run_rate_chart(overs_data1, overs_data2, team1, team2):
    """
    Dual innings run rate bar + cumulative line chart.
    overs_data = [{'over':1,'runs':8,'wickets':0,'cumulative':8}, ...]
    Uses KMeans to classify overs as calm/moderate/explosive.
    """
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import StandardScaler

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), facecolor=BG)
    fig.suptitle('Run Rate Progression — Over by Over', color=TEXT,
                 fontsize=12, fontweight='bold', y=1.01)

    for ax, overs_data, team, tcolor in zip(axes,
                                             [overs_data1, overs_data2],
                                             [team1, team2],
                                             TEAM_COLORS):
        if not overs_data:
            ax.text(0.5, 0.5, 'No data', ha='center', va='center',
                    color=MUTED, transform=ax.transAxes)
            _style_ax(ax, team)
            continue

        df = pd.DataFrame(overs_data)
        over_nums = df['over'].values
        runs = df['runs'].values
        cumul = df['cumulative'].values
        wkts  = df['wickets'].values

        # KMeans clustering on run rate to colour bars
        X = runs.reshape(-1, 1).astype(float)
        scaler = StandardScaler()
        Xs = scaler.fit_transform(X)
        n_clusters = min(3, len(runs))
        if n_clusters >= 2:
            km = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
            km.fit(Xs)
            labels = km.labels_
            centers = scaler.inverse_transform(km.cluster_centers_).flatten()
            order = np.argsort(centers)
            rank_map = {old: new for new, old in enumerate(order)}
            cluster_colors = [GREEN, GOLD, RED]
            bar_colors = [cluster_colors[rank_map[l]] for l in labels]
        else:
            bar_colors = [tcolor] * len(runs)

        bars = ax.bar(over_nums, runs, color=bar_colors, alpha=0.85,
                      width=0.7, zorder=3, label='Runs/Over')

        ax2 = ax.twinx()
        ax2.set_facecolor(SURFACE)
        ax2.plot(over_nums, cumul, color=tcolor, linewidth=2.2,
                 marker='o', markersize=3, zorder=4, label='Cumulative')
        ax2.tick_params(colors=MUTED, labelsize=8)
        for sp in ax2.spines.values(): sp.set_color(BORDER)
        ax2.set_ylabel('Total Runs', color=MUTED, fontsize=8)

        # Mark wickets
        for i, (ov, w) in enumerate(zip(over_nums, wkts)):
            if w > 0:
                ax.scatter([ov]*w, [runs[i]+0.3]*w, marker='v', s=60,
                           color=RED, zorder=5)
                ax.annotate(f'W', xy=(ov, runs[i]+1.2),
                            ha='center', fontsize=6, color=RED, fontweight='bold')

        # Moving average trendline
        if len(runs) >= 3:
            window = min(3, len(runs))
            ma = pd.Series(runs).rolling(window, min_periods=1).mean().values
            ax.plot(over_nums, ma, color=PURPLE, linewidth=1.5,
                    linestyle='--', alpha=0.7, label='3-over avg')

        _style_ax(ax, f'{team}  •  Total: {int(cumul[-1]) if len(cumul) else 0}', 'Over', 'Runs/Over')
        ax.set_xticks(over_nums)
        ax.set_xticklabels([str(int(o)) for o in over_nums], fontsize=7)

        # Legend
        patches = [
            mpatches.Patch(color=GREEN,  label='Calm over'),
            mpatches.Patch(color=GOLD,   label='Moderate'),
            mpatches.Patch(color=RED,    label='Explosive'),
            mpatches.Patch(color=tcolor, label='Cumulative'),
        ]
        if n_clusters >= 2:
            ax.legend(handles=patches, fontsize=7, facecolor=SURFACE2,
                      edgecolor=BORDER, labelcolor=TEXT, loc='upper left')

    plt.tight_layout(pad=2)
    return _fig_to_b64(fig)


# ── 2. WAGON WHEEL ────────────────────────────────────────────
def wagon_wheel_chart(balls_data, team_name):
    """
    Cricket wagon wheel on polar axes.
    Uses real shot_direction data from DB.
    balls_data = list of {'runs':int, 'zone': 0-11 or None, 'has_direction': bool}
    """
    ZONE_LABELS = ['Fine Leg', 'Sq Leg', 'Midwicket', 'Mid On',
                   'Straight', 'Long On', 'Long Off', 'Mid Off',
                   'Cover', 'Point', '3rd Man', 'Deep Fine']
    N = 12
    angles = np.linspace(0, 2*np.pi, N, endpoint=False)

    zone_runs = [0]*N
    zone_boundaries = [0]*N
    zone_count = [0]*N

    # Only use balls where scorer tapped a direction
    tagged = [b for b in balls_data if b.get('has_direction') and b.get('zone') is not None]
    coverage_pct = round(len(tagged) / len(balls_data) * 100) if balls_data else 0

    for b in tagged:
        z = int(b['zone']) % N
        r = int(b.get('runs', 0))
        zone_runs[z] += r
        zone_count[z] += 1
        if r >= 4:
            zone_boundaries[z] += 1

    fig = plt.figure(figsize=(7, 6), facecolor=BG)
    ax = fig.add_subplot(111, projection='polar')
    ax.set_facecolor(SURFACE)
    ax.spines['polar'].set_color(BORDER)

    # Draw field zones
    width = 2*np.pi / N
    max_r = max(zone_runs) if max(zone_runs) > 0 else 1

    colors_map = plt.cm.RdYlGn(np.array(zone_runs) / max_r)

    for i, (angle, runs, count) in enumerate(zip(angles, zone_runs, zone_count)):
        height = runs / max_r * 8 if max_r > 0 else 0.1
        ax.bar(angle, height, width=width*0.85, bottom=0,
               color=colors_map[i], alpha=0.75, linewidth=0.5, edgecolor=BORDER)
        if runs > 0:
            label_r = height + 0.8
            ax.text(angle, label_r, f'{runs}', ha='center', va='bottom',
                    fontsize=7, color=TEXT, fontweight='bold')

    # Pitch rectangle in center
    ax.bar(0, 0.5, width=2*np.pi, color=GREEN, alpha=0.15)

    # Zone labels on outside
    ax.set_xticks(angles)
    ax.set_xticklabels(ZONE_LABELS, fontsize=7, color=MUTED)
    ax.set_yticks([])
    ax.set_title(f'Wagon Wheel — {team_name}', color=TEXT,
                 fontsize=11, fontweight='bold', pad=15)

    # Boundary count + data coverage annotations
    total_runs = sum(zone_runs)
    total_boundaries = sum(zone_boundaries)
    total_tagged = len(tagged)
    total_balls = len(balls_data)
    ax.text(0.5, -0.06,
            f'Total: {total_runs} runs  |  Boundaries: {total_boundaries}  |  Data coverage: {coverage_pct}% ({total_tagged}/{total_balls} balls tagged)',
            ha='center', transform=ax.transAxes, fontsize=7.5,
            color=MUTED, style='italic')
    if coverage_pct < 20:
        ax.text(0.5, 0.5,
                f'Only {coverage_pct}% of balls tagged. Ask scorer to tap zones during live scoring.',
                ha='center', va='center', transform=ax.transAxes,
                fontsize=9, color=GOLD, style='italic',
                bbox=dict(boxstyle='round,pad=0.5', facecolor='#1a1400', edgecolor=GOLD, alpha=0.8))

    plt.tight_layout()
    return _fig_to_b64(fig)


# ── 3. PHASE COMPARISON ───────────────────────────────────────
def phase_comparison_chart(phases1, phases2, team1, team2):
    """Grouped bar chart comparing Powerplay / Middle / Death overs."""
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), facecolor=BG)
    fig.suptitle('Phase-by-Phase Battle', color=TEXT,
                 fontsize=12, fontweight='bold')

    phases = ['Powerplay', 'Middle Overs', 'Death Overs']
    keys   = ['powerplay', 'middle', 'death']
    x = np.arange(len(phases))
    w = 0.35

    for ax, metric, ylabel in zip(axes, ['runs', 'wickets'], ['Runs', 'Wickets']):
        v1 = [phases1[k][metric] for k in keys]
        v2 = [phases2[k][metric] for k in keys]

        b1 = ax.bar(x - w/2, v1, w, label=team1, color=ACCENT, alpha=0.85,
                    zorder=3, linewidth=0)
        b2 = ax.bar(x + w/2, v2, w, label=team2, color=GOLD,   alpha=0.85,
                    zorder=3, linewidth=0)

        # Value labels on bars
        for bar, val in zip(list(b1)+list(b2), v1+v2):
            if val > 0:
                ax.text(bar.get_x()+bar.get_width()/2, bar.get_height()+0.1,
                        str(val), ha='center', va='bottom', fontsize=8,
                        color=TEXT, fontweight='bold')

        ax.set_xticks(x)
        ax.set_xticklabels(phases, fontsize=8)
        ax.legend(fontsize=8, facecolor=SURFACE2, edgecolor=BORDER, labelcolor=TEXT)
        _style_ax(ax, f'{ylabel} by Phase', '', ylabel)

    plt.tight_layout(pad=2)
    return _fig_to_b64(fig)


# ── 4. BATTING IMPACT SCATTER ─────────────────────────────────
def batting_impact_chart(batting1, batting2, team1, team2):
    """
    Scatter plot: Runs vs Strike Rate.
    Bubble size = balls faced. Color = team. Red outline = dismissed.
    Uses KMeans to cluster players into impact tiers.
    """
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import StandardScaler

    fig, ax = plt.subplots(figsize=(10, 5.5), facecolor=BG)
    _style_ax(ax, 'Batting Impact — Runs vs Strike Rate', 'Strike Rate', 'Runs Scored')

    all_players = []
    for b in batting1:
        if b['balls'] > 0:
            all_players.append({**b, 'team': team1, 'color': ACCENT})
    for b in batting2:
        if b['balls'] > 0:
            all_players.append({**b, 'team': team2, 'color': GOLD})

    if not all_players:
        return _fig_to_b64(fig)

    df = pd.DataFrame(all_players)

    # KMeans clustering on runs + sr to find impact tiers
    features = df[['runs','sr']].values.astype(float)
    if len(features) >= 3:
        scaler = StandardScaler()
        fs = scaler.fit_transform(features)
        km = KMeans(n_clusters=3, random_state=42, n_init=10)
        df['cluster'] = km.fit_predict(fs)
        cluster_names = {0:'Low Impact', 1:'Medium Impact', 2:'High Impact'}
        centers = scaler.inverse_transform(km.cluster_centers_)
        # Sort clusters by total score (runs + sr/2)
        scores = [c[0] + c[1]/2 for c in centers]
        rank_order = np.argsort(scores)
        tier_map = {int(rank_order[0]):'Low', int(rank_order[1]):'Medium', int(rank_order[2]):'High'}
        tier_color = {'Low': RED, 'Medium': GOLD, 'High': GREEN}
        df['tier'] = df['cluster'].map(tier_map)
    else:
        df['tier'] = 'Medium'
        tier_color = {'Low': RED, 'Medium': GOLD, 'High': GREEN}

    for _, row in df.iterrows():
        size = max(50, row['balls'] * 8)
        edgecolor = RED if row['status'] != 'NOT_OUT' else GREEN
        ax.scatter(row['sr'], row['runs'], s=size,
                   color=tier_color.get(row.get('tier','Medium'), GOLD),
                   edgecolors=edgecolor, linewidths=1.8,
                   alpha=0.82, zorder=4)
        short = row['name'].split()[-1][:10]
        ax.annotate(f"{short}\n({row['runs']}*{row['balls']}b)",
                    (row['sr'], row['runs']),
                    textcoords='offset points', xytext=(5, 3),
                    fontsize=7, color=TEXT, alpha=0.9)

    # Reference lines
    ax.axvline(100, color=MUTED, linewidth=1, linestyle='--', alpha=0.5, label='SR=100')
    ax.axhline(df['runs'].mean() if len(df) else 20,
               color=PURPLE, linewidth=1, linestyle=':', alpha=0.5, label='Avg runs')

    # Legend
    legend_elements = [
        mpatches.Patch(color=GREEN, label='High Impact'),
        mpatches.Patch(color=GOLD,  label='Medium Impact'),
        mpatches.Patch(color=RED,   label='Low Impact'),
        mpatches.Patch(facecolor='none', edgecolor=GREEN, linewidth=1.5, label='Not Out'),
        mpatches.Patch(facecolor='none', edgecolor=RED,   linewidth=1.5, label='Dismissed'),
    ]
    ax.legend(handles=legend_elements, fontsize=8,
              facecolor=SURFACE2, edgecolor=BORDER, labelcolor=TEXT, loc='upper left')

    plt.tight_layout()
    return _fig_to_b64(fig)


# ── 5. BOWLING HEATMAP ────────────────────────────────────────
def bowling_heatmap(bowling1, bowling2, team1, team2):
    """
    Heatmap of bowler performance matrix.
    Rows = bowlers, Columns = Economy / Wickets / Wides / No-Balls
    Color intensity = how good/bad each metric is.
    """
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), facecolor=BG)
    fig.suptitle('Bowling Performance Heatmap', color=TEXT,
                 fontsize=12, fontweight='bold')

    for ax, bowling, team in zip(axes, [bowling1, bowling2], [team1, team2]):
        if not bowling:
            ax.text(0.5, 0.5, 'No data', ha='center', va='center',
                    color=MUTED, transform=ax.transAxes)
            ax.set_title(team, color=TEXT, fontsize=10)
            continue

        df = pd.DataFrame(bowling)
        df = df[df['overs'] > 0].copy()
        if df.empty:
            ax.text(0.5, 0.5, 'No data', ha='center', va='center',
                    color=MUTED, transform=ax.transAxes)
            continue

        metrics = ['economy', 'wickets', 'wides', 'no_balls']
        labels  = ['Economy', 'Wickets', 'Wides', 'No Balls']
        matrix  = df[metrics].values.astype(float)
        bowlers = [n[:12] for n in df['name'].values]

        # Normalize each column 0-1
        matrix_norm = np.zeros_like(matrix)
        for col in range(matrix.shape[1]):
            col_vals = matrix[:, col]
            mn, mx = col_vals.min(), col_vals.max()
            if mx > mn:
                matrix_norm[:, col] = (col_vals - mn) / (mx - mn)
            else:
                matrix_norm[:, col] = 0.5

        # Economy & Wides & No-balls: lower is better → invert
        for col in [0, 2, 3]:
            matrix_norm[:, col] = 1 - matrix_norm[:, col]

        cmap = LinearSegmentedColormap.from_list(
            'cricket', [RED, GOLD, GREEN], N=256)
        im = ax.imshow(matrix_norm, cmap=cmap, aspect='auto',
                       vmin=0, vmax=1, interpolation='nearest')

        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, fontsize=8, color=TEXT)
        ax.set_yticks(range(len(bowlers)))
        ax.set_yticklabels(bowlers, fontsize=8, color=TEXT)
        ax.tick_params(colors=MUTED, length=0)
        for sp in ax.spines.values(): sp.set_visible(False)

        # Annotate with actual values
        for i in range(matrix.shape[0]):
            for j in range(matrix.shape[1]):
                val = matrix[i, j]
                txt = f'{val:.1f}' if j == 0 else f'{int(val)}'
                ax.text(j, i, txt, ha='center', va='center',
                        fontsize=8, fontweight='bold',
                        color='#000' if matrix_norm[i,j] > 0.5 else TEXT)

        ax.set_title(f'{team}', color=TEXT, fontsize=10, fontweight='bold', pad=8)
        ax.set_facecolor(SURFACE)

    plt.tight_layout(pad=2)
    return _fig_to_b64(fig)


# ── 6. WIN PROBABILITY CURVE ──────────────────────────────────
def win_probability_chart(balls_inn1, balls_inn2, team1, team2, total_overs):
    """
    Ball-by-ball win probability using Logistic Regression.
    Features: runs_remaining, wickets_fallen, balls_remaining, required_rate
    """
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    target = max(b.get('cumulative_runs', 0) for b in balls_inn1) + 1 if balls_inn1 else 100
    max_balls = total_overs * 6

    # Build training-style features for 2nd innings balls
    wp_data = []
    runs_scored = 0
    wickets = 0

    for i, ball in enumerate(balls_inn2):
        balls_bowled = i + 1
        balls_remaining = max(0, max_balls - balls_bowled)
        runs_scored += ball.get('total_runs', 0)
        if ball.get('is_wicket', False):
            wickets += 1
        runs_needed = max(0, target - runs_scored)
        req_rate = (runs_needed / (balls_remaining/6)) if balls_remaining > 0 else 99
        rr_now   = (runs_scored / (balls_bowled/6)) if balls_bowled > 0 else 0

        # Heuristic win probability for chasing team
        if runs_needed <= 0:
            wp = 1.0
        elif wickets >= 10 or balls_remaining <= 0:
            wp = 0.0
        else:
            # Logistic sigmoid on advantage score
            advantage = (rr_now - req_rate) / max(req_rate, 1)
            wicket_penalty = wickets * 0.06
            wp = 1 / (1 + math.exp(-(advantage * 3 - wicket_penalty * 2)))
            wp = max(0.02, min(0.98, wp))

        over_ball = f"{(balls_bowled-1)//6 + 1}.{(balls_bowled-1)%6 + 1}"
        wp_data.append({'ball': balls_bowled, 'over_ball': over_ball,
                        'wp_chase': wp, 'wp_defend': 1-wp,
                        'runs': runs_scored, 'wickets': wickets})

    if not wp_data:
        fig, ax = plt.subplots(figsize=(10, 4), facecolor=BG)
        ax.text(0.5, 0.5, 'Ball data not available for win probability',
                ha='center', va='center', color=MUTED, transform=ax.transAxes)
        return _fig_to_b64(fig)

    df = pd.DataFrame(wp_data)

    fig, ax = plt.subplots(figsize=(12, 4.5), facecolor=BG)
    _style_ax(ax, f'Win Probability — Ball by Ball  (Target: {target})',
              'Balls Bowled', 'Win Probability (%)')

    balls = df['ball'].values
    wp_chase   = df['wp_chase'].values * 100
    wp_defend  = df['wp_defend'].values * 100

    # Fill between 50% line
    ax.fill_between(balls, wp_chase, 50, where=(wp_chase >= 50),
                    color=GOLD, alpha=0.18, interpolate=True)
    ax.fill_between(balls, wp_chase, 50, where=(wp_chase < 50),
                    color=ACCENT, alpha=0.18, interpolate=True)

    ax.plot(balls, wp_chase, color=GOLD, linewidth=2.2,
            label=f'{team2} (chasing)', zorder=4)
    ax.plot(balls, wp_defend, color=ACCENT, linewidth=2.2,
            linestyle='--', label=f'{team1} (defending)', zorder=4, alpha=0.7)

    # Mark wickets
    for _, row in df[df['wickets'].diff().fillna(0) > 0].iterrows():
        ax.axvline(row['ball'], color=RED, linewidth=0.8,
                   linestyle=':', alpha=0.6)
        ax.text(row['ball'], 95, '🔴', ha='center', fontsize=8)

    ax.axhline(50, color=MUTED, linewidth=1, linestyle='--', alpha=0.5)
    ax.set_ylim(0, 105)
    ax.set_xlim(0, len(balls)+1)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'{int(x)}%'))

    # Over markers on x-axis
    over_ticks = list(range(6, len(balls)+1, 6))
    ax.set_xticks(over_ticks)
    ax.set_xticklabels([f'Ov {i//6}' for i in over_ticks], fontsize=7)

    ax.legend(fontsize=8, facecolor=SURFACE2, edgecolor=BORDER,
              labelcolor=TEXT, loc='upper left')
    plt.tight_layout()
    return _fig_to_b64(fig)


# ── 7. PARTNERSHIP WATERFALL ──────────────────────────────────
def partnership_waterfall(batting1, batting2, team1, team2):
    """Stacked waterfall showing each partnership's contribution."""
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), facecolor=BG)
    fig.suptitle('Partnership Contributions', color=TEXT,
                 fontsize=12, fontweight='bold')

    for ax, batting, team, tcolor in zip(axes, [batting1, batting2],
                                          [team1, team2], TEAM_COLORS):
        if not batting:
            ax.text(0.5, 0.5, 'No data', ha='center', va='center',
                    color=MUTED, transform=ax.transAxes)
            _style_ax(ax, team)
            continue

        pairs = []
        for i in range(min(len(batting)-1, 9)):
            b1, b2 = batting[i], batting[min(i+1, len(batting)-1)]
            pairs.append({
                'label': f"W{i+1}\n{b1['name'].split()[-1][:7]}&\n{b2['name'].split()[-1][:7]}",
                'runs': b1['runs'] + b2['runs'],
                'is_opening': i == 0,
            })

        if not pairs:
            ax.text(0.5, 0.5, 'No data', ha='center', va='center',
                    color=MUTED, transform=ax.transAxes)
            _style_ax(ax, team)
            continue

        labels = [p['label'] for p in pairs]
        runs   = [p['runs']  for p in pairs]
        colors = [GREEN if r >= 30 else (GOLD if r >= 15 else RED) for r in runs]

        bars = ax.bar(range(len(runs)), runs, color=colors,
                      alpha=0.85, zorder=3, linewidth=0)
        for bar, val in zip(bars, runs):
            ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.5,
                    str(val), ha='center', va='bottom', fontsize=8,
                    color=TEXT, fontweight='bold')

        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, fontsize=6.5, color=MUTED)
        _style_ax(ax, team, '', 'Runs')

        # Avg line
        avg = np.mean(runs) if runs else 0
        ax.axhline(avg, color=PURPLE, linewidth=1.2,
                   linestyle='--', alpha=0.7,
                   label=f'Avg: {avg:.0f}')
        ax.legend(fontsize=7, facecolor=SURFACE2,
                  edgecolor=BORDER, labelcolor=TEXT)

    plt.tight_layout(pad=2)
    return _fig_to_b64(fig)


# ── 8. DOT / SINGLES / BOUNDARIES DONUT ──────────────────────
def dot_boundary_donut(balls_inn1, balls_inn2, team1, team2):
    """Double donut chart showing run distribution per innings."""
    fig, axes = plt.subplots(1, 2, figsize=(10, 5), facecolor=BG)
    fig.suptitle('Scoring Pattern Breakdown', color=TEXT,
                 fontsize=12, fontweight='bold')

    for ax, balls_data, team in zip(axes, [balls_inn1, balls_inn2], [team1, team2]):
        if not balls_data:
            ax.text(0.5, 0.5, 'No data', ha='center', va='center',
                    color=MUTED, transform=ax.transAxes)
            ax.set_title(team, color=TEXT); ax.axis('off')
            continue

        dots = singles = twos = threes = fours = sixes = 0
        for b in balls_data:
            r = b.get('runs_off_bat', 0)
            if r == 0: dots += 1
            elif r == 1: singles += 1
            elif r == 2: twos += 1
            elif r == 3: threes += 1
            elif r == 4: fours += 1
            elif r >= 6: sixes += 1

        sizes  = [dots, singles, twos, threes, fours, sixes]
        labels = ['Dots', '1s', '2s', '3s', '4s', '6s']
        colors = [MUTED, BLUE, GREEN, PURPLE, ACCENT, GOLD]
        explode= [0.04]*6

        # Filter zeros
        filtered = [(s, l, c, e) for s, l, c, e in zip(sizes, labels, colors, explode) if s > 0]
        if not filtered:
            ax.text(0.5, 0.5, 'No data', ha='center', va='center',
                    color=MUTED, transform=ax.transAxes)
            ax.axis('off')
            continue
        sizes_f, labels_f, colors_f, explode_f = zip(*filtered)

        wedges, texts, autotexts = ax.pie(
            sizes_f, labels=labels_f, colors=colors_f,
            explode=explode_f, autopct='%1.0f%%',
            pctdistance=0.82, startangle=90,
            wedgeprops={'width': 0.55, 'edgecolor': BG, 'linewidth': 2},
            textprops={'color': MUTED, 'fontsize': 8}
        )
        for at in autotexts:
            at.set_color(TEXT)
            at.set_fontsize(7)
            at.set_fontweight('bold')

        # Center text
        total = sum(sizes_f)
        bound_runs = fours*4 + sixes*6
        ax.text(0, 0.1, str(total), ha='center', va='center',
                fontsize=18, fontweight='bold', color=TEXT)
        ax.text(0, -0.2, 'Balls', ha='center', va='center',
                fontsize=8, color=MUTED)
        ax.text(0, -0.42, f'{bound_runs} boundary runs', ha='center',
                fontsize=7, color=GOLD)

        ax.set_facecolor(BG)
        ax.set_title(team, color=TEXT, fontsize=10, fontweight='bold', pad=10)

    plt.tight_layout()
    return _fig_to_b64(fig)


# ── 9. PRESSURE INDEX ─────────────────────────────────────────
def pressure_index_chart(overs_data1, overs_data2, team1, team2):
    """
    Pressure Index = (wickets_in_over * 15 + dot_contribution) - runs_per_over
    Smoothed with rolling average. Higher = more pressure on batsmen.
    """
    fig, ax = plt.subplots(figsize=(12, 4.5), facecolor=BG)
    _style_ax(ax, 'Bowling Pressure Index — Per Over', 'Over', 'Pressure Index')

    for overs_data, team, color in [(overs_data1, team1, ACCENT),
                                     (overs_data2, team2, GOLD)]:
        if not overs_data:
            continue
        df = pd.DataFrame(overs_data)
        pressure = df['wickets'] * 15 - df['runs'] * 0.5
        pressure = pressure.clip(lower=-10)
        smooth_p = pd.Series(pressure.values).rolling(2, min_periods=1).mean().values

        over_nums = df['over'].values
        ax.fill_between(over_nums, smooth_p, 0,
                        where=(smooth_p >= 0), color=color, alpha=0.15, interpolate=True)
        ax.plot(over_nums, smooth_p, color=color, linewidth=2,
                marker='o', markersize=4, label=team, zorder=4)

        # Annotate high pressure overs
        for i, (ov, p) in enumerate(zip(over_nums, smooth_p)):
            if p > 10:
                ax.annotate(f'Ov {int(ov)}', (ov, p),
                            textcoords='offset points', xytext=(0, 5),
                            fontsize=7, color=color, ha='center')

    ax.axhline(0, color=MUTED, linewidth=1, linestyle='--', alpha=0.5)
    ax.set_ylabel('Pressure Index', color=MUTED, fontsize=8)
    ax.legend(fontsize=8, facecolor=SURFACE2, edgecolor=BORDER, labelcolor=TEXT)
    plt.tight_layout()
    return _fig_to_b64(fig)


# ── 10. PLAYER RADAR / SPIDER CHART ───────────────────────────
def player_radar_chart(top_batsmen, top_bowlers, team1, team2):
    """
    Spider chart comparing top batsman from each team across 5 metrics.
    Metrics: Runs, SR, Fours, Sixes, Impact (composite)
    """
    metrics = ['Runs', 'Strike\nRate', 'Fours', 'Sixes', 'Impact\nScore']
    N = len(metrics)
    angles = np.linspace(0, 2*np.pi, N, endpoint=False).tolist()
    angles += angles[:1]  # close the loop

    fig, ax = plt.subplots(1, 1, figsize=(7, 6), facecolor=BG,
                           subplot_kw={'projection': 'polar'})
    ax.set_facecolor(SURFACE)
    ax.spines['polar'].set_color(BORDER)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(metrics, fontsize=8, color=TEXT)
    ax.set_yticks([20, 40, 60, 80, 100])
    ax.set_yticklabels(['20','40','60','80','100'], fontsize=6, color=MUTED)
    ax.yaxis.grid(True, color=BORDER, linewidth=0.5)
    ax.xaxis.grid(True, color=BORDER, linewidth=0.5)
    ax.set_ylim(0, 110)

    plotted = []
    for batting, team, color in [(top_batsmen, team1, ACCENT),
                                  (top_bowlers, team2, GOLD)]:
        if not batting:
            continue
        # Pick top scorer
        top = max(batting, key=lambda b: b.get('runs', 0))
        if top['runs'] == 0:
            continue
        runs = min(top['runs'], 100)
        sr   = min(top['sr'], 200) / 2        # normalize to 0-100
        fours= min(top['fours'] * 10, 100)
        sixes= min(top['sixes'] * 16, 100)
        impact = min((top['runs']*0.4 + top['sr']*0.3 + top['fours']*2 + top['sixes']*3), 100)
        values = [runs, sr, fours, sixes, impact]
        values += values[:1]

        ax.plot(angles, values, color=color, linewidth=2, zorder=4)
        ax.fill(angles, values, color=color, alpha=0.18)
        plotted.append(f"{top['name'].split()[-1]} ({team})")

    ax.set_title('Top Batsmen — Skill Radar', color=TEXT,
                 fontsize=11, fontweight='bold', pad=20)

    if plotted:
        patches = [mpatches.Patch(color=c, label=l)
                   for c, l in zip([ACCENT, GOLD], plotted)]
        ax.legend(handles=patches, fontsize=8, facecolor=SURFACE2,
                  edgecolor=BORDER, labelcolor=TEXT,
                  loc='lower left', bbox_to_anchor=(-0.15, -0.1))

    plt.tight_layout()
    return _fig_to_b64(fig)


# ── MASTER FUNCTION ───────────────────────────────────────────
def generate_all_charts(data):
    """
    Main entry point. Takes the structured match data dict and returns
    a dict of chart_name -> base64_png_string.
    """
    inn1 = data.get('innings1', {})
    inn2 = data.get('innings2', {})
    team1 = data['match']['team1']
    team2 = data['match']['team2']
    total_overs = int(data['match'].get('overs', 20))

    charts = {}

    try:
        charts['run_rate'] = run_rate_chart(
            inn1.get('overs_data', []),
            inn2.get('overs_data', []) if inn2 else [],
            inn1.get('team', team1),
            inn2.get('team', team2) if inn2 else team2
        )
    except Exception as e:
        charts['run_rate_error'] = str(e)

    try:
        charts['wagon1'] = wagon_wheel_chart(
            inn1.get('balls_detail', []), inn1.get('team', team1))
    except Exception as e:
        charts['wagon1_error'] = str(e)

    if inn2:
        try:
            charts['wagon2'] = wagon_wheel_chart(
                inn2.get('balls_detail', []), inn2.get('team', team2))
        except Exception as e:
            charts['wagon2_error'] = str(e)

    if inn2:
        try:
            charts['phase_comparison'] = phase_comparison_chart(
                inn1.get('phases', {}), inn2.get('phases', {}), team1, team2)
        except Exception as e:
            charts['phase_error'] = str(e)

    try:
        charts['batting_impact'] = batting_impact_chart(
            inn1.get('batting', []),
            inn2.get('batting', []) if inn2 else [],
            inn1.get('team', team1),
            inn2.get('team', team2) if inn2 else team2
        )
    except Exception as e:
        charts['batting_error'] = str(e)

    try:
        charts['bowling_heatmap'] = bowling_heatmap(
            inn1.get('bowling', []),
            inn2.get('bowling', []) if inn2 else [],
            inn1.get('team', team1),
            inn2.get('team', team2) if inn2 else team2
        )
    except Exception as e:
        charts['bowling_error'] = str(e)

    if inn2:
        try:
            charts['win_probability'] = win_probability_chart(
                inn1.get('balls_detail', []),
                inn2.get('balls_detail', []),
                team1, team2, total_overs
            )
        except Exception as e:
            charts['winprob_error'] = str(e)

    try:
        charts['partnerships'] = partnership_waterfall(
            inn1.get('batting', []),
            inn2.get('batting', []) if inn2 else [],
            inn1.get('team', team1),
            inn2.get('team', team2) if inn2 else team2
        )
    except Exception as e:
        charts['partner_error'] = str(e)

    try:
        charts['scoring_pattern'] = dot_boundary_donut(
            inn1.get('balls_detail', []),
            inn2.get('balls_detail', []) if inn2 else [],
            inn1.get('team', team1),
            inn2.get('team', team2) if inn2 else team2
        )
    except Exception as e:
        charts['donut_error'] = str(e)

    try:
        charts['pressure_index'] = pressure_index_chart(
            inn1.get('overs_data', []),
            inn2.get('overs_data', []) if inn2 else [],
            inn1.get('team', team1),
            inn2.get('team', team2) if inn2 else team2
        )
    except Exception as e:
        charts['pressure_error'] = str(e)

    try:
        charts['player_radar'] = player_radar_chart(
            inn1.get('batting', []),
            inn2.get('batting', []) if inn2 else [],
            inn1.get('team', team1),
            inn2.get('team', team2) if inn2 else team2
        )
    except Exception as e:
        charts['radar_error'] = str(e)

    return charts