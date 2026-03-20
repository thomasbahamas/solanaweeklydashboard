"""Generate the Solana Floor dashboard as a static HTML page.

Tabbed interface with sections:
  Overview  — Market, Technical, News
  Deep Dive — Ecosystem, Solana, DEX Volume, Protocols
  Intelligence — Whales, Market Makers, X Pulse, The Signal
  Content — Pitches, Tweets, Briefing
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
  --border: #1e1e2e; --text: #e4e4e7; --muted: #71717a;
  --accent: #9333ea; --accent2: #7c3aed;
  --green: #22c55e; --red: #ef4444;
}
* { margin:0; padding:0; box-sizing:border-box; }
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
  padding: 16px 0; border-bottom: 1px solid var(--border); margin-bottom: 0;
}
.header-left h1 { font-size: 1.6rem; letter-spacing: 2px; }
.header-left .meta { color: var(--muted); font-size: 0.8rem; }
.header-right { display: flex; align-items: center; gap: 12px; }
.fg-mini { text-align: center; }
.fg-mini .fg-label-text { font-size: 0.75rem; color: var(--muted); }

/* Tabs */
.tab-bar {
  display: flex; gap: 0; border-bottom: 1px solid var(--border);
  overflow-x: auto; -webkit-overflow-scrolling: touch;
}
.tab-group {
  padding: 12px 20px; font-size: 0.85rem; font-weight: 600;
  color: var(--muted); background: none; border: none; cursor: pointer;
  border-bottom: 2px solid transparent; white-space: nowrap;
  transition: color 0.15s, border-color 0.15s;
}
.tab-group:hover { color: var(--text); }
.tab-group.active { color: var(--accent); border-bottom-color: var(--accent); }
.sub-bar {
  display: flex; gap: 0; border-bottom: 1px solid var(--border);
  overflow-x: auto; background: var(--surface);
}
.sub-tab {
  padding: 10px 16px; font-size: 0.8rem; color: var(--muted);
  background: none; border: none; cursor: pointer;
  border-bottom: 2px solid transparent; white-space: nowrap;
  transition: color 0.15s, border-color 0.15s;
}
.sub-tab:hover { color: var(--text); }
.sub-tab.active { color: var(--text); border-bottom-color: var(--accent); }

/* Panels */
.panel { display: none; padding: 16px 0; }
.panel.active { display: block; }
.panel-section { padding: 0; }
.section-badge {
  display: inline-block; background: var(--surface2); color: var(--muted);
  padding: 2px 10px; border-radius: 4px; font-size: 0.75rem; margin-bottom: 12px;
}

/* Stats */
.stats-row { display: flex; gap: 20px; flex-wrap: wrap; margin-bottom: 16px; }
.stat { flex: 1; min-width: 140px; }
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

/* Pitches */
.pitch-card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 14px; position: relative;
}
.pitch-new {
  display: inline-block; background: var(--green); color: #000;
  padding: 1px 8px; border-radius: 3px; font-size: 0.65rem; font-weight: 700; margin-bottom: 6px;
}

/* Tweets */
.tweet-handle { color: var(--accent); font-weight: 600; margin-bottom: 12px; }
.tweet-card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 14px; margin-bottom: 10px;
}
.tweet-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.tweet-label { color: var(--accent); font-size: 0.8rem; font-weight: 600; }
.tweet-text {
  white-space: pre-wrap; font-family: inherit; font-size: 0.88rem;
  color: var(--text); background: none; border: none; margin: 0;
}
.copy-btn {
  background: var(--surface2); color: var(--muted); border: 1px solid var(--border);
  padding: 4px 12px; border-radius: 4px; cursor: pointer; font-size: 0.75rem;
  transition: color 0.15s, border-color 0.15s;
}
.copy-btn:hover { color: var(--text); border-color: var(--accent); }

/* Briefing */
.brief-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.briefing-box {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 20px; font-family: 'SF Mono', 'Fira Code', monospace;
  font-size: 0.82rem; line-height: 1.8; white-space: pre-wrap;
  max-height: 700px; overflow-y: auto;
}

/* Footer */
footer { text-align: center; color: var(--muted); padding: 24px 0; font-size: 0.75rem; border-top: 1px solid var(--border); margin-top: 20px; }

/* Responsive */
@media (max-width: 768px) {
  .grid-2, .grid-3 { grid-template-columns: 1fr; }
  .grid-prices { grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); }
  .stats-row { flex-direction: column; gap: 12px; }
  .header { flex-direction: column; gap: 8px; align-items: flex-start; }
  .tab-group { padding: 10px 14px; font-size: 0.8rem; }
  .sub-tab { padding: 8px 12px; font-size: 0.75rem; }
  .tech-row { flex-direction: column; }
}
"""

