"""Generate the Solana Weekly site — homepage + dashboard.

Homepage (output/index.html):
  Summary/highlights with newsletter signup, links to full dashboard.

Dashboard (output/dashboard/index.html):
  Tabbed interface with sections:
    Overview  — Market, Technical, News
    Deep Dive — Ecosystem, Solana, DEX Volume, Protocols
    Intelligence — The Signal, Whales, Market Makers, X Pulse
"""

import json
import html as html_mod
from pathlib import Path
from config import load_json, get_logger, now_utc, OUTPUT_DIR

log = get_logger("generate_dashboard")


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def esc(text):
    """HTML-escape user-supplied text."""
    if text is None:
        return ""
    return html_mod.escape(str(text))


def fmt_usd(value, decimals=2, compact=True):
    if value is None or value == 0:
        return "$0"
    if compact:
        if abs(value) >= 1e12:
            return f"${value/1e12:.{decimals}f}T"
        if abs(value) >= 1e9:
            return f"${value/1e9:.{decimals}f}B"
        if abs(value) >= 1e6:
            return f"${value/1e6:.{decimals}f}M"
        if abs(value) >= 1e3:
            return f"${value/1e3:.{decimals}f}K"
    return f"${value:,.{decimals}f}"


def fmt_change(value):
    if value is None:
        return ""
    arrow = "&#9650;" if value > 0 else "&#9660;" if value < 0 else "&#8211;"
    color = "var(--green)" if value > 0 else "var(--red)" if value < 0 else "var(--muted)"
    return f'<span style="color:{color}">{arrow} {value:+.1f}%</span>'


def fmt_wow(wow: dict, key: str) -> str:
    if not wow or key not in wow:
        return '<span class="wow">WoW: collecting</span>'
    val = wow[key]
    color = "var(--green)" if val > 0 else "var(--red)" if val < 0 else "var(--muted)"
    return f'<span class="wow" style="color:{color}">WoW: {val:+.1f}%</span>'


def sentiment_dot(sentiment: str) -> str:
    s = (sentiment or "").lower()
    if "extremely" in s and "bullish" in s:
        return '<span class="dot dot-green-bright"></span>'
    if "bullish" in s:
        return '<span class="dot dot-green"></span>'
    if "bearish" in s:
        return '<span class="dot dot-red"></span>'
    return '<span class="dot dot-gray"></span>'


# ---------------------------------------------------------------------------
# SVG chart helpers
# ---------------------------------------------------------------------------

def svg_gauge(value, max_val=100, size=90):
    """Semi-circular F&G gauge."""
    if not isinstance(value, (int, float)):
        value = 50
    pct = max(0, min(1, value / max_val))
    # Arc from -180 to 0 degrees
    angle = -180 + pct * 180
    rad = 3.14159265 * angle / 180
    import math
    r = 36
    cx, cy = size // 2, size // 2 + 5
    ex = cx + r * math.cos(rad)
    ey = cy + r * math.sin(rad)
    large = 1 if pct > 0.5 else 0

    # Color gradient: red -> orange -> yellow -> green
    if value <= 25:
        color = "#ef4444"
    elif value <= 45:
        color = "#f97316"
    elif value <= 55:
        color = "#eab308"
    elif value <= 75:
        color = "#22c55e"
    else:
        color = "#16a34a"

    return f'''<svg width="{size}" height="{size//2 + 20}" viewBox="0 0 {size} {size//2 + 20}">
  <path d="M {cx - r} {cy} A {r} {r} 0 0 1 {cx + r} {cy}" fill="none" stroke="#1e1e2e" stroke-width="8" stroke-linecap="round"/>
  <path d="M {cx - r} {cy} A {r} {r} 0 {large} 1 {ex:.1f} {ey:.1f}" fill="none" stroke="{color}" stroke-width="8" stroke-linecap="round"/>
  <text x="{cx}" y="{cy - 8}" text-anchor="middle" fill="{color}" font-size="20" font-weight="700">{value}</text>
</svg>'''


def svg_bar_chart_horiz(items, label_key, value_key, width=460, bar_h=22, fmt_fn=None, max_bars=10, accent="#9333ea"):
    """Horizontal bar chart. items = list of dicts."""
    items = items[:max_bars]
    if not items:
        return ""
    values = [abs(float(it.get(value_key, 0) or 0)) for it in items]
    max_v = max(values) if values else 1
    if max_v == 0:
        max_v = 1
    label_w = 100
    chart_w = width - label_w - 70
    h = len(items) * (bar_h + 4) + 4
    bars = []
    for i, it in enumerate(items):
        v = abs(float(it.get(value_key, 0) or 0))
        bw = max(2, (v / max_v) * chart_w)
        y = i * (bar_h + 4) + 2
        label = esc(str(it.get(label_key, "")))[:16]
        val_str = fmt_fn(v) if fmt_fn else f"{v:,.0f}"
        bars.append(
            f'<text x="{label_w - 4}" y="{y + bar_h // 2 + 4}" text-anchor="end" fill="#a1a1aa" font-size="11" font-family="inherit">{label}</text>'
            f'<rect x="{label_w}" y="{y}" width="{bw:.0f}" height="{bar_h}" fill="{accent}" rx="3" opacity="0.85"/>'
            f'<text x="{label_w + bw + 4}" y="{y + bar_h // 2 + 4}" fill="#a1a1aa" font-size="10" font-family="inherit">{val_str}</text>'
        )
    return f'<svg width="100%" viewBox="0 0 {width} {h}" xmlns="http://www.w3.org/2000/svg">{"".join(bars)}</svg>'


def svg_monthly_returns(monthly_returns):
    """Compact monthly return heatmap-style bar chart."""
    if not monthly_returns:
        return '<p class="muted">Collecting monthly return data...</p>'
    items = monthly_returns[-12:]  # last 12 months
    w = 460
    bar_w = 32
    gap = 4
    h = 80
    mid_y = 50
    max_abs = max((abs(m.get("return_pct", 0)) for m in items), default=1) or 1
    scale = 30 / max_abs
    bars = []
    for i, m in enumerate(items):
        x = i * (bar_w + gap) + 30
        ret = m.get("return_pct", 0)
        bh = abs(ret) * scale
        color = "#22c55e" if ret >= 0 else "#ef4444"
        y = mid_y - bh if ret >= 0 else mid_y
        label = m.get("month", "")[-5:]  # "MM" or "YYYY-MM"
        if len(label) > 3:
            label = label[-2:]  # just month number
        bars.append(
            f'<rect x="{x}" y="{y:.0f}" width="{bar_w}" height="{max(2, bh):.0f}" fill="{color}" rx="2" opacity="0.8"/>'
            f'<text x="{x + bar_w // 2}" y="{mid_y + 16}" text-anchor="middle" fill="#71717a" font-size="9">{label}</text>'
            f'<text x="{x + bar_w // 2}" y="{y - 3 if ret >= 0 else y + bh + 11:.0f}" text-anchor="middle" fill="{color}" font-size="8">{ret:+.0f}%</text>'
        )
    total_w = len(items) * (bar_w + gap) + 60
    return f'<svg width="100%" viewBox="0 0 {total_w} {h + 10}" xmlns="http://www.w3.org/2000/svg"><line x1="30" y1="{mid_y}" x2="{total_w - 10}" y2="{mid_y}" stroke="#1e1e2e" stroke-width="1"/>{"".join(bars)}</svg>'


def svg_range_bar(current, low, high, width=200, height=24):
    """52-week range indicator."""
    if not all(isinstance(v, (int, float)) for v in [current, low, high]) or high <= low:
        return ""
    pct = (current - low) / (high - low)
    pct = max(0, min(1, pct))
    bar_x = 0
    bar_w = width
    dot_x = bar_x + pct * bar_w
    return f'''<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect x="{bar_x}" y="8" width="{bar_w}" height="6" fill="#1e1e2e" rx="3"/>
  <rect x="{bar_x}" y="8" width="{dot_x:.0f}" height="6" fill="#9333ea" rx="3"/>
  <circle cx="{dot_x:.0f}" cy="11" r="6" fill="#9333ea"/>
  <text x="0" y="22" fill="#71717a" font-size="9">${low:,.0f}</text>
  <text x="{width}" y="22" text-anchor="end" fill="#71717a" font-size="9">${high:,.0f}</text>
</svg>'''


