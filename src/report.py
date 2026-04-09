from database_functions import get_hr_report_data
from datetime import datetime
import tempfile
import os

def generate_hr_report(user_id: str, guild_id: str, reports_dir: str) -> str | None:
    """
    Generates a styled HTML HR report for a given user.
    Returns the file path of the saved report, or None if user not found.
    """
    rows = get_hr_report_data(user_id, guild_id)
    if not rows:
        return None

    user = rows[0]
    username = user["username"]
    total_debt = user["social_credit_score"]
    status_role = user["current_status_role"]

    # Filter out rows where there's no infraction (LEFT JOIN can produce nulls)
    infractions = [r for r in rows if r.get("infraction_id") is not None]

    # Build cumulative score over time for the chart
    # Sort oldest first, accumulate score
    sorted_infractions = sorted(infractions, key=lambda r: r["timestamp"])
    chart_labels = []
    chart_values = []
    running = 0
    for inf in sorted_infractions:
        running += inf["score_penalty"]
        chart_labels.append(inf["timestamp"].strftime("%b %d %H:%M"))
        chart_values.append(running)

    # If no infractions yet, show a flat zero line
    if not chart_labels:
        chart_labels = [datetime.now().strftime("%b %d")]
        chart_values = [0]

    # Severity badge colors
    severity_colors = {
        "Low":      "#22c55e",
        "Medium":   "#f59e0b",
        "High":     "#f97316",
        "Critical": "#ef4444",
    }

    # Status badge color
    status_color = "#22c55e" if status_role == "HR Approved" else "#ef4444"

    # Build infraction rows HTML
    infraction_rows_html = ""
    if infractions:
        for inf in reversed(sorted_infractions):  # newest first
            sev = inf.get("severity_level", "Low")
            color = severity_colors.get(sev, "#94a3b8")
            ts = inf["timestamp"].strftime("%Y-%m-%d %H:%M")
            context = inf.get("violation_context", "N/A")
            inference = inf.get("stacy_inference", "N/A")
            penalty = inf.get("score_penalty", 0)

            infraction_rows_html += f"""
            <tr>
                <td>{ts}</td>
                <td><span class="badge" style="background:{color}">{sev}</span></td>
                <td>+{penalty}</td>
                <td class="context-cell" title="{context}">{context[:80]}{'...' if len(context) > 80 else ''}</td>
                <td class="context-cell" title="{inference}">{inference[:80]}{'...' if len(inference) > 80 else ''}</td>
            </tr>
            """
    else:
        infraction_rows_html = """
            <tr><td colspan="5" style="text-align:center; color:#64748b; padding:2rem;">
                ✅ No infractions on record. Clean slate!
            </td></tr>
        """

    html = f"""<!DOCTYPE html>
    <html lang="en">
    <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HR Report — {username}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Inter:wght@300;400;500;600&display=swap');

    :root {{
        --bg:        #0a0e1a;
        --surface:   #111827;
        --border:    #1e2d40;
        --accent:    #e11d48;
        --accent2:   #f43f5e;
        --text:      #e2e8f0;
        --muted:     #64748b;
        --mono:      'Space Mono', monospace;
        --sans:      'Inter', sans-serif;
    }}

    * {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
        background: var(--bg);
        color: var(--text);
        font-family: var(--sans);
        min-height: 100vh;
        padding: 2rem;
    }}

    .scanline {{
        position: fixed; inset: 0; pointer-events: none; z-index: 999;
        background: repeating-linear-gradient(
        0deg, transparent, transparent 2px,
        rgba(0,0,0,0.08) 2px, rgba(0,0,0,0.08) 4px
        );
    }}

    .container {{
        max-width: 960px;
        margin: 0 auto;
    }}

    /* HEADER */
    .header {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        border-bottom: 1px solid var(--border);
        padding-bottom: 1.5rem;
        margin-bottom: 2rem;
    }}

    .header-left h1 {{
        font-family: var(--mono);
        font-size: 0.75rem;
        letter-spacing: 0.2em;
        color: var(--accent);
        text-transform: uppercase;
        margin-bottom: 0.5rem;
    }}

    .header-left h2 {{
        font-family: var(--mono);
        font-size: 2rem;
        color: var(--text);
        letter-spacing: -0.02em;
    }}

    .header-right {{
        text-align: right;
        font-family: var(--mono);
        font-size: 0.7rem;
        color: var(--muted);
        line-height: 1.8;
    }}

    /* STAT CARDS */
    .stats {{
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 1rem;
        margin-bottom: 2rem;
    }}

    .stat-card {{
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 1.25rem 1.5rem;
        position: relative;
        overflow: hidden;
    }}

    .stat-card::before {{
        content: '';
        position: absolute;
        top: 0; left: 0; right: 0;
        height: 2px;
        background: var(--accent);
    }}

    .stat-card .label {{
        font-size: 0.65rem;
        letter-spacing: 0.15em;
        text-transform: uppercase;
        color: var(--muted);
        font-family: var(--mono);
        margin-bottom: 0.5rem;
    }}

    .stat-card .value {{
        font-family: var(--mono);
        font-size: 2rem;
        font-weight: 700;
        color: var(--text);
    }}

    .stat-card .value.danger {{ color: var(--accent); }}

    /* BADGE */
    .badge {{
        display: inline-block;
        padding: 0.2rem 0.6rem;
        border-radius: 4px;
        font-family: var(--mono);
        font-size: 0.7rem;
        font-weight: 700;
        letter-spacing: 0.05em;
        color: #fff;
    }}

    /* CHART */
    .chart-section {{
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 1.5rem;
        margin-bottom: 2rem;
    }}

    .section-title {{
        font-family: var(--mono);
        font-size: 0.7rem;
        letter-spacing: 0.2em;
        text-transform: uppercase;
        color: var(--accent);
        margin-bottom: 1.25rem;
    }}

    .chart-wrapper {{
        position: relative;
        height: 220px;
    }}

    /* TABLE */
    .table-section {{
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 1.5rem;
        margin-bottom: 2rem;
        overflow-x: auto;
    }}

    table {{
        width: 100%;
        border-collapse: collapse;
        font-size: 0.8rem;
    }}

    thead th {{
        font-family: var(--mono);
        font-size: 0.65rem;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        color: var(--muted);
        text-align: left;
        padding: 0.6rem 0.75rem;
        border-bottom: 1px solid var(--border);
    }}

    tbody tr {{
        border-bottom: 1px solid var(--border);
        transition: background 0.15s;
    }}

    tbody tr:hover {{ background: rgba(255,255,255,0.03); }}
    tbody tr:last-child {{ border-bottom: none; }}

    tbody td {{
        padding: 0.75rem;
        color: var(--text);
        vertical-align: top;
    }}

    .context-cell {{
        color: var(--muted);
        font-size: 0.75rem;
        max-width: 220px;
        cursor: help;
    }}

    /* FOOTER */
    .footer {{
        text-align: center;
        font-family: var(--mono);
        font-size: 0.65rem;
        color: var(--muted);
        letter-spacing: 0.1em;
        padding-top: 1rem;
        border-top: 1px solid var(--border);
    }}
    </style>
    </head>
    <body>
    <div class="scanline"></div>
    <div class="container">

    <!-- HEADER -->
    <div class="header">
        <div class="header-left">
        <h1>⚖️ Stacy HR Division — Confidential</h1>
        <h2>@{username}</h2>
        </div>
        <div class="header-right">
        USER ID: {user_id}<br>
        GUILD: {guild_id}<br>
        GENERATED: {datetime.now().strftime("%Y-%m-%d %H:%M")}<br>
        STATUS: <span style="color:{status_color}">{status_role}</span>
        </div>
    </div>

    <!-- STAT CARDS -->
    <div class="stats">
        <div class="stat-card">
        <div class="label">Total Social Debt</div>
        <div class="value {'danger' if total_debt > 5 else ''}">{total_debt}</div>
        </div>
        <div class="stat-card">
        <div class="label">Total Infractions</div>
        <div class="value {'danger' if len(infractions) > 0 else ''}">{len(infractions)}</div>
        </div>
        <div class="stat-card">
        <div class="label">Current Status</div>
        <div class="value" style="font-size:1rem; padding-top:0.4rem;">
            <span class="badge" style="background:{status_color}; font-size:0.8rem">{status_role}</span>
        </div>
        </div>
    </div>

    <!-- CHART -->
    <div class="chart-section">
        <div class="section-title">📈 Social Debt Over Time</div>
        <div class="chart-wrapper">
        <canvas id="debtChart"></canvas>
        </div>
    </div>

    <!-- INFRACTION TABLE -->
    <div class="table-section">
        <div class="section-title">📋 Infraction History</div>
        <table>
        <thead>
            <tr>
            <th>Timestamp</th>
            <th>Severity</th>
            <th>Points</th>
            <th>Message</th>
            <th>Stacy's Inference</th>
            </tr>
        </thead>
        <tbody>
            {infraction_rows_html}
        </tbody>
        </table>
    </div>

    <div class="footer">
        STACY HR SYSTEM — THIS REPORT IS AUTOMATICALLY GENERATED — STACY'S WORD IS LAW
    </div>

    </div>

    <script>
    const ctx = document.getElementById('debtChart').getContext('2d');
    new Chart(ctx, {{
    type: 'line',
    data: {{
        labels: {chart_labels},
        datasets: [{{
        label: 'Social Debt',
        data: {chart_values},
        borderColor: '#e11d48',
        backgroundColor: 'rgba(225, 29, 72, 0.08)',
        borderWidth: 2,
        pointBackgroundColor: '#e11d48',
        pointRadius: 4,
        pointHoverRadius: 6,
        fill: true,
        tension: 0.3,
        }}]
    }},
    options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{
        legend: {{ display: false }},
        tooltip: {{
            backgroundColor: '#111827',
            borderColor: '#1e2d40',
            borderWidth: 1,
            titleColor: '#e2e8f0',
            bodyColor: '#94a3b8',
            titleFont: {{ family: 'Space Mono' }},
        }}
        }},
        scales: {{
        x: {{
            ticks: {{ color: '#64748b', font: {{ family: 'Space Mono', size: 10 }} }},
            grid: {{ color: '#1e2d40' }}
        }},
        y: {{
            beginAtZero: true,
            ticks: {{ color: '#64748b', font: {{ family: 'Space Mono', size: 10 }} }},
            grid: {{ color: '#1e2d40' }}
        }}
        }}
    }}
    }});
    </script>
    </body>
    </html>"""

    # Save to a temp file
    filename = os.path.join(reports_dir, f"hr_report_{user_id}_{guild_id}.html")

    with open(filename, "w", encoding="utf-8") as f:
        f.write(html)

    return filename
