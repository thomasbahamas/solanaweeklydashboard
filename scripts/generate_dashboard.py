"""Generate the Solana Floor dashboard as a static HTML page."""

import json
from pathlib import Path
from config import load_json, get_logger, now_utc, OUTPUT_DIR

log = get_logger("generate_dashboard")


def fmt_usd(value, decimals=2, compact=True):
    """Format USD values with appropriate suffixes."""
    if value is None or value == 0:
        return "$0"
    if compact:
        if abs(value) >= 1e12:
            return f"${value/1e12:.{decimals}f}T"
        elif abs(value) >= 1e9:
            return f"${value/1e9:.{decimals}f}B"
        elif abs(value) >= 1e6:
            return f"${value/1e6:.{decimals}f}M"
        elif abs(value) >= 1e3:
            return f"${value/1e3:.{decimals}f}K"
    return f"${value:,.{decimals}f}"


def fmt_change(value):
    """Format percentage change with arrow."""
    if value is None:
        return ""
    arrow = "▲" if value > 0 else "▼" if value < 0 else "–"
    color = "#22c55e" if value > 0 else "#ef4444" if value < 0 else "#888"
    return f'<span style="color:{color}">{arrow} {value:+.1f}%</span>'


def build_dashboard(compiled: dict, narrative: dict) -> str:
    """Build the full HTML dashboard."""
    market = compiled.get("market", {})
    solana = compiled.get("solana", {})
    news = compiled.get("news", {})
    whales = compiled.get("whales", {})

    fg = market.get("fear_greed", {})
    prices = market.get("prices", {})
    global_data = market.get("global", {})
    technicals = market.get("sol_technicals", {})
    dex = solana.get("dex_volumes", {})
    fees = solana.get("fees", {})
    network = solana.get("network", {})
    stables = solana.get("stablecoins", {})
    chain_tvls = solana.get("chain_tvls", [])
    protocols = solana.get("protocol_rankings", [])
    trending = market.get("trending", [])

    signal = narrative.get("the_signal", {})
    pitches = narrative.get("story_pitches", [])
    tweets = narrative.get("tweet_options", [])
    briefing = narrative.get("briefing_script", "")

    # Fear & Greed color
    fg_val = fg.get("value", 50)
    if fg_val <= 25:
        fg_color = "#ef4444"
    elif fg_val <= 45:
        fg_color = "#f97316"
    elif fg_val <= 55:
        fg_color = "#eab308"
    elif fg_val <= 75:
        fg_color = "#22c55e"
    else:
        fg_color = "#16a34a"

    # Build price cards
    price_cards = ""
    for ticker, data in prices.items():
        price_cards += f"""
        <div class="card price-card">
            <div class="ticker">{ticker}</div>
            <div class="price">${data['price']:,.2f}</div>
            <div class="change">{fmt_change(data['change_24h'])}</div>
        </div>"""

    # Build news list
    all_news = news.get("solana_news", []) + news.get("general_news", [])
    news_html = ""
    for story in all_news[:12]:
        cats = ", ".join(story.get("categories", ["General"]))
        news_html += f"""
        <div class="news-item">
            <span class="news-cat">{cats}</span>
            <a href="{story.get('url', '#')}" target="_blank">{story['title']}</a>
            <span class="news-source">{story['source']}</span>
        </div>"""

    # RSS headlines
    rss = news.get("rss_feeds", [])
    rss_html = ""
    for story in rss[:8]:
        rss_html += f"""
        <div class="news-item">
            <a href="{story.get('url', '#')}" target="_blank">{story['title']}</a>
            <span class="news-source">{story['source']}</span>
        </div>"""

    # Chain TVL table
    chain_rows = ""
    for c in chain_tvls[:10]:
        chain_rows += f"""
        <tr>
            <td>{c['name']}</td>
            <td>{fmt_usd(c['tvl'])}</td>
            <td>{fmt_change(c.get('change_1d'))}</td>
        </tr>"""

    # Top spot DEXes
    spot_rows = ""
    for d in dex.get("top_spot", [])[:15]:
        spot_rows += f"""
        <tr>
            <td>{d['name']}</td>
            <td>{fmt_usd(d['volume_24h'])}</td>
            <td>{fmt_change(d['change_1d'])}</td>
        </tr>"""

    # Top perp DEXes
    perp_rows = ""
    for d in dex.get("top_perps", [])[:7]:
        perp_rows += f"""
        <tr>
            <td>{d['name']}</td>
            <td>{fmt_usd(d['volume_24h'])}</td>
            <td>{fmt_change(d['change_1d'])}</td>
        </tr>"""

    # Protocol rankings
    protocol_rows = ""
    for i, p in enumerate(protocols[:20], 1):
        protocol_rows += f"""
        <tr>
            <td>{i}</td>
            <td>{p['name']}</td>
            <td>{p['category']}</td>
            <td>{fmt_usd(p['tvl'])}</td>
            <td>{fmt_change(p['change_1d'])}</td>
            <td>{fmt_change(p['change_7d'])}</td>
        </tr>"""

    # Divergence alerts
    divergences = signal.get("divergence_alerts", [])
    div_html = ""
    for d in divergences:
        sev_color = {"high": "#ef4444", "medium": "#f97316", "low": "#eab308"}.get(d.get("severity", "low"), "#888")
        div_html += f"""
        <div class="divergence-alert" style="border-left: 3px solid {sev_color}; padding-left: 12px; margin-bottom: 12px;">
            <strong>{d.get('title', '')}</strong> <span style="color:{sev_color}">({d.get('severity', 'low')})</span>
            <p>{d.get('description', '')}</p>
        </div>"""

    # Story pitches
    pitches_html = ""
    for p in pitches[:3]:
        pitches_html += f"""
        <div class="pitch-card">
            <div class="pitch-badge">{'● New' if p.get('is_new') else ''}</div>
            <h4>{p.get('title', '')}</h4>
            <p>{p.get('hook', '')}</p>
        </div>"""

    # Tweet options
    tweets_html = ""
    for t in tweets[:2]:
        tweets_html += f"""
        <div class="tweet-card">
            <div class="tweet-label">{t.get('label', '')}</div>
            <pre class="tweet-text">{t.get('text', '')}</pre>
        </div>"""

    # Whale intel
    whale_html = ""
    for w in whales.get("whale_news", [])[:6]:
        whale_html += f"""
        <div class="whale-item">
            <p>{w['title']}</p>
            <span class="news-source">{w['source']}</span>
        </div>"""

    staking = whales.get("staking_flows", {})
    staking_html = ""
    if staking:
        staking_html = f"<p><strong>Total Liquid Staking TVL:</strong> {fmt_usd(staking.get('total_staked_tvl', 0))}</p>"
        for p in staking.get("protocols", [])[:5]:
            staking_html += f"<div class='staking-row'>{p['name']}: {fmt_usd(p['tvl'])} {fmt_change(p['change_1d'])}</div>"

    # Trending
    trending_html = ""
    for t in trending[:10]:
        trending_html += f"<span class='trending-tag'>#{t.get('market_cap_rank', '?')} {t['symbol']}</span> "

    # Briefing script
    briefing_html = briefing.replace("\n", "<br>") if briefing else "<em>No briefing generated</em>"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Solana Floor Daily Dashboard</title>