# ---------------------------------------------------------------------------
# JS
# ---------------------------------------------------------------------------

JS = """
var TABS = {
  overview: ['market','technical','news'],
  deepdive: ['ecosystem','solana','dex','protocols'],
  intelligence: ['whales','marketmakers','xpulse','signal'],
  content: ['pitches','tweets','briefing']
};
var currentGroup = 'overview';

function showGroup(group) {
  currentGroup = group;
  var i, els;
  // Update main tabs
  els = document.querySelectorAll('.tab-group');
  for (i = 0; i < els.length; i++) els[i].className = 'tab-group';
  var activeTab = document.querySelector('.tab-group[data-group=\"' + group + '\"]');
  if (activeTab) activeTab.className = 'tab-group active';
  // Show correct sub-bar
  els = document.querySelectorAll('.sub-bar');
  for (i = 0; i < els.length; i++) els[i].style.display = 'none';
  var bar = document.getElementById('sub-' + group);
  if (bar) bar.style.display = 'flex';
  // Show first panel in group
  showPanel(TABS[group][0]);
}

function showPanel(panel) {
  var i, els;
  // Hide all panels
  els = document.querySelectorAll('.panel');
  for (i = 0; i < els.length; i++) {
    els[i].style.display = 'none';
    els[i].className = 'panel';
  }
  // Show target
  var el = document.getElementById('panel-' + panel);
  if (el) {
    el.style.display = 'block';
    el.className = 'panel active';
  }
  // Update sub-tabs in current group
  var subBar = document.getElementById('sub-' + currentGroup);
  if (subBar) {
    var tabs = subBar.querySelectorAll('.sub-tab');
    for (i = 0; i < tabs.length; i++) tabs[i].className = 'sub-tab';
  }
  var tab = document.querySelector('.sub-tab[data-panel=\"' + panel + '\"]');
  if (tab) tab.className = 'sub-tab active';
}

function copyTweet(btn) {
  var text = btn.parentElement.nextElementSibling.textContent;
  navigator.clipboard.writeText(text).then(function() {
    btn.textContent = 'Copied!';
    setTimeout(function() { btn.textContent = 'Copy'; }, 1500);
  });
}

function copyBriefing() {
  var text = document.getElementById('briefing-text').textContent;
  navigator.clipboard.writeText(text).then(function() {
    var btn = document.querySelector('.brief-header .copy-btn');
    btn.textContent = 'Copied!';
    setTimeout(function() { btn.textContent = 'Copy full script'; }, 1500);
  });
}

// Init — run immediately, no DOMContentLoaded needed since script is at end of body
showGroup('overview');
"""


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

    signal = narrative.get("the_signal", {})
    pitches = narrative.get("story_pitches", [])
    tweets = narrative.get("tweet_options", [])
    briefing = narrative.get("briefing_script", "")

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
<title>Solana Floor Daily Dashboard</title>
<style>{CSS}</style>
</head>
<body>

<!-- HEADER -->
<div class="header">
  <div class="header-left">
    <h1>SOLANA FLOOR</h1>
    <div class="meta">Updated: {esc(generated_at)}{f" &middot; Run #{run_num}" if run_num else ""}</div>
  </div>
  <div class="header-right">
    <div class="fg-mini">
      {gauge_mini}
      <div class="fg-label-text">{esc(str(fg_label))}</div>
    </div>
  </div>
</div>