# ---------------------------------------------------------------------------
# Panel builders
# ---------------------------------------------------------------------------

def build_market_panel(prices, global_data, fg, wow):
    price_cards = ""
    order = ["BTC", "ETH", "SOL", "HYPE", "ZEC", "NEAR"]
    for ticker in order:
        if ticker not in prices:
            continue
        d = prices[ticker]
        price_cards += f'''<div class="card price-card">
  <div class="price-name">{esc(ticker)} <span class="muted">{esc(d.get("name",""))}</span></div>
  <div class="price-val">${d["price"]:,.2f}</div>
  <div>{fmt_change(d.get("change_24h"))}</div>
</div>'''

    fg_val = fg.get("value", "N/A")
    fg_label = fg.get("label", "")
    gauge = svg_gauge(fg_val if isinstance(fg_val, (int, float)) else 50)

    return f'''<div class="panel-section">
  <div class="stats-row">
    <div class="stat"><div class="stat-label">Total Market Cap</div>
      <div class="stat-value">{fmt_usd(global_data.get("total_market_cap",0))}</div>
      <div>{fmt_change(global_data.get("market_cap_change_24h"))} 24h</div>
      <div>{fmt_wow(wow, "total_market_cap")}</div>
    </div>
    <div class="stat"><div class="stat-label">BTC Dominance</div>
      <div class="stat-value">{global_data.get("btc_dominance",0)}%</div>
    </div>
    <div class="stat"><div class="stat-label">SOL Dominance</div>
      <div class="stat-value">{global_data.get("sol_dominance",0)}%</div>
      <div>{fmt_wow(wow, "sol_dominance")}</div>
    </div>
  </div>
  <div class="grid grid-prices">{price_cards}</div>
  <div class="fg-section">
    <div class="fg-gauge">{gauge}</div>
    <div class="fg-info">
      <div class="fg-label-text">{esc(str(fg_label))}</div>
      <div class="muted">Yesterday: {fg.get("yesterday","N/A")}</div>
    </div>
  </div>
</div>'''


def build_technical_panel(technicals, monthly_returns):
    price = technicals.get("price") or 0
    ma50 = technicals.get("ma_50") or 0
    ma200 = technicals.get("ma_200") or 0
    rsi = technicals.get("rsi_14") or "N/A"
    signal = technicals.get("ma_signal") or "N/A"
    h52 = technicals.get("high_52w")
    l52 = technicals.get("low_52w")
    range_svg = svg_range_bar(price, l52, h52, width=280) if all(isinstance(v, (int, float)) for v in [price, l52, h52]) else ""
    monthly_svg = svg_monthly_returns(monthly_returns)

    return f'''<div class="panel-section">
  <h3>SOL/USD</h3>
  <div class="stats-row">
    <div class="stat"><div class="stat-label">Price</div><div class="stat-value">${price:,.2f}</div></div>
    <div class="stat"><div class="stat-label">50-Day MA</div><div class="stat-value">${ma50:,.2f}</div></div>
    <div class="stat"><div class="stat-label">200-Day MA</div><div class="stat-value">${ma200:,.2f}</div></div>
    <div class="stat"><div class="stat-label">RSI (14)</div><div class="stat-value">{rsi}</div></div>
  </div>
  <div class="tech-row">
    <div><span class="stat-label">MA Signal</span><br><strong>{esc(str(signal))}</strong></div>
    <div><span class="stat-label">52W Range</span><br>{range_svg if range_svg else f"${l52 or 0:,.0f} &mdash; ${h52 or 0:,.0f}"}</div>
  </div>
  <h4 style="margin-top:16px">Monthly Returns (%)</h4>
  {monthly_svg}
</div>'''


def build_news_panel(news):
    all_news = news.get("solana_news", []) + news.get("general_news", [])
    rss = news.get("rss_feeds", [])
    total = news.get("total_stories", len(all_news) + len(rss))
    items_html = ""
    for s in all_news[:12]:
        cats = ", ".join(s.get("categories", ["General"]))
        items_html += f'''<div class="news-item">
  <a href="{esc(s.get("url","#"))}" target="_blank" rel="noopener">{esc(s["title"])}</a>
  <span class="news-source">{esc(s.get("source",""))}</span>
  <span class="news-cat">{esc(cats)}</span>
</div>'''
    for s in rss[:6]:
        items_html += f'''<div class="news-item">
  <a href="{esc(s.get("url","#"))}" target="_blank" rel="noopener">{esc(s["title"])}</a>
  <span class="news-source">{esc(s.get("source",""))}</span>
</div>'''
    return f'''<div class="panel-section">
  <div class="section-badge">{total} stories</div>
  {items_html}
</div>'''


def build_ecosystem_panel(chain_tvls, trending):
    rows = ""
    for c in chain_tvls[:10]:
        rows += f'<tr><td>{esc(c["name"])}</td><td>{fmt_usd(c["tvl"])}</td></tr>'
    chart = svg_bar_chart_horiz(chain_tvls[:10], "name", "tvl", fmt_fn=fmt_usd, accent="#9333ea")

    trending_html = ""
    for t in trending[:10]:
        rank = t.get("market_cap_rank", "?")
        trending_html += f'<span class="trending-tag">#{rank} <strong>{esc(t.get("symbol","?"))}</strong></span>'

    return f'''<div class="panel-section">
  <div class="grid grid-2">
    <div>
      <h4>Top 10 Chains by TVL</h4>
      <table><tr><th>Chain</th><th>TVL</th></tr>{rows}</table>
    </div>
    <div>
      <h4>Top 10 Chain TVL</h4>
      {chart}
    </div>
  </div>
  <h4 style="margin-top:20px">Trending on CoinGecko</h4>
  <div class="trending-row">{trending_html}</div>
</div>'''


def build_solana_panel(solana, wow):
    tvl = solana.get("solana_tvl", {})
    dex = solana.get("dex_volumes", {})
    fees = solana.get("fees", {})
    network = solana.get("network", {})
    stables = solana.get("stablecoins", {})
    daily_tx = network.get("daily_transactions_est")
    daily_tx_str = f"{daily_tx/1e6:.0f}M" if daily_tx else "N/A"
    tps_total = network.get("tps_total", "N/A")
    tps_nv = network.get("tps_non_vote", "N/A")
    vote_pct = network.get("vote_pct", 0)

    return f'''<div class="panel-section">
  <h4>Primary Ecosystem</h4>
  <div class="stats-row">
    <div class="stat"><div class="stat-label">Solana TVL</div>
      <div class="stat-value">{fmt_usd(tvl.get("current",0))}</div>
      <div>{fmt_change(tvl.get("change_1d"))} 24h</div>
      <div>{fmt_wow(wow, "sol_tvl")}</div>
    </div>
    <div class="stat"><div class="stat-label">Spot DEX Volume</div>
      <div class="stat-value">{fmt_usd(dex.get("spot_24h",0))}</div>
      <div>{fmt_change(dex.get("spot_change_1d"))} 24h</div>
    </div>
    <div class="stat"><div class="stat-label">Perp DEX Volume</div>
      <div class="stat-value">{fmt_usd(dex.get("perp_24h",0))}</div>
      <div>{fmt_wow(wow, "dex_volume")}</div>
    </div>
    <div class="stat"><div class="stat-label">Fees 24h</div>
      <div class="stat-value">{fmt_usd(fees.get("total_24h",0))}</div>
      <div>{fmt_change(fees.get("change_1d"))}</div>
    </div>
  </div>
  <div class="stats-row">
    <div class="stat"><div class="stat-label">Stablecoins on Solana</div><div class="stat-value">{fmt_usd(stables.get("total",0))}</div></div>
  </div>

  <h4 style="margin-top:20px">Network Activity</h4>
  <div class="stats-row">
    <div class="stat"><div class="stat-label">Daily Transactions</div>
      <div class="stat-value">{daily_tx_str}</div>
      <div class="muted">Estimated from TPS</div>
    </div>
    <div class="stat"><div class="stat-label">TPS (Total)</div>
      <div class="stat-value">{tps_total}</div>
      <div class="muted">Epoch {network.get("epoch","N/A")}</div>
      <div>{fmt_wow(wow, "tps")}</div>
    </div>
    <div class="stat"><div class="stat-label">TPS (Non-Vote)</div>
      <div class="stat-value">{tps_nv}</div>
      <div class="muted">{vote_pct}% vote</div>
    </div>
  </div>
</div>'''