<style>
:root {{
    --bg: #0a0a0f;
    --surface: #13131a;
    --border: #1e1e2e;
    --text: #e4e4e7;
    --muted: #71717a;
    --accent: #9333ea;
    --green: #22c55e;
    --red: #ef4444;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Inter', sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
    padding: 20px;
    max-width: 1400px;
    margin: 0 auto;
}}
h1 {{ font-size: 1.8rem; margin-bottom: 4px; }}
h2 {{
    font-size: 1.1rem;
    color: var(--muted);
    margin: 32px 0 16px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
    text-transform: uppercase;
    letter-spacing: 1px;
}}
h3 {{ font-size: 1rem; margin-bottom: 8px; }}
.header {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 24px;
}}
.header .timestamp {{ color: var(--muted); font-size: 0.85rem; }}
.fg-badge {{
    display: inline-block;
    padding: 6px 16px;
    border-radius: 6px;
    font-weight: 700;
    font-size: 1.2rem;
    color: white;
    background: {fg_color};
    margin-right: 8px;
}}
.fg-label {{ color: var(--muted); font-size: 0.85rem; }}
.grid {{ display: grid; gap: 12px; }}
.grid-6 {{ grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); }}
.grid-2 {{ grid-template-columns: 1fr 1fr; }}
.grid-3 {{ grid-template-columns: 1fr 1fr 1fr; }}
.card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px;
}}
.price-card .ticker {{ font-size: 0.75rem; color: var(--muted); text-transform: uppercase; }}
.price-card .price {{ font-size: 1.3rem; font-weight: 700; margin: 4px 0; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
th {{ text-align: left; color: var(--muted); padding: 8px; border-bottom: 1px solid var(--border); font-weight: 500; }}
td {{ padding: 8px; border-bottom: 1px solid var(--border); }}
.news-item {{
    padding: 8px 0;
    border-bottom: 1px solid var(--border);
    font-size: 0.9rem;
}}
.news-item a {{ color: var(--text); text-decoration: none; }}
.news-item a:hover {{ color: var(--accent); }}
.news-cat {{
    display: inline-block;
    background: var(--border);
    color: var(--muted);
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 0.7rem;
    margin-right: 6px;
}}
.news-source {{ color: var(--muted); font-size: 0.75rem; margin-left: 8px; }}
.divergence-alert p {{ color: var(--muted); font-size: 0.9rem; margin-top: 4px; }}
.pitch-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 12px;
}}
.pitch-badge {{ color: var(--green); font-size: 0.8rem; }}
.tweet-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 12px;
}}
.tweet-label {{ color: var(--accent); font-size: 0.8rem; font-weight: 600; margin-bottom: 8px; }}
.tweet-text {{
    white-space: pre-wrap;
    font-family: inherit;
    font-size: 0.9rem;
    color: var(--text);
    background: none;
    border: none;
}}
.whale-item {{ padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 0.9rem; }}
.staking-row {{ font-size: 0.85rem; padding: 4px 0; }}
.trending-tag {{
    display: inline-block;
    background: var(--border);
    padding: 4px 10px;
    border-radius: 4px;
    font-size: 0.8rem;
    margin: 2px;
}}
.briefing-box {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 24px;
    font-size: 0.9rem;
    line-height: 1.8;
    max-height: 600px;
    overflow-y: auto;
}}
.signal-context {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 20px;
    font-size: 0.9rem;
    line-height: 1.7;
}}
.stats-row {{
    display: flex;
    gap: 24px;
    flex-wrap: wrap;
    margin-bottom: 12px;
}}
.stat {{
    flex: 1;
    min-width: 150px;
}}
.stat-label {{ color: var(--muted); font-size: 0.75rem; text-transform: uppercase; }}
.stat-value {{ font-size: 1.3rem; font-weight: 700; }}
@media (max-width: 768px) {{
    .grid-2, .grid-3 {{ grid-template-columns: 1fr; }}
    .grid-6 {{ grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); }}
    .stats-row {{ flex-direction: column; gap: 12px; }}
}}
</style>
</head>
<body>