<!-- MAIN TABS -->
<div class="tab-bar">
  <button class="tab-group active" data-group="overview" onclick="showGroup('overview')">Overview</button>
  <button class="tab-group" data-group="deepdive" onclick="showGroup('deepdive')">Deep Dive</button>
  <button class="tab-group" data-group="intelligence" onclick="showGroup('intelligence')">Intelligence</button>
  <button class="tab-group" data-group="content" onclick="showGroup('content')">Content</button>
</div>

<!-- SUB TABS: Overview -->
<div class="sub-bar" id="sub-overview" style="display:flex">
  <button class="sub-tab active" data-panel="market" onclick="showPanel('market')">&#128202; Market</button>
  <button class="sub-tab" data-panel="technical" onclick="showPanel('technical')">&#128200; Technical</button>
  <button class="sub-tab" data-panel="news" onclick="showPanel('news')">&#128240; News</button>
</div>
<!-- SUB TABS: Deep Dive -->
<div class="sub-bar" id="sub-deepdive" style="display:none">
  <button class="sub-tab" data-panel="ecosystem" onclick="showPanel('ecosystem')">&#127760; Ecosystem</button>
  <button class="sub-tab" data-panel="solana" onclick="showPanel('solana')">&#9678; Solana</button>
  <button class="sub-tab" data-panel="dex" onclick="showPanel('dex')">&#128177; DEX Volume</button>
  <button class="sub-tab" data-panel="protocols" onclick="showPanel('protocols')">&#127959; Protocols</button>
</div>
<!-- SUB TABS: Intelligence -->
<div class="sub-bar" id="sub-intelligence" style="display:none">
  <button class="sub-tab" data-panel="whales" onclick="showPanel('whales')">&#128011; Whales</button>
  <button class="sub-tab" data-panel="marketmakers" onclick="showPanel('marketmakers')">&#127963; Market Makers</button>
  <button class="sub-tab" data-panel="xpulse" onclick="showPanel('xpulse')">&#120143; X Pulse</button>
  <button class="sub-tab" data-panel="signal" onclick="showPanel('signal')">&#129504; The Signal</button>
</div>
<!-- SUB TABS: Content -->
<div class="sub-bar" id="sub-content" style="display:none">
  <button class="sub-tab" data-panel="pitches" onclick="showPanel('pitches')">&#128221; Pitches</button>
  <button class="sub-tab" data-panel="tweets" onclick="showPanel('tweets')">&#128038; Tweets</button>
  <button class="sub-tab" data-panel="briefing" onclick="showPanel('briefing')">&#127908; Briefing</button>
</div>

<!-- PANELS -->
<div class="panel active" id="panel-market">{build_market_panel(prices, global_data, fg, wow)}</div>
<div class="panel" id="panel-technical">{build_technical_panel(technicals, monthly_returns)}</div>
<div class="panel" id="panel-news">{build_news_panel(news)}</div>
<div class="panel" id="panel-ecosystem">{build_ecosystem_panel(chain_tvls, trending)}</div>
<div class="panel" id="panel-solana">{build_solana_panel(solana, wow)}</div>
<div class="panel" id="panel-dex">{build_dex_panel(dex)}</div>
<div class="panel" id="panel-protocols">{build_protocols_panel(protocols)}</div>
<div class="panel" id="panel-whales">{build_whale_panel(whales)}</div>
<div class="panel" id="panel-marketmakers">{build_market_makers_panel(narrative)}</div>
<div class="panel" id="panel-xpulse">{build_xpulse_panel(narrative)}</div>
<div class="panel" id="panel-signal">{build_signal_panel(signal)}</div>
<div class="panel" id="panel-pitches">{build_pitches_panel(pitches)}</div>
<div class="panel" id="panel-tweets">{build_tweets_panel(tweets)}</div>
<div class="panel" id="panel-briefing">{build_briefing_panel(briefing)}</div>

<footer>Solana Floor Daily Intelligence &middot; Built for @thomasbahamas &middot; {esc(generated_at)}</footer>

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

    html = build_dashboard(compiled, narrative)

    output_path = OUTPUT_DIR / "index.html"
    with open(output_path, "w") as f:
        f.write(html)

    log.info(f"Dashboard written to {output_path}")
    return str(output_path)


if __name__ == "__main__":
    run()