def build_dex_panel(dex):
    spot_rows = ""
    for d in dex.get("top_spot", [])[:15]:
        spot_rows += f'<tr><td>{esc(d["name"])}</td><td>{fmt_usd(d["volume_24h"])}</td><td>{fmt_change(d.get("change_1d"))}</td></tr>'
    perp_rows = ""
    for d in dex.get("top_perps", [])[:10]:
        perp_rows += f'<tr><td>{esc(d["name"])}</td><td>{fmt_usd(d["volume_24h"])}</td><td>{fmt_change(d.get("change_1d"))}</td></tr>'

    spot_cov = dex.get("spot_coverage_pct", 0)
    perp_cov = dex.get("perp_coverage_pct", 0)

    return f'''<div class="panel-section">
  <h4>Spot + Perps</h4>
  <div class="stats-row">
    <div class="stat"><div class="stat-label">Spot DEX 24h</div><div class="stat-value">{fmt_usd(dex.get("spot_24h",0))}</div><div>{fmt_change(dex.get("spot_change_1d"))} 1d</div></div>
    <div class="stat"><div class="stat-label">Perp DEX 24h</div><div class="stat-value">{fmt_usd(dex.get("perp_24h",0))}</div></div>
    <div class="stat"><div class="stat-label">Combined 24h</div><div class="stat-value">{fmt_usd(dex.get("combined_24h",0))}</div></div>
  </div>
  <div class="grid grid-2">
    <div>
      <h4>Top Spot DEXes{f" ({spot_cov}% of total)" if spot_cov else ""}</h4>
      <table><tr><th>Protocol</th><th>Volume 24h</th><th>Chg 1d</th></tr>{spot_rows}</table>
    </div>
    <div>
      <h4>Top Perp DEXes{f" ({perp_cov}% of total)" if perp_cov else ""}</h4>
      <table><tr><th>Protocol</th><th>Volume 24h</th><th>Chg 1d</th></tr>{perp_rows}</table>
    </div>
  </div>
</div>'''


def build_protocols_panel(protocols):
    rows = ""
    for i, p in enumerate(protocols[:20], 1):
        rows += f'''<tr>
  <td>{i}</td><td>{esc(p["name"])}</td><td>{esc(p.get("category",""))}</td>
  <td>{fmt_usd(p["tvl"])}</td><td>{fmt_change(p.get("change_1d"))}</td><td>{fmt_change(p.get("change_7d"))}</td>
</tr>'''
    chart = svg_bar_chart_horiz(protocols[:10], "name", "tvl", fmt_fn=fmt_usd, accent="#7c3aed")

    return f'''<div class="panel-section">
  <h4>Top 20 by TVL</h4>
  <table class="full-table">
    <tr><th>#</th><th>Protocol</th><th>Category</th><th>TVL</th><th>24h</th><th>7d</th></tr>
    {rows}
  </table>
  <h4 style="margin-top:16px">Protocol TVL</h4>
  {chart}
</div>'''


def build_whale_panel(whales):
    whale_news = whales.get("whale_news", [])
    staking = whales.get("staking_flows", {})
    count = len(whale_news)

    events_html = ""
    for w in whale_news[:8]:
        events_html += f'''<div class="whale-event">
  <div class="whale-title">{esc(w.get("title",""))}</div>
  <div class="whale-meta">
    <span class="muted">{esc(w.get("source",""))}</span>
    <span class="muted">{esc(w.get("published","")[:10])}</span>
  </div>
</div>'''

    staking_html = ""
    if staking:
        staking_html = f'<h4 style="margin-top:16px">Staking Flows</h4>'
        staking_html += f'<p>Total Liquid Staking TVL: <strong>{fmt_usd(staking.get("total_staked_tvl",0))}</strong></p>'
        for p in staking.get("protocols", [])[:7]:
            staking_html += f'<div class="staking-row">{esc(p["name"])}: {fmt_usd(p["tvl"])} {fmt_change(p.get("change_1d"))}</div>'

    return f'''<div class="panel-section">
  <div class="section-badge">{count} events</div>
  {events_html if events_html else '<p class="muted">No whale events detected today.</p>'}
  {staking_html}
</div>'''


def build_market_makers_panel(narrative):
    mm = narrative.get("market_maker_activity", {})
    signals = mm.get("signals", [])
    if not signals:
        return '<div class="panel-section"><p class="muted">No market maker signals detected.</p></div>'

    signals_html = '<h4>Recent Signals</h4>'
    for s in signals:
        signals_html += f'''<div class="mm-signal">
  {sentiment_dot(s.get("sentiment",""))}
  <div class="mm-body">
    <strong>{esc(s.get("firm",""))}</strong> &mdash; {esc(s.get("signal",""))}
    <div class="muted">{esc(s.get("detail",""))}</div>
    <div class="mm-sentiment">{esc(s.get("sentiment",""))}</div>
  </div>
</div>'''

    return f'<div class="panel-section">{signals_html}</div>'


def build_xpulse_panel(narrative):
    xp = narrative.get("x_pulse", {})
    proto = xp.get("protocol_updates", [])
    influ = xp.get("influencer_takes", [])
    narratives = xp.get("trending_narratives", [])

    proto_html = ""
    for p in proto[:6]:
        proto_html += f'''<div class="xp-item">
  <span class="xp-handle">{esc(p.get("account",""))}</span>
  <span>{esc(p.get("text",""))}</span>
</div>'''

    influ_html = ""
    for p in influ[:6]:
        influ_html += f'''<div class="xp-item">
  <span class="xp-handle">{esc(p.get("account",""))}</span>
  <span>{esc(p.get("text",""))}</span>
</div>'''

    narr_html = ""
    for n in narratives[:6]:
        narr_html += f'''<div class="narr-item">
  <span class="narr-arrow">&#9656;</span>
  <strong>{esc(n.get("title",""))}</strong> &mdash; {esc(n.get("detail",""))}
</div>'''

    return f'''<div class="panel-section">
  <h4>Protocol Updates</h4>
  {proto_html if proto_html else '<p class="muted">No protocol updates available.</p>'}
  <h4 style="margin-top:16px">Influencer Takes</h4>
  {influ_html if influ_html else '<p class="muted">No influencer takes available.</p>'}
  <h4 style="margin-top:16px">Trending Narratives</h4>
  {narr_html if narr_html else '<p class="muted">No trending narratives available.</p>'}
</div>'''