<div class="header">
    <div>
        <h1>SOLANA FLOOR</h1>
        <span class="timestamp">Updated: {compiled.get('generated_at', now_utc())}</span>
    </div>
    <div>
        <span class="fg-badge">{fg.get('value', 'N/A')}</span>
        <span class="fg-label">{fg.get('label', '')} (Yesterday: {fg.get('yesterday', 'N/A')})</span>
    </div>
</div>

<!-- MARKET OVERVIEW -->
<h2>Market Overview</h2>
<div class="stats-row">
    <div class="stat">
        <div class="stat-label">Total Market Cap</div>
        <div class="stat-value">{fmt_usd(global_data.get('total_market_cap', 0))} {fmt_change(global_data.get('market_cap_change_24h'))}</div>
    </div>
    <div class="stat">
        <div class="stat-label">BTC Dominance</div>
        <div class="stat-value">{global_data.get('btc_dominance', 0)}%</div>
    </div>
    <div class="stat">
        <div class="stat-label">SOL Dominance</div>
        <div class="stat-value">{global_data.get('sol_dominance', 0)}%</div>
    </div>
</div>
<div class="grid grid-6">
    {price_cards}
</div>

<!-- TECHNICAL ANALYSIS -->
<h2>Technical Analysis — SOL/USD</h2>
<div class="stats-row">
    <div class="stat"><div class="stat-label">Price</div><div class="stat-value">${technicals.get('price', 0):,.2f}</div></div>
    <div class="stat"><div class="stat-label">50-Day MA</div><div class="stat-value">${technicals.get('ma_50', 0):,.2f}</div></div>
    <div class="stat"><div class="stat-label">200-Day MA</div><div class="stat-value">${technicals.get('ma_200', 0):,.2f}</div></div>
    <div class="stat"><div class="stat-label">RSI (14)</div><div class="stat-value">{technicals.get('rsi_14', 'N/A')}</div></div>
</div>
<p style="color:var(--muted)">MA Signal: {technicals.get('ma_signal', 'N/A')}</p>

<!-- TOP NEWS -->
<h2>Top News</h2>
{news_html}
{f'<h3 style="margin-top:16px;">RSS Headlines</h3>' + rss_html if rss_html else ''}

<!-- ECOSYSTEM OVERVIEW -->
<h2>Ecosystem Overview — Top Chains by TVL</h2>
<table>
    <tr><th>Chain</th><th>TVL</th><th>24h</th></tr>
    {chain_rows}
</table>

<!-- SOLANA DEEP DIVE -->
<h2>Solana Deep Dive</h2>
<div class="stats-row">
    <div class="stat"><div class="stat-label">Solana TVL</div><div class="stat-value">{fmt_usd(solana.get('solana_tvl', {}).get('current', 0))}</div></div>
    <div class="stat"><div class="stat-label">Spot DEX 24h</div><div class="stat-value">{fmt_usd(dex.get('spot_24h', 0))}</div></div>
    <div class="stat"><div class="stat-label">Perp DEX 24h</div><div class="stat-value">{fmt_usd(dex.get('perp_24h', 0))}</div></div>
    <div class="stat"><div class="stat-label">Fees 24h</div><div class="stat-value">{fmt_usd(fees.get('total_24h', 0))}</div></div>
    <div class="stat"><div class="stat-label">Stablecoins</div><div class="stat-value">{fmt_usd(stables.get('total', 0))}</div></div>
</div>
<div class="stats-row">
    <div class="stat"><div class="stat-label">TPS (Total)</div><div class="stat-value">{network.get('tps_total', 'N/A')}</div></div>
    <div class="stat"><div class="stat-label">TPS (Non-Vote)</div><div class="stat-value">{network.get('tps_non_vote', 'N/A')}</div></div>
    <div class="stat"><div class="stat-label">Epoch</div><div class="stat-value">{network.get('epoch', 'N/A')}</div></div>
</div>

<div class="grid grid-2">
    <div>
        <h3>Top Spot DEXes</h3>
        <table><tr><th>Protocol</th><th>Volume 24h</th><th>Chg 1d</th></tr>{spot_rows}</table>
    </div>
    <div>
        <h3>Top Perp DEXes</h3>
        <table><tr><th>Protocol</th><th>Volume 24h</th><th>Chg 1d</th></tr>{perp_rows}</table>
    </div>
</div>

<!-- PROTOCOL RANKINGS -->
<h2>Solana Protocol Rankings — Top 20 by TVL</h2>
<table>
    <tr><th>#</th><th>Protocol</th><th>Category</th><th>TVL</th><th>24h</th><th>7d</th></tr>
    {protocol_rows}
</table>

<!-- WHALE INTELLIGENCE -->
<h2>Whale Intelligence</h2>
{whale_html or '<p style="color:var(--muted)">No whale events detected today.</p>'}
{staking_html}

<!-- TRENDING -->
<h2>Trending on CoinGecko</h2>
<div>{trending_html}</div>

<!-- THE SIGNAL -->
<h2>The Signal — AI Analysis</h2>
<div class="signal-context">
    <h3>Market Context</h3>
    <p>{signal.get('market_context', 'No analysis generated.')}</p>
</div>
<h3 style="margin-top:16px;">Divergence Alerts</h3>
{div_html or '<p style="color:var(--muted)">No divergences detected.</p>'}

<!-- STORY PITCHES -->
<h2>Story Pitches</h2>
<div class="grid grid-3">
    {pitches_html}
</div>

<!-- TWEET OPTIONS -->
<h2>Tweet Options — @thomasbahamas</h2>
{tweets_html}

<!-- BRIEFING SCRIPT -->
<h2>Briefing Script</h2>
<div class="briefing-box">
    {briefing_html}
</div>

<footer style="text-align:center; color:var(--muted); padding:32px 0; font-size:0.8rem;">
    Solana Floor Daily Intelligence · Built for @thomasbahamas · {compiled.get('generated_at', '')}
</footer>

</body>
</html>"""

    return html


def run() -> str:
    compiled = load_json("compiled.json")
    narrative = load_json("narrative.json")

    if not compiled:
        log.error("No compiled data found")
        return ""

    html = build_dashboard(compiled, narrative)

    output_path = OUTPUT_DIR / "index.html"
    with open(output_path, "w") as f:
        f.write(html)

    log.info(f"Dashboard written to {output_path}")
    return str(output_path)


if __name__ == "__main__":
    run()