def build_upgrades_panel(upgrades):
    if not upgrades:
        return '<div class="panel-section"><p class="muted">No upgrade data available.</p></div>'

    # --- Client adoption bar chart ---
    adoption = upgrades.get("validator_adoption", {})
    clients = adoption.get("clients", [])
    total_validators = adoption.get("total_validators", 0)
    total_stake_sol = adoption.get("total_stake_sol", 0)

    adoption_html = ""
    if clients:
        # Color map for clients
        colors = {
            "Agave": "#9333ea",
            "Jito-Solana": "#22c55e",
            "Firedancer": "#f97316",
            "Frankendancer": "#eab308",
        }
        bars_html = ""
        for c in clients:
            pct = c["stake_pct"]
            color = colors.get(c["name"], "#71717a")
            count = c["validator_count"]
            stake_m = c.get("stake_sol", 0)
            stake_str = f"{stake_m/1e6:.0f}M SOL" if stake_m >= 1e6 else f"{stake_m:,.0f} SOL"
            bar_w = max(2, pct)  # min width so small bars are visible
            bars_html += f'''<div style="margin-bottom:10px">
  <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:3px">
    <span style="font-weight:600;font-size:0.88rem">{esc(c["name"])}</span>
    <span style="font-size:0.82rem"><strong>{pct}%</strong> <span class="muted">&middot; {count} validators &middot; {stake_str}</span></span>
  </div>
  <div style="background:var(--surface2);border-radius:4px;height:8px;overflow:hidden">
    <div style="background:{color};height:100%;width:{bar_w}%;border-radius:4px;transition:width 0.3s"></div>
  </div>
</div>'''

        # Stacked bar summary
        stacked = ""
        for c in clients:
            pct = c["stake_pct"]
            if pct < 0.5:
                continue
            color = colors.get(c["name"], "#71717a")
            stacked += f'<div style="width:{pct}%;background:{color};height:100%" title="{esc(c["name"])}: {pct}%"></div>'

        adoption_html = f'''<div style="margin-bottom:24px">
  <h4>Validator Client Adoption (Stake-Weighted)</h4>
  <div class="stats-row" style="margin-bottom:16px">
    <div class="stat"><div class="stat-label">Total Validators</div><div class="stat-value">{total_validators:,}</div></div>
    <div class="stat"><div class="stat-label">Total Stake</div><div class="stat-value">{total_stake_sol/1e6:.0f}M SOL</div></div>
  </div>
  <div style="display:flex;height:12px;border-radius:6px;overflow:hidden;margin-bottom:16px;background:var(--surface2)">{stacked}</div>
  {bars_html}
</div>'''

    # Top versions
    top_versions = adoption.get("top_versions", [])
    versions_html = ""
    if top_versions:
        rows = ""
        for v in top_versions[:8]:
            rows += f'<tr><td><code style="font-size:0.78rem">{esc(v["version"])}</code></td><td>{esc(v["client"])}</td><td>{v["stake_pct"]}%</td></tr>'
        versions_html = f'''<h4 style="margin-top:20px">Top Versions by Stake</h4>
<table><tr><th>Version</th><th>Client</th><th>Stake %</th></tr>{rows}</table>'''

    # --- Infrastructure cards ---
    infra = upgrades.get("infrastructure", {})
    # Only show non-client infra cards (DoubleZero, Alpenglow, Harmonic)
    infra_cards = ""
    for key in ["doublezero", "alpenglow", "harmonic"]:
        item = infra.get(key)
        if not item:
            continue
        metric_html = ""
        if item.get("metric_value"):
            metric_html = f'<div class="stat-value" style="font-size:1.1rem">{esc(str(item["metric_value"]))}</div>'
            if item.get("metric_detail"):
                metric_html += f'<div class="muted">{esc(item["metric_detail"])}</div>'

        status = item.get("status", "Unknown")
        status_color = "var(--green)" if "live" in status.lower() or "active" in status.lower() else "#eab308" if "development" in status.lower() else "var(--muted)"

        url_attr = f' href="{esc(item["url"])}" target="_blank" rel="noopener"' if item.get("url") else ""
        name_tag = f"<a{url_attr} style='color:var(--text);text-decoration:none'>{esc(item['name'])}</a>" if url_attr else esc(item["name"])

        infra_cards += f'''<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:start">
    <div>
      <div style="font-weight:600;font-size:0.9rem">{name_tag}</div>
      <div class="muted" style="font-size:0.78rem;margin-top:2px">{esc(item["description"])}</div>
    </div>
    <span style="color:{status_color};font-size:0.7rem;font-weight:600;text-transform:uppercase;white-space:nowrap;margin-left:12px">{esc(status)}</span>
  </div>
  {f'<div style="margin-top:8px">{metric_html}</div>' if metric_html else ''}
</div>'''

    # --- SIMDs ---
    simds = upgrades.get("simds", {})
    stats = simds.get("stats", {})
    recent = simds.get("recent", [])

    simd_stats = ""
    if stats:
        simd_stats = f'''<div class="stats-row" style="margin-bottom:12px">
  <div class="stat"><div class="stat-label">Open</div><div class="stat-value">{stats.get("open", 0)}</div></div>
  <div class="stat"><div class="stat-label">Merged</div><div class="stat-value" style="color:var(--green)">{stats.get("merged", 0)}</div></div>
  <div class="stat"><div class="stat-label">Closed</div><div class="stat-value">{stats.get("closed", 0)}</div></div>
</div>'''

    simd_rows = ""
    for s in recent[:10]:
        state = s.get("state", "")
        if state == "merged":
            state_badge = '<span style="color:var(--green);font-size:0.7rem;font-weight:600">MERGED</span>'
        elif state == "open":
            state_badge = '<span style="color:#eab308;font-size:0.7rem;font-weight:600">OPEN</span>'
        else:
            state_badge = '<span style="color:var(--muted);font-size:0.7rem;font-weight:600">CLOSED</span>'

        labels_html = ""
        for lb in s.get("labels", [])[:3]:
            labels_html += f'<span style="background:var(--surface2);color:var(--accent);padding:1px 6px;border-radius:3px;font-size:0.6rem;margin-left:4px">{esc(lb)}</span>'

        simd_rows += f'''<div style="padding:6px 0;border-bottom:1px solid var(--border);font-size:0.84rem">
  <div style="display:flex;justify-content:space-between;align-items:center">
    <a href="{esc(s.get("url","#"))}" target="_blank" rel="noopener" style="color:var(--text);text-decoration:none">{esc(s["title"][:80])}</a>
    {state_badge}
  </div>
  <div class="muted" style="font-size:0.7rem">{esc(s.get("author",""))} &middot; {esc(s.get("updated",""))}{labels_html}</div>
</div>'''

    # --- Upgrade news ---
    upgrade_news = upgrades.get("upgrade_news", [])
    news_html = ""
    for n in upgrade_news[:6]:
        news_html += f'''<div class="news-item">
  <a href="{esc(n.get("url","#"))}" target="_blank" rel="noopener">{esc(n["title"])}</a>
  <span class="news-source">{esc(n.get("source",""))}</span>
</div>'''

    return f'''<div class="panel-section">
  {adoption_html}
  {versions_html}
  {f'<h4 style="margin-top:24px">Infrastructure &amp; Upcoming Upgrades</h4><div class="grid grid-3" style="margin-bottom:20px">{infra_cards}</div>' if infra_cards else ''}
  <h4 style="margin-top:24px">SIMDs — Solana Improvement Documents</h4>
  {simd_stats}
  {simd_rows if simd_rows else '<p class="muted">No SIMD data available.</p>'}
  {f'<h4 style="margin-top:20px">Upgrade News</h4>{news_html}' if news_html else ''}
</div>'''


def build_signal_panel(signal):
    context = signal.get("market_context", "No analysis generated.")
    divergences = signal.get("divergence_alerts", [])
    angles = signal.get("story_angles", [])
    key_rel = signal.get("key_data_relationships", "")

    div_html = ""
    for d in divergences:
        sev = d.get("severity", "low")
        sev_color = {"high": "var(--red)", "medium": "#f97316", "low": "#eab308"}.get(sev, "var(--muted)")
        div_html += f'''<div class="div-alert" style="border-left:3px solid {sev_color}">
  <div><strong>{esc(d.get("title",""))}</strong> <span class="div-sev" style="color:{sev_color}">{sev}</span></div>
  <div class="muted">{esc(d.get("description",""))}</div>
</div>'''

    angles_html = ""
    for a in angles:
        angles_html += f'<div class="narr-item"><span class="narr-arrow">&#9656;</span> {esc(a)}</div>'

    return f'''<div class="panel-section">
  <h4>Market Context</h4>
  <div class="signal-context">{esc(context)}</div>
  <h4 style="margin-top:16px">&#9888; Divergence Alerts</h4>
  {div_html if div_html else '<p class="muted">No divergences detected.</p>'}
  <h4 style="margin-top:16px">&#128225; Story Angles</h4>
  {angles_html}
  {f'<h4 style="margin-top:16px">&#9673; Key Data Relationships</h4><div class="signal-context">{esc(key_rel)}</div>' if key_rel else ''}
</div>'''


def build_pitches_panel(pitches):
    html = ""
    for p in pitches[:3]:
        badge = '<span class="pitch-new">New</span>' if p.get("is_new") else ""
        html += f'''<div class="pitch-card">
  {badge}
  <h4>{esc(p.get("title",""))}</h4>
  <p class="muted">{esc(p.get("hook",""))}</p>
</div>'''
    return f'<div class="panel-section"><div class="section-badge">{len(pitches)} pitches</div><div class="grid grid-3">{html}</div></div>'


def build_tweets_panel(tweets):
    html = ""
    for i, t in enumerate(tweets[:2]):
        letter = chr(65 + i)
        html += f'''<div class="tweet-card">
  <div class="tweet-header">
    <span class="tweet-label">Option {letter} &mdash; {esc(t.get("label",""))}</span>
    <button class="copy-btn" onclick="copyTweet(this)">Copy</button>
  </div>
  <pre class="tweet-text">{esc(t.get("text",""))}</pre>
</div>'''
    return f'<div class="panel-section"><div class="tweet-handle">@thomasbahamas</div>{html}</div>'


def build_briefing_panel(briefing):
    if not briefing:
        return '<div class="panel-section"><p class="muted">No briefing generated.</p></div>'
    return f'''<div class="panel-section">
  <div class="brief-header">
    <span class="muted">~3-4 min read</span>
    <button class="copy-btn" onclick="copyBriefing()">Copy full script</button>
  </div>
  <pre class="briefing-box" id="briefing-text">{esc(briefing)}</pre>
</div>'''


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

CSS = """
:root {
  --bg: #09090b; --surface: #111113; --surface2: #18181b;
  --border: #1e1e2e; --border-heavy: #27272a; --text: #e4e4e7; --muted: #71717a;
  --accent: #9333ea; --accent2: #7c3aed;
  --green: #22c55e; --red: #ef4444;
}
* { margin:0; padding:0; box-sizing:border-box; }
html { scroll-behavior: smooth; scroll-padding-top: 56px; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', sans-serif;
  background: var(--bg); color: var(--text);
  line-height: 1.6; max-width: 1400px; margin: 0 auto; padding: 12px;
}
h3 { font-size: 1rem; margin-bottom: 8px; }
h4 { font-size: 0.9rem; margin-bottom: 8px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; }

/* Header */
.header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 16px 0; border-bottom: 1px solid var(--border);
}
.header-left h1 { font-size: 1.6rem; letter-spacing: 2px; }
.header-left h1 a { color: inherit; text-decoration: none; }
.header-left .meta { color: var(--muted); font-size: 0.8rem; }
.header-right { display: flex; align-items: center; gap: 12px; }
.fg-mini { text-align: center; }
.fg-mini .fg-label-text { font-size: 0.75rem; color: var(--muted); }

/* Sticky section nav */
.section-nav {
  position: sticky; top: 0; z-index: 100;
  background: var(--bg); border-bottom: 1px solid var(--border);
  display: flex; gap: 0; overflow-x: auto; -webkit-overflow-scrolling: touch;
  backdrop-filter: blur(8px);
}
.section-nav a {
  padding: 12px 18px; font-size: 0.78rem; font-weight: 600;
  color: var(--muted); text-decoration: none; white-space: nowrap;
  border-bottom: 2px solid transparent;
  transition: color 0.15s, border-color 0.15s;
}
.section-nav a:hover { color: var(--text); }
.section-nav a.active { color: var(--accent); border-bottom-color: var(--accent); }

/* Newsletter banner */
.nl-banner {
  background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
  padding: 10px 20px; margin: 12px 0; display: flex; align-items: center;
  justify-content: space-between; gap: 12px; flex-wrap: wrap;
}
.nl-banner .nl-text { font-size: 0.82rem; color: var(--muted); }
.nl-banner .nl-text strong { color: var(--text); }
.nl-banner a {
  background: var(--accent); color: #fff; padding: 6px 18px; border-radius: 5px;
  font-size: 0.8rem; font-weight: 600; text-decoration: none; white-space: nowrap;
}
.nl-banner a:hover { opacity: 0.9; }

/* Sections */
.dash-section {
  padding: 24px 0; border-bottom: 1px solid var(--border);
}
.dash-section:last-of-type { border-bottom: none; }
.section-title {
  font-size: 0.75rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 1.5px; color: var(--accent); margin-bottom: 16px;
  padding-bottom: 8px; border-bottom: 2px solid var(--accent);
  display: inline-block;
}

/* Two-column section layout */
.section-cols {
  display: grid; grid-template-columns: 1fr 1fr; gap: 24px;
}
.section-cols-3 {
  display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 24px;
}
.section-full { }

/* Panel section (reused from panel builders) */
.panel-section { padding: 0; }
.section-badge {
  display: inline-block; background: var(--surface2); color: var(--muted);
  padding: 2px 10px; border-radius: 4px; font-size: 0.75rem; margin-bottom: 12px;
}

/* Stats */
.stats-row { display: flex; gap: 20px; flex-wrap: wrap; margin-bottom: 16px; }
.stat { flex: 1; min-width: 120px; }
.stat-label { color: var(--muted); font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.5px; }
.stat-value { font-size: 1.25rem; font-weight: 700; }
.muted { color: var(--muted); font-size: 0.8rem; }
.wow { color: var(--muted); font-size: 0.75rem; font-style: italic; }

/* Grid */
.grid { display: grid; gap: 12px; }
.grid-2 { grid-template-columns: 1fr 1fr; }
.grid-3 { grid-template-columns: 1fr 1fr 1fr; }
.grid-prices { grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 10px; }

/* Cards */
.card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 14px;
}
.price-card .price-name { font-size: 0.8rem; font-weight: 600; }
.price-card .price-name .muted { font-weight: 400; font-size: 0.7rem; }
.price-card .price-val { font-size: 1.2rem; font-weight: 700; margin: 2px 0; }

/* F&G */
.fg-section {
  display: flex; align-items: center; gap: 16px;
  margin-top: 16px; padding: 12px; background: var(--surface);
  border-radius: 8px; border: 1px solid var(--border); width: fit-content;
}
.fg-label-text { font-weight: 600; }

/* Tables */
table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
th { text-align: left; color: var(--muted); padding: 8px 6px; border-bottom: 1px solid var(--border); font-weight: 500; font-size: 0.75rem; text-transform: uppercase; }
td { padding: 7px 6px; border-bottom: 1px solid var(--border); }
.full-table { font-size: 0.8rem; }

/* News */
.news-item { padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 0.85rem; }
.news-item a { color: var(--text); text-decoration: none; }
.news-item a:hover { color: var(--accent); }
.news-cat {
  display: inline-block; background: var(--surface2); color: var(--accent);
  padding: 1px 8px; border-radius: 3px; font-size: 0.65rem; margin-left: 6px;
}
.news-source { color: var(--muted); font-size: 0.7rem; margin-left: 6px; }

/* Technical */
.tech-row { display: flex; gap: 32px; flex-wrap: wrap; margin-top: 8px; }

/* Trending */
.trending-row { display: flex; flex-wrap: wrap; gap: 6px; }
.trending-tag {
  display: inline-block; background: var(--surface2); border: 1px solid var(--border);
  padding: 4px 12px; border-radius: 4px; font-size: 0.8rem;
}

/* Whale */
.whale-event { padding: 10px 0; border-bottom: 1px solid var(--border); }
.whale-title { font-size: 0.88rem; }
.whale-meta { display: flex; gap: 12px; margin-top: 4px; }
.staking-row { font-size: 0.82rem; padding: 4px 0; }

/* Market Makers */
.mm-signal {
  display: flex; gap: 10px; align-items: flex-start;
  padding: 10px 0; border-bottom: 1px solid var(--border);
}
.mm-body { flex: 1; }
.mm-sentiment { font-size: 0.75rem; color: var(--muted); margin-top: 2px; font-style: italic; }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-top: 6px; flex-shrink: 0; }
.dot-green-bright { background: #16a34a; }
.dot-green { background: var(--green); }
.dot-red { background: var(--red); }
.dot-gray { background: var(--muted); }

/* X Pulse */
.xp-item { padding: 6px 0; border-bottom: 1px solid var(--border); font-size: 0.85rem; }
.xp-handle { color: var(--accent); font-weight: 600; margin-right: 6px; font-size: 0.8rem; }
.narr-item { padding: 4px 0; font-size: 0.85rem; }
.narr-arrow { color: var(--accent); margin-right: 4px; }

/* Signal */
.signal-context {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 16px; font-size: 0.88rem; line-height: 1.7;
  white-space: pre-wrap;
}
.div-alert { padding: 10px 0 10px 14px; margin-bottom: 8px; }
.div-sev { font-size: 0.75rem; font-weight: 600; margin-left: 6px; }

/* Footer */
footer { text-align: center; color: var(--muted); padding: 24px 0; font-size: 0.75rem; border-top: 1px solid var(--border); margin-top: 20px; }
footer a { color: var(--accent); text-decoration: none; }
footer a:hover { text-decoration: underline; }

/* Responsive */
@media (max-width: 768px) {
  .grid-2, .grid-3, .section-cols, .section-cols-3 { grid-template-columns: 1fr; }
  .grid-prices { grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); }
  .stats-row { flex-direction: column; gap: 12px; }
  .header { flex-direction: column; gap: 8px; align-items: flex-start; }
  .section-nav a { padding: 10px 14px; font-size: 0.75rem; }
  .tech-row { flex-direction: column; }
}
"""

# ---------------------------------------------------------------------------
# JS
# ---------------------------------------------------------------------------

JS = """
// Scroll-spy: highlight active section in nav
(function() {
  var links = document.querySelectorAll('.section-nav a');
  var sections = [];
  for (var i = 0; i < links.length; i++) {
    var id = links[i].getAttribute('href').slice(1);
    var el = document.getElementById(id);
    if (el) sections.push({el: el, link: links[i]});
  }
  function onScroll() {
    var scrollY = window.scrollY + 80;
    var active = sections[0];
    for (var i = 0; i < sections.length; i++) {
      if (sections[i].el.offsetTop <= scrollY) active = sections[i];
    }
    for (var i = 0; i < sections.length; i++) {
      sections[i].link.classList.toggle('active', sections[i] === active);
    }
  }
  window.addEventListener('scroll', onScroll, {passive: true});
  onScroll();
})();
"""


# ---------------------------------------------------------------------------
# Homepage builder
# ---------------------------------------------------------------------------

HOMEPAGE_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700;900&family=Inter:wght@400;500;600&family=Source+Serif+4:ital,wght@0,400;0,600;1,400&display=swap');

:root {
  --bg: #09090b; --surface: #111113; --surface2: #18181b;
  --border: #27272a; --border-heavy: #3f3f46;
  --text: #e4e4e7; --muted: #71717a; --dim: #52525b;
  --accent: #9333ea; --accent2: #7c3aed;
  --green: #22c55e; --red: #ef4444;
  --serif: 'Playfair Display', 'Georgia', 'Times New Roman', serif;
  --body: 'Source Serif 4', 'Georgia', serif;
  --sans: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
}
* { margin:0; padding:0; box-sizing:border-box; }
body {
  font-family: var(--body); background: var(--bg); color: var(--text);
  line-height: 1.7; max-width: 900px; margin: 0 auto; padding: 0 20px;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

/* === MASTHEAD === */
.masthead {
  text-align: center; padding: 36px 0 0;
}
.masthead-rule { border: none; border-top: 3px double var(--border-heavy); margin-bottom: 16px; }
.masthead h1 {
  font-family: var(--serif); font-size: 3.2rem; font-weight: 900;
  letter-spacing: 4px; line-height: 1.1;
}
.masthead .edition {
  font-family: var(--sans); font-size: 0.75rem; color: var(--muted);
  text-transform: uppercase; letter-spacing: 2px; margin-top: 6px;
}
.masthead-rule-bottom { border: none; border-top: 1px solid var(--border); margin-top: 16px; }

/* === NAV === */
.hp-nav {
  display: flex; justify-content: center; gap: 8px;
  padding: 10px 0; border-bottom: 1px solid var(--border);
  font-family: var(--sans); font-size: 0.78rem; text-transform: uppercase;
  letter-spacing: 1px;
}
.hp-nav a {
  color: var(--dim); padding: 4px 14px;
  transition: color 0.15s;
}
.hp-nav a:hover { color: var(--text); text-decoration: none; }
.hp-nav-sep { color: var(--border); }

/* === TICKER STRIP === */
.ticker-strip {
  display: flex; justify-content: center; gap: 0;
  border-bottom: 2px solid var(--border-heavy);
  font-family: var(--sans); overflow-x: auto;
}
.ticker {
  padding: 12px 20px; text-align: center;
  border-right: 1px solid var(--border); flex-shrink: 0;
}
.ticker:last-child { border-right: none; }
.ticker .t-label { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 1px; color: var(--dim); }
.ticker .t-value { font-size: 1.15rem; font-weight: 700; margin: 2px 0; }
.ticker .t-change { font-size: 0.75rem; }

/* === HEADLINE / LEAD === */
.lead {
  padding: 28px 0; border-bottom: 1px solid var(--border);
}
.lead .kicker {
  font-family: var(--sans); font-size: 0.7rem; text-transform: uppercase;
  letter-spacing: 1.5px; color: var(--accent); margin-bottom: 8px;
}
.lead h2 {
  font-family: var(--serif); font-size: 1.8rem; font-weight: 700;
  line-height: 1.3; margin-bottom: 12px;
}
.lead .deck {
  font-size: 1.05rem; color: var(--muted); line-height: 1.7;
  font-style: italic;
}

/* === TWO COLUMN BODY === */
.columns {
  display: grid; grid-template-columns: 1fr 1fr; gap: 32px;
  padding: 24px 0; border-bottom: 1px solid var(--border);
}
.col { }

/* === SECTIONS === */
.section-head {
  font-family: var(--sans); font-size: 0.7rem; text-transform: uppercase;
  letter-spacing: 1.5px; color: var(--accent); padding-bottom: 6px;
  border-bottom: 2px solid var(--accent); margin-bottom: 14px;
  display: inline-block;
}

/* Signal/context */
.signal-text {
  font-size: 0.95rem; line-height: 1.85; white-space: pre-wrap;
}

/* Alerts */
.hp-alert {
  padding: 10px 0 10px 16px; margin-bottom: 8px;
  border-left: 3px solid var(--dim);
}
.hp-alert-high { border-left-color: var(--red); }
.hp-alert-medium { border-left-color: #f97316; }
.hp-alert-low { border-left-color: #eab308; }
.hp-alert strong { font-family: var(--sans); font-size: 0.85rem; }
.hp-alert .desc { color: var(--muted); font-size: 0.82rem; margin-top: 2px; }
.hp-alert .sev {
  font-family: var(--sans); font-size: 0.65rem; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.5px; margin-left: 8px;
}

/* News */
.hp-news-item {
  padding: 10px 0; border-bottom: 1px solid var(--border);
}
.hp-news-item a { color: var(--text); font-size: 0.9rem; }
.hp-news-item a:hover { color: var(--accent); }
.hp-news-source {
  display: block; font-family: var(--sans); color: var(--dim);
  font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 2px;
}

/* Angles */
.hp-angle { padding: 5px 0; font-size: 0.9rem; }
.hp-angle-arrow { color: var(--accent); margin-right: 6px; }

/* === CTA === */
.hp-cta { text-align: center; padding: 32px 0; }
.hp-cta-btn {
  display: inline-block; background: var(--accent); color: #fff;
  padding: 14px 44px; border-radius: 6px; font-family: var(--sans);
  font-size: 0.9rem; font-weight: 600; text-decoration: none;
  letter-spacing: 0.5px; transition: opacity 0.15s;
}
.hp-cta-btn:hover { opacity: 0.9; text-decoration: none; }

/* === NEWSLETTER === */
.hp-newsletter {
  border: 2px solid var(--border-heavy); padding: 36px 32px;
  text-align: center; margin: 0 0 24px;
}
.hp-newsletter h2 { font-family: var(--serif); font-size: 1.3rem; margin-bottom: 6px; }
.hp-newsletter .sub { color: var(--muted); font-size: 0.88rem; margin-bottom: 20px; }
.hp-email-form { display: flex; gap: 8px; max-width: 420px; margin: 0 auto; }
.hp-email-input {
  flex: 1; padding: 12px 16px; border: 1px solid var(--border-heavy);
  background: var(--bg); color: var(--text); font-family: var(--sans);
  font-size: 0.88rem; outline: none;
}
.hp-email-input:focus { border-color: var(--accent); }
.hp-email-btn {
  padding: 12px 28px; border: none;
  background: var(--text); color: var(--bg); font-family: var(--sans);
  font-weight: 600; cursor: pointer; font-size: 0.85rem;
  letter-spacing: 0.5px; transition: opacity 0.15s;
}
.hp-email-btn:hover { opacity: 0.85; }

/* === FOOTER === */
footer {
  text-align: center; color: var(--dim); padding: 20px 0;
  font-family: var(--sans); font-size: 0.7rem; letter-spacing: 0.5px;
  border-top: 3px double var(--border-heavy); margin-top: 8px;
}
footer a { color: var(--muted); }

/* === RESPONSIVE === */
@media (max-width: 640px) {
  .masthead h1 { font-size: 2.2rem; letter-spacing: 2px; }
  .columns { grid-template-columns: 1fr; gap: 0; }
  .col { padding-bottom: 20px; border-bottom: 1px solid var(--border); margin-bottom: 20px; }
  .col:last-child { border-bottom: none; margin-bottom: 0; }
  .ticker { padding: 10px 14px; }
  .ticker .t-value { font-size: 1rem; }
  .lead h2 { font-size: 1.4rem; }
  .hp-email-form { flex-direction: column; }
  .hp-newsletter { padding: 28px 20px; }
}
"""


def build_homepage(compiled: dict, narrative: dict) -> str:
    """Build the summary/highlights homepage at solanaweekly.io/."""
    market = compiled.get("market", {})
    solana = compiled.get("solana", {})
    news = compiled.get("news", {})
    wow = compiled.get("wow", {})
    generated_at = compiled.get("generated_at", now_utc())

    prices = market.get("prices", {})
    global_data = market.get("global", {})
    fg = market.get("fear_greed", {})
    dex = solana.get("dex_volumes", {})
    sol_tvl = solana.get("solana_tvl", {})

    signal = (narrative or {}).get("the_signal", {})
    context = signal.get("market_context", "")
    divergences = signal.get("divergence_alerts", [])
    angles = signal.get("story_angles", [])

    # Key numbers
    sol = prices.get("SOL", {})
    btc = prices.get("BTC", {})
    fg_val = fg.get("value", "N/A")
    fg_label = fg.get("label", "")

    # Ticker data
    sol_price = sol.get("price", 0)
    sol_chg = sol.get("change_24h")
    btc_price = btc.get("price", 0)
    btc_chg = btc.get("change_24h")
    dex_combined = dex.get("combined_24h", 0)
    tvl_current = sol_tvl.get("current", 0)
    tvl_chg = sol_tvl.get("change_1d")

    fg_color = "#ef4444" if isinstance(fg_val, (int, float)) and fg_val <= 25 else "#f97316" if isinstance(fg_val, (int, float)) and fg_val <= 45 else "#eab308" if isinstance(fg_val, (int, float)) and fg_val <= 55 else "#22c55e"

    # Build headline from signal or default
    headline = "Solana Ecosystem Daily Briefing"
    deck = ""
    if context:
        # Use first sentence of context as deck
        sentences = context.replace("\n", " ").split(". ")
        if len(sentences) >= 2:
            headline = sentences[0].strip().rstrip(".")
            deck = ". ".join(sentences[1:3]).strip()
            if deck and not deck.endswith("."):
                deck += "."
        else:
            deck = context[:200]

    # Divergence alerts
    div_html = ""
    if divergences:
        for d in divergences[:4]:
            sev = d.get("severity", "low")
            sev_color = "var(--red)" if sev == "high" else "#f97316" if sev == "medium" else "#eab308"
            div_html += f'''<div class="hp-alert hp-alert-{sev}">
  <strong>{esc(d.get("title",""))}</strong><span class="sev" style="color:{sev_color}">{sev}</span>
  <div class="desc">{esc(d.get("description",""))}</div>
</div>'''

    # Story angles
    angles_html = ""
    if angles:
        for a in angles[:4]:
            angles_html += f'<div class="hp-angle"><span class="hp-angle-arrow">&#9656;</span> {esc(a)}</div>'

    # Signal body text
    signal_body = ""
    if context:
        signal_body = f'<div class="signal-text">{esc(context)}</div>'
    else:
        signal_body = '<p style="color:var(--muted);font-style:italic">Signal analysis generates daily at 6am PT. Check back soon.</p>'

    # Top news
    all_news = news.get("solana_news", []) + news.get("general_news", [])
    news_html = ""
    for s in all_news[:6]:
        news_html += f'''<div class="hp-news-item">
  <a href="{esc(s.get("url","#"))}" target="_blank" rel="noopener">{esc(s["title"])}</a>
  <span class="hp-news-source">{esc(s.get("source",""))}</span>
</div>'''

    # Format date nicely
    from datetime import datetime
    try:
        dt = datetime.strptime(generated_at[:19], "%Y-%m-%d %H:%M")
        date_display = dt.strftime("%A, %B %-d, %Y")
    except Exception:
        date_display = generated_at[:10]

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Solana Weekly — Daily Solana Intelligence</title>
<meta name="description" content="Daily Solana ecosystem intelligence. Key metrics, market analysis, divergence alerts, and top stories delivered to your inbox every morning.">
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%239333ea'/%3E%3Ctext x='16' y='23' font-family='Arial,sans-serif' font-size='22' font-weight='900' fill='white' text-anchor='middle'%3ES%3C/text%3E%3C/svg%3E">
<style>{HOMEPAGE_CSS}</style>
</head>
<body>

<!-- MASTHEAD -->
<div class="masthead">
  <hr class="masthead-rule">
  <h1>SOLANA WEEKLY</h1>
  <div class="edition">{esc(date_display)} &nbsp;&bull;&nbsp; Daily Intelligence</div>
  <hr class="masthead-rule-bottom">
</div>

<!-- NAV -->
<nav class="hp-nav">
  <a href="/dashboard">Dashboard</a>
  <span class="hp-nav-sep">|</span>
  <a href="https://solanaweekly.fun" target="_blank">Podcast</a>
  <span class="hp-nav-sep">|</span>
  <a href="#newsletter">Subscribe</a>
</nav>

<!-- TICKER STRIP -->
<div class="ticker-strip">
  <div class="ticker">
    <div class="t-label">SOL</div>
    <div class="t-value">${sol_price:,.2f}</div>
    <div class="t-change">{fmt_change(sol_chg)}</div>
  </div>
  <div class="ticker">
    <div class="t-label">BTC</div>
    <div class="t-value">${btc_price:,.0f}</div>
    <div class="t-change">{fmt_change(btc_chg)}</div>
  </div>
  <div class="ticker">
    <div class="t-label">Fear &amp; Greed</div>
    <div class="t-value" style="color:{fg_color}">{fg_val}</div>
    <div class="t-change" style="color:var(--muted)">{esc(str(fg_label))}</div>
  </div>
  <div class="ticker">
    <div class="t-label">DEX Vol 24h</div>
    <div class="t-value">{fmt_usd(dex_combined)}</div>
    <div class="t-change">{fmt_wow(wow, "dex_volume")}</div>
  </div>
  <div class="ticker">
    <div class="t-label">Solana TVL</div>
    <div class="t-value">{fmt_usd(tvl_current)}</div>
    <div class="t-change">{fmt_change(tvl_chg)}</div>
  </div>
</div>

<!-- LEAD STORY -->
<div class="lead">
  <div class="kicker">Today&rsquo;s Signal</div>
  <h2>{esc(headline)}</h2>
  {f'<div class="deck">{esc(deck)}</div>' if deck else ''}
</div>

<!-- TWO COLUMN BODY -->
<div class="columns">
  <div class="col">
    <div class="section-head">Analysis</div>
    {signal_body}
    {f'<div style="margin-top:20px"><div class="section-head">What to Watch</div>{angles_html}</div>' if angles_html else ''}
  </div>
  <div class="col">
    {f'<div class="section-head">Divergence Alerts</div>{div_html}' if div_html else ''}
    <div style="margin-top:{20 if div_html else 0}px">
      <div class="section-head">Headlines</div>
      {news_html if news_html else '<p style="color:var(--muted)">No stories available.</p>'}
    </div>
  </div>
</div>

<!-- CTA -->
<div class="hp-cta">
  <a href="/dashboard" class="hp-cta-btn">Open Full Dashboard &rarr;</a>
</div>

<!-- NEWSLETTER -->
<div class="hp-newsletter" id="newsletter">
  <h2>Get This In Your Inbox</h2>
  <div class="sub">Daily highlights + what matters in Solana. Every morning, before the market moves.</div>
  <form class="hp-email-form" id="newsletter-form" onsubmit="return handleSubscribe(event)">
    <input type="email" class="hp-email-input" placeholder="your@email.com" required id="newsletter-email">
    <button type="submit" class="hp-email-btn">Subscribe</button>
  </form>
  <div id="newsletter-msg" style="margin-top:12px;font-size:0.85rem;display:none"></div>
</div>

<!-- FOOTER -->
<footer>
  solanaweekly.io &nbsp;&bull;&nbsp; Daily Solana Intelligence &nbsp;&bull;&nbsp; {esc(generated_at)}
</footer>

<script>
function handleSubscribe(e) {{
  e.preventDefault();
  var email = document.getElementById('newsletter-email').value;
  var msg = document.getElementById('newsletter-msg');
  // TODO: Connect to Kit (ConvertKit) API
  var subs = JSON.parse(localStorage.getItem('sw_subscribers') || '[]');
  if (subs.indexOf(email) === -1) subs.push(email);
  localStorage.setItem('sw_subscribers', JSON.stringify(subs));
  msg.style.display = 'block';
  msg.style.color = '#22c55e';
  msg.textContent = 'You\\'re in! Newsletter coming soon.';
  document.getElementById('newsletter-email').value = '';
  return false;
}}
</script>

</body>
</html>'''


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_dashboard(compiled: dict, narrative: dict) -> str:
    market = compiled.get("market", {})
    solana = compiled.get("solana", {})
    news = compiled.get("news", {})
    whales = compiled.get("whales", {})
    wow = compiled.get("wow", {})
    run_num = compiled.get("run_number", "")

    prices = market.get("prices", {})
    global_data = market.get("global", {})
    fg = market.get("fear_greed", {})
    technicals = market.get("sol_technicals", {})
    monthly_returns = technicals.get("monthly_returns", [])
    trending = market.get("trending", [])
    chain_tvls = solana.get("chain_tvls", [])
    dex = solana.get("dex_volumes", {})
    protocols = solana.get("protocol_rankings", [])

    upgrades = compiled.get("upgrades", {})
    signal = narrative.get("the_signal", {})

    fg_val = fg.get("value", "N/A")
    fg_label = fg.get("label", "")
    gauge_mini = svg_gauge(fg_val if isinstance(fg_val, (int, float)) else 50, size=70)

    generated_at = compiled.get("generated_at", now_utc())
    date_str = generated_at[:10] if len(generated_at) >= 10 else generated_at

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Solana Weekly Dashboard</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%239333ea'/%3E%3Ctext x='16' y='23' font-family='Arial,sans-serif' font-size='22' font-weight='900' fill='white' text-anchor='middle'%3ES%3C/text%3E%3C/svg%3E">
<style>{CSS}</style>
</head>
<body>

<!-- HEADER -->
<div class="header">
  <div class="header-left">
    <h1><a href="/">SOLANA WEEKLY</a></h1>
    <div class="meta">Updated: {esc(generated_at)}{f" &middot; Run #{run_num}" if run_num else ""}</div>
  </div>
  <div class="header-right">
    <div class="fg-mini">
      {gauge_mini}
      <div class="fg-label-text">{esc(str(fg_label))}</div>
    </div>
  </div>
</div>

<!-- STICKY NAV -->
<nav class="section-nav">
  <a href="#market" class="active">Market</a>
  <a href="#technical">Technical</a>
  <a href="#solana">Solana</a>
  <a href="#dex">DEX</a>
  <a href="#protocols">Protocols</a>
  <a href="#upgrades">Upgrades</a>
  <a href="#signal">Signal</a>
  <a href="#intelligence">Intel</a>
  <a href="#ecosystem">Ecosystem</a>
  <a href="#news">News</a>
</nav>

<!-- NEWSLETTER BANNER -->
<div class="nl-banner">
  <div class="nl-text"><strong>Get daily Solana intelligence in your inbox.</strong> Key numbers + what they mean, every morning.</div>
  <a href="/#newsletter">Subscribe Free</a>
</div>

<!-- ====== MARKET ====== -->
<div class="dash-section" id="market">
  <div class="section-title">Market Overview</div>
  {build_market_panel(prices, global_data, fg, wow)}
</div>

<!-- ====== TECHNICAL ====== -->
<div class="dash-section" id="technical">
  <div class="section-title">SOL Technical Analysis</div>
  {build_technical_panel(technicals, monthly_returns)}
</div>

<!-- ====== SOLANA ECOSYSTEM ====== -->
<div class="dash-section" id="solana">
  <div class="section-title">Solana Ecosystem</div>
  {build_solana_panel(solana, wow)}
</div>

<!-- ====== DEX VOLUME ====== -->
<div class="dash-section" id="dex">
  <div class="section-title">DEX Volume</div>
  {build_dex_panel(dex)}
</div>

<!-- ====== PROTOCOLS ====== -->
<div class="dash-section" id="protocols">
  <div class="section-title">Top Protocols</div>
  {build_protocols_panel(protocols)}
</div>

<!-- ====== NETWORK UPGRADES ====== -->
<div class="dash-section" id="upgrades">
  <div class="section-title">Network Upgrades</div>
  {build_upgrades_panel(upgrades)}
</div>

<!-- ====== THE SIGNAL ====== -->
<div class="dash-section" id="signal">
  <div class="section-title">The Signal</div>
  {build_signal_panel(signal)}
</div>

<!-- ====== INTELLIGENCE ====== -->
<div class="dash-section" id="intelligence">
  <div class="section-title">Intelligence</div>
  <div class="section-cols">
    <div>
      <h4>Whales &amp; Staking</h4>
      {build_whale_panel(whales)}
    </div>
    <div>
      <h4>Market Makers</h4>
      {build_market_makers_panel(narrative)}
    </div>
  </div>
  <div style="margin-top:20px">
    <h4>X Pulse</h4>
    {build_xpulse_panel(narrative)}
  </div>
</div>

<!-- ====== ECOSYSTEM ====== -->
<div class="dash-section" id="ecosystem">
  <div class="section-title">Cross-Chain Ecosystem</div>
  {build_ecosystem_panel(chain_tvls, trending)}
</div>

<!-- ====== NEWS ====== -->
<div class="dash-section" id="news">
  <div class="section-title">News Feed</div>
  {build_news_panel(news)}
</div>

<footer>
  <a href="/">solanaweekly.io</a> &middot; Daily Solana Intelligence &middot; {esc(generated_at)}
</footer>

<script>{JS}</script>
</body>
</html>'''


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> str:
    compiled = load_json("compiled.json")
    narrative = load_json("narrative.json")

    if not compiled:
        log.error("No compiled data found")
        return ""

    # Build full dashboard → output/dashboard/index.html
    dashboard_html = build_dashboard(compiled, narrative)
    dashboard_dir = OUTPUT_DIR / "dashboard"
    dashboard_dir.mkdir(parents=True, exist_ok=True)
    dashboard_path = dashboard_dir / "index.html"
    with open(dashboard_path, "w") as f:
        f.write(dashboard_html)
    log.info(f"Dashboard written to {dashboard_path}")

    # Build homepage → output/index.html
    homepage_html = build_homepage(compiled, narrative)
    homepage_path = OUTPUT_DIR / "index.html"
    with open(homepage_path, "w") as f:
        f.write(homepage_html)
    log.info(f"Homepage written to {homepage_path}")

    # Write CNAME for GitHub Pages custom domain
    cname_path = OUTPUT_DIR / "CNAME"
    with open(cname_path, "w") as f:
        f.write("solanaweekly.io")

    return str(dashboard_path)


if __name__ == "__main__":
    run()
