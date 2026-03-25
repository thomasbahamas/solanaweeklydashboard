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
    arrow = "&#9650;" if val > 0 else "&#9660;" if val < 0 else "&#8211;"
    return f'<span class="wow" style="color:{color}">{arrow} {val:+.1f}% WoW</span>'


def source_link(url, name):
    """Clickable source attribution link."""
    return f'<a href="{url}" target="_blank" rel="noopener" class="source-link">{name} &#8599;</a>'


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
    items = monthly_returns[-24:]  # last 24 months
    w = 460
    bar_w = 22
    gap = 3
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


def svg_sparkline(points, width=120, height=32, color=None):
    """7-day sparkline as inline SVG. Points is a list of floats."""
    if not points or len(points) < 2:
        return ""
    n = len(points)
    mn, mx = min(points), max(points)
    rng = mx - mn if mx != mn else 1
    pad = 2
    w = width - pad * 2
    h = height - pad * 2
    coords = []
    for i, p in enumerate(points):
        x = pad + (i / (n - 1)) * w
        y = pad + h - ((p - mn) / rng) * h
        coords.append(f"{x:.1f},{y:.1f}")
    polyline = " ".join(coords)
    if not color:
        color = "var(--green)" if points[-1] >= points[0] else "var(--red)"
    return f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" style="vertical-align:middle"><polyline points="{polyline}" fill="none" stroke="{color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>'


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
    order = ["BTC", "ETH", "SOL", "JTO", "BONK", "HYPE", "HNT", "ZEC"]
    for ticker in order:
        if ticker not in prices:
            continue
        d = prices[ticker]
        sparkline = svg_sparkline(d.get("sparkline_7d", []), width=100, height=28)
        price_cards += f'''<div class="card price-card">
  <div class="price-header">
    <div>
      <div class="price-name">{esc(ticker)} <span class="muted">{esc(d.get("name",""))}</span></div>
      <div class="price-val">${d["price"]:,.2f}</div>
      <div>{fmt_change(d.get("change_24h"))} <span class="muted" style="font-size:0.7rem">24h</span></div>
    </div>
    <div class="sparkline-wrap">{sparkline}</div>
  </div>
  <div class="price-7d">{fmt_change(d.get("change_7d"))} <span class="muted" style="font-size:0.7rem">7d</span></div>
</div>'''

    fg_val = fg.get("value", "N/A")
    fg_label = fg.get("label", "")
    fg_yesterday = fg.get("yesterday", "N/A")
    gauge = svg_gauge(fg_val if isinstance(fg_val, (int, float)) else 50, size=110)

    # SOL/BTC ratio
    sol_price = prices.get("SOL", {}).get("price", 0)
    btc_price = prices.get("BTC", {}).get("price", 0)
    sol_btc = sol_price / btc_price if btc_price else 0
    sol_btc_str = f"{sol_btc:.6f}" if sol_btc else "N/A"

    return f'''<div class="panel-section">
  <div class="market-top">
    <div class="market-stats-col">
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
        <div class="stat"><div class="stat-label">SOL/BTC</div>
          <div class="stat-value">{sol_btc_str}</div>
        </div>
      </div>
    </div>
    <div class="fg-hero">
      <div class="fg-gauge">{gauge}</div>
      <div class="fg-info">
        <div class="fg-label-text">{esc(str(fg_label))}</div>
        <div class="fg-yesterday">Yesterday: <strong>{fg_yesterday}</strong></div>
      </div>
    </div>
  </div>
  <div class="grid grid-prices">{price_cards}</div>
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
    youtube = news.get("youtube_videos", [])
    total = news.get("total_stories", len(all_news) + len(rss) + len(youtube))
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

    # YouTube videos
    yt_html = ""
    if youtube:
        yt_items = ""
        for v in youtube[:8]:
            vid = v.get("video_id", "")
            thumb = f'<img src="https://i.ytimg.com/vi/{vid}/mqdefault.jpg" style="width:120px;height:68px;border-radius:4px;object-fit:cover;flex-shrink:0" alt="">' if vid else ""
            yt_items += f'''<div style="display:flex;gap:10px;padding:8px 0;border-bottom:1px solid var(--border)">
  {thumb}
  <div>
    <a href="{esc(v.get("url","#"))}" target="_blank" rel="noopener" style="color:var(--text);text-decoration:none;font-size:0.84rem;line-height:1.3;display:block">{esc(v["title"])}</a>
    <span class="muted" style="font-size:0.7rem">{esc(v.get("channel",""))}</span>
  </div>
</div>'''
        yt_html = f'<h4 style="margin-top:20px">&#9654; Top Crypto YouTube</h4>{yt_items}'

    return f'''<div class="panel-section">
  <div class="section-badge">{total} stories</div>
  {items_html}
  {yt_html}
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
      <h4>Top 10 Chains by TVL {source_link("https://defillama.com/chains", "DeFiLlama")}</h4>
      <table><tr><th>Chain</th><th>TVL</th></tr>{rows}</table>
    </div>
    <div>
      <h4>Top 10 Chain TVL</h4>
      {chart}
    </div>
  </div>
  <h4 style="margin-top:20px">Trending on CoinGecko {source_link("https://www.coingecko.com/en/trending", "View All")}</h4>
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
      <div>{fmt_wow(wow, "fees_24h")}</div>
    </div>
  </div>
  <div class="stats-row">
    <div class="stat"><div class="stat-label">Stablecoins on Solana</div>
      <div class="stat-value">{fmt_usd(stables.get("total",0))}</div>
      <div>{fmt_wow(wow, "stables_tvl")}</div>
    </div>
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
      <h4>Top Spot DEXes{f" ({spot_cov}% of total)" if spot_cov else ""} {source_link("https://defillama.com/dexs/chain/Solana", "DeFiLlama")}</h4>
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
        staking_html = f'<h4 style="margin-top:16px">Staking Flows {source_link("https://defillama.com/protocols/Liquid%20Staking/Solana", "DeFiLlama")}</h4>'
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
        return ""

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

    # Hide entire panel if no data at all
    if not proto_html and not influ_html and not narr_html:
        return ""

    parts = []
    if proto_html:
        parts.append(f'<h4>Protocol Updates</h4>{proto_html}')
    if influ_html:
        parts.append(f'<h4 style="margin-top:16px">Influencer Takes</h4>{influ_html}')
    if narr_html:
        parts.append(f'<h4 style="margin-top:16px">Trending Narratives</h4>{narr_html}')

    return f'<div class="panel-section">{"".join(parts)}</div>'


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
  <h4 style="margin-top:24px">SIMDs — Solana Improvement Documents {source_link("https://github.com/solana-foundation/solana-improvement-documents/pulls", "GitHub")}</h4>
  {simd_stats}
  {simd_rows if simd_rows else '<p class="muted">No SIMD data available.</p>'}
  {f'<h4 style="margin-top:20px">Upgrade News</h4>{news_html}' if news_html else ''}
</div>'''


def build_defi_yields_panel(solana):
    """DeFi yields panel — top Solana lending/LP APYs."""
    yields = solana.get("defi_yields", {})
    if not yields or not yields.get("top_yields"):
        return ""

    summary = yields.get("summary", {})
    top_yields = yields.get("top_yields", [])
    stable_yields = yields.get("top_stablecoin_yields", [])

    # Summary stats
    summary_html = f'''<div class="stats-row">
  <div class="stat"><div class="stat-label">Total Solana DeFi TVL</div><div class="stat-value">{fmt_usd(summary.get("total_tvl", 0))}</div></div>
  <div class="stat"><div class="stat-label">Avg APY</div><div class="stat-value">{summary.get("avg_apy", 0):.1f}%</div></div>
  <div class="stat"><div class="stat-label">Best Stable APY</div><div class="stat-value" style="color:var(--green)">{summary.get("best_stable_apy", 0):.1f}%</div></div>
  <div class="stat"><div class="stat-label">Pools Tracked</div><div class="stat-value">{summary.get("total_pools", 0)}</div></div>
</div>'''

    # Top pools table
    pool_rows = ""
    for p in top_yields[:15]:
        apy = p.get("apy", 0) or 0
        apy_base = p.get("apyBase", 0) or 0
        apy_reward = p.get("apyReward", 0) or 0
        is_stable = "&#9679;" if p.get("stablecoin") else ""
        apy_color = "var(--green)" if apy > 5 else "var(--text)"
        pool_rows += f'''<tr>
  <td>{esc(p.get("project",""))}</td>
  <td>{esc(p.get("symbol",""))} {is_stable}</td>
  <td style="color:{apy_color};font-weight:600">{apy:.1f}%</td>
  <td class="muted">{apy_base:.1f}%</td>
  <td class="muted">{apy_reward:.1f}%</td>
  <td>{fmt_usd(p.get("tvlUsd", 0))}</td>
</tr>'''

    # Top stablecoin yields
    stable_rows = ""
    for p in stable_yields[:8]:
        apy = p.get("apy", 0) or 0
        stable_rows += f'''<tr>
  <td>{esc(p.get("project",""))}</td>
  <td>{esc(p.get("symbol",""))}</td>
  <td style="color:var(--green);font-weight:600">{apy:.1f}%</td>
  <td>{fmt_usd(p.get("tvlUsd", 0))}</td>
</tr>'''

    return f'''<div class="panel-section">
  {summary_html}
  <div class="grid grid-2" style="margin-top:16px">
    <div>
      <h4>Top Pools by TVL {source_link("https://defillama.com/yields?chain=Solana", "DeFiLlama")}</h4>
      <table><tr><th>Protocol</th><th>Pool</th><th>APY</th><th>Base</th><th>Reward</th><th>TVL</th></tr>{pool_rows}</table>
    </div>
    <div>
      <h4>Top Stablecoin Yields</h4>
      <table><tr><th>Protocol</th><th>Pool</th><th>APY</th><th>TVL</th></tr>{stable_rows}</table>
    </div>
  </div>
</div>'''


def build_tx_economics_panel(solana):
    """Transaction economics panel — fees, priority fees."""
    tx_econ = solana.get("tx_economics", {})
    if not tx_econ:
        return ""

    pf = tx_econ.get("priority_fees", {})
    # Hide if no meaningful data (all zeros or no samples)
    if not pf or (pf.get("median", 0) == 0 and pf.get("p90", 0) == 0 and pf.get("sample_count", 0) == 0):
        return ""

    base_sol = tx_econ.get("base_fee_sol", 0.000005)

    return f'''<div class="panel-section">
  <div class="section-sources">Source: <a href="https://defillama.com/fees/solana" target="_blank">DeFiLlama Fees</a></div>
  <div class="stats-row">
    <div class="stat"><div class="stat-label">Base Fee</div><div class="stat-value">{base_sol} SOL</div><div class="muted">5,000 lamports</div></div>
    <div class="stat"><div class="stat-label">Priority Fee (Median)</div><div class="stat-value">{pf.get("median", 0):,.0f}</div><div class="muted">micro-lamports</div></div>
    <div class="stat"><div class="stat-label">Priority Fee (P75)</div><div class="stat-value">{pf.get("p75", 0):,.0f}</div><div class="muted">micro-lamports</div></div>
    <div class="stat"><div class="stat-label">Priority Fee (P90)</div><div class="stat-value">{pf.get("p90", 0):,.0f}</div><div class="muted">micro-lamports</div></div>
  </div>
  <div class="muted" style="margin-top:8px">{pf.get("sample_count", 0)} recent slots sampled</div>
</div>'''


def build_competitive_panel(chain_tvls):
    """Competitive positioning — Solana market share vs other chains."""
    if not chain_tvls:
        return '<div class="panel-section"><p class="muted">No chain data available.</p></div>'

    total_tvl = sum(c.get("tvl", 0) for c in chain_tvls)
    if total_tvl == 0:
        return '<div class="panel-section"><p class="muted">No TVL data.</p></div>'

    rows = ""
    sol_share = 0
    for c in chain_tvls[:10]:
        tvl = c.get("tvl", 0)
        share = (tvl / total_tvl * 100) if total_tvl else 0
        bar_w = max(1, share)
        name = esc(c.get("name", ""))
        is_sol = name == "Solana"
        if is_sol:
            sol_share = share
        color = "var(--accent)" if is_sol else "var(--muted)"
        weight = "700" if is_sol else "400"
        rows += f'''<div style="margin-bottom:8px">
  <div style="display:flex;justify-content:space-between;font-size:0.82rem">
    <span style="font-weight:{weight};color:{'var(--accent)' if is_sol else 'var(--text)'}">{name}</span>
    <span><strong>{share:.1f}%</strong> <span class="muted">{fmt_usd(tvl)}</span></span>
  </div>
  <div style="background:var(--surface2);height:6px;border-radius:3px;overflow:hidden;margin-top:3px">
    <div style="background:{color};height:100%;width:{bar_w}%;border-radius:3px"></div>
  </div>
</div>'''

    return f'''<div class="panel-section">
  <div class="stats-row" style="margin-bottom:16px">
    <div class="stat"><div class="stat-label">Total Top-10 TVL</div><div class="stat-value">{fmt_usd(total_tvl)}</div></div>
    <div class="stat"><div class="stat-label">Solana Market Share</div><div class="stat-value" style="color:var(--accent)">{sol_share:.1f}%</div></div>
  </div>
  {rows}
</div>'''


def build_sectors_panel(solana):
    """Sector rotation and DePIN tracking."""
    sectors_data = solana.get("sectors", {})
    sectors = sectors_data.get("sectors", [])
    depin = sectors_data.get("depin", [])

    if not sectors:
        return ""

    # Sector table
    sector_rows = ""
    for s in sectors[:12]:
        chg = s.get("change_1d", 0)
        color = "var(--green)" if chg > 0 else "var(--red)" if chg < 0 else "var(--muted)"
        sector_rows += f'''<tr>
  <td style="font-weight:600">{esc(s["sector"])}</td>
  <td>{fmt_usd(s["tvl"])}</td>
  <td style="color:{color}">{chg:+.1f}%</td>
  <td class="muted">{s["protocol_count"]}</td>
  <td class="muted">{esc(s["top_protocol"])}</td>
</tr>'''

    # DePIN section
    depin_html = ""
    if depin:
        depin_rows = ""
        for d in depin[:8]:
            chg = d.get("change_1d", 0)
            chg7 = d.get("change_7d", 0)
            color = "var(--green)" if chg > 0 else "var(--red)" if chg < 0 else "var(--muted)"
            color7 = "var(--green)" if chg7 > 0 else "var(--red)" if chg7 < 0 else "var(--muted)"
            depin_rows += f'''<tr>
  <td style="font-weight:600">{esc(d["name"])}</td>
  <td class="muted">{esc(d.get("category",""))}</td>
  <td>{fmt_usd(d["tvl"])}</td>
  <td style="color:{color}">{chg:+.1f}%</td>
  <td style="color:{color7}">{chg7:+.1f}%</td>
</tr>'''
        depin_html = f'''<h4 style="margin-top:20px">DePIN &amp; Infrastructure</h4>
<table><tr><th>Protocol</th><th>Category</th><th>TVL</th><th>24h</th><th>7d</th></tr>{depin_rows}</table>'''

    return f'''<div class="panel-section">
  <h4>Sector Rotation (Solana TVL by Category) {source_link("https://defillama.com/categories", "DeFiLlama")}</h4>
  <table><tr><th>Sector</th><th>TVL</th><th>24h Chg</th><th>Protocols</th><th>Top Protocol</th></tr>{sector_rows}</table>
  {depin_html}
</div>'''


def build_signal_panel(signal):
    context = signal.get("market_context", "")
    divergences = signal.get("divergence_alerts", [])
    angles = signal.get("story_angles", [])
    key_rel = signal.get("key_data_relationships", "")

    # Hide entire panel if no signal data
    if not context and not divergences and not angles:
        return ""

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

    parts = []
    if context:
        parts.append(f'<h4>Market Context</h4><div class="signal-context">{esc(context)}</div>')
    if div_html:
        parts.append(f'<h4 style="margin-top:16px">&#9888; Divergence Alerts</h4>{div_html}')
    if angles_html:
        parts.append(f'<h4 style="margin-top:16px">&#128225; Story Angles</h4>{angles_html}')
    if key_rel:
        parts.append(f'<h4 style="margin-top:16px">&#9673; Key Data Relationships</h4><div class="signal-context">{esc(key_rel)}</div>')

    return f'<div class="panel-section">{"".join(parts)}</div>'


def build_pitches_panel(pitches):
    if not pitches:
        return ""
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
    if not tweets:
        return ""
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
        return ""
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
.header-right { display: flex; align-items: center; gap: 16px; }
.header-link { color: var(--muted); font-size: 0.8rem; font-weight: 600; text-decoration: none; text-transform: uppercase; letter-spacing: 0.5px; }
.header-link:hover { color: var(--accent); }
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
.nl-form { display: flex; gap: 6px; align-items: center; flex-shrink: 0; }
.nl-input {
  padding: 6px 12px; border: 1px solid var(--border); background: var(--bg);
  color: var(--text); font-size: 0.78rem; border-radius: 5px; outline: none; width: 180px;
}
.nl-input:focus { border-color: var(--accent); }
.nl-btn {
  background: var(--accent); color: #fff; padding: 6px 18px; border-radius: 5px;
  font-size: 0.8rem; font-weight: 600; border: none; cursor: pointer; white-space: nowrap;
}
.nl-btn:hover { opacity: 0.9; }

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
.wow { font-size: 0.78rem; font-weight: 600; }

/* Grid */
.grid { display: grid; gap: 12px; }
.grid-2 { grid-template-columns: 1fr 1fr; }
.grid-3 { grid-template-columns: 1fr 1fr 1fr; }
.grid-prices { grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 10px; }

/* Market top layout */
.market-top {
  display: flex; gap: 24px; align-items: flex-start; margin-bottom: 20px;
}
.market-stats-col { flex: 1; }
.fg-hero {
  flex-shrink: 0; display: flex; flex-direction: column; align-items: center;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 16px 24px; min-width: 160px;
}
.fg-hero .fg-label-text { font-weight: 700; font-size: 1rem; margin-top: 4px; }
.fg-hero .fg-yesterday { color: var(--muted); font-size: 0.78rem; margin-top: 2px; }
.fg-label-text { font-weight: 600; }

/* Cards */
.card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 14px;
}
.price-card .price-header { display: flex; justify-content: space-between; align-items: flex-start; }
.price-card .sparkline-wrap { flex-shrink: 0; margin-left: 8px; opacity: 0.85; }
.price-card .price-name { font-size: 0.8rem; font-weight: 600; }
.price-card .price-name .muted { font-weight: 400; font-size: 0.7rem; }
.price-card .price-val { font-size: 1.2rem; font-weight: 700; margin: 2px 0; }
.price-card .price-7d { margin-top: 4px; font-size: 0.78rem; }

/* Back to top */
.back-top {
  position: fixed; bottom: 24px; right: 24px; z-index: 200;
  width: 40px; height: 40px; border-radius: 50%;
  background: var(--accent); color: #fff; border: none; cursor: pointer;
  font-size: 1.1rem; display: none; align-items: center; justify-content: center;
  box-shadow: 0 2px 8px rgba(0,0,0,0.3); transition: opacity 0.2s;
}
.back-top:hover { opacity: 0.85; }
.back-top.visible { display: flex; }

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

/* Source links */
.section-sources { font-size: 0.7rem; margin-bottom: 12px; color: var(--muted); }
.section-sources a { color: var(--muted); text-decoration: none; }
.section-sources a:hover { color: var(--accent); }
.source-link { font-size: 0.7rem; color: var(--muted); text-decoration: none; font-weight: 400; text-transform: none; letter-spacing: 0; margin-left: 8px; }
.source-link:hover { color: var(--accent); }

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
  .market-top { flex-direction: column; }
  .fg-hero { width: 100%; flex-direction: row; gap: 16px; justify-content: center; }
  .back-top { bottom: 16px; right: 16px; }
}
"""

# ---------------------------------------------------------------------------
# JS
# ---------------------------------------------------------------------------

JS = """
// Kit newsletter subscribe
function dashSubscribe(e) {
  e.preventDefault();
  var email = document.getElementById('dash-nl-email').value;
  var msg = document.getElementById('dash-nl-msg');
  var btn = document.querySelector('.nl-btn');
  btn.disabled = true; btn.textContent = '...';
  fetch('https://api.convertkit.com/v3/forms/9240695/subscribe', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({api_key: 'PvJNzxZ5JOLRr4pA0Mps2w', email: email})
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    msg.style.display = 'inline';
    if (data.subscription) {
      msg.style.color = 'var(--green)';
      msg.textContent = "You're in!";
      document.getElementById('dash-nl-email').value = '';
    } else {
      msg.style.color = '#f97316';
      msg.textContent = data.message || 'Try again.';
    }
    btn.disabled = false; btn.textContent = 'Subscribe';
  })
  .catch(function() {
    msg.style.display = 'inline';
    msg.style.color = 'var(--red)';
    msg.textContent = 'Error. Try again.';
    btn.disabled = false; btn.textContent = 'Subscribe';
  });
  return false;
}

// Scroll-spy: highlight active section in nav
(function() {
  var links = document.querySelectorAll('.section-nav a');
  var sections = [];
  for (var i = 0; i < links.length; i++) {
    var id = links[i].getAttribute('href').slice(1);
    var el = document.getElementById(id);
    if (el) sections.push({el: el, link: links[i]});
  }
  var backTop = document.getElementById('back-top');
  function onScroll() {
    var scrollY = window.scrollY + 80;
    var active = sections[0];
    for (var i = 0; i < sections.length; i++) {
      if (sections[i].el.offsetTop <= scrollY) active = sections[i];
    }
    for (var i = 0; i < sections.length; i++) {
      sections[i].link.classList.toggle('active', sections[i] === active);
    }
    if (backTop) backTop.classList.toggle('visible', window.scrollY > 600);
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
    sol_btc = sol_price / btc_price if btc_price else 0

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
  <a href="https://skrmaxing.com" target="_blank">SKR Maxing</a>
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
  <div class="ticker">
    <div class="t-label">SOL/BTC</div>
    <div class="t-value">{f"{sol_btc:.6f}" if sol_btc else "N/A"}</div>
    <div class="t-change">&nbsp;</div>
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
  var btn = document.querySelector('.hp-email-btn');
  btn.disabled = true;
  btn.textContent = 'Subscribing...';
  fetch('https://api.convertkit.com/v3/forms/9240695/subscribe', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{
      api_key: 'PvJNzxZ5JOLRr4pA0Mps2w',
      email: email
    }})
  }})
  .then(function(r) {{ return r.json(); }})
  .then(function(data) {{
    msg.style.display = 'block';
    if (data.subscription) {{
      msg.style.color = '#22c55e';
      msg.textContent = 'You\\'re in! Daily Solana intelligence, every morning.';
      document.getElementById('newsletter-email').value = '';
    }} else {{
      msg.style.color = '#f97316';
      msg.textContent = data.message || 'Something went wrong. Try again.';
    }}
    btn.disabled = false;
    btn.textContent = 'Subscribe';
  }})
  .catch(function() {{
    msg.style.display = 'block';
    msg.style.color = '#ef4444';
    msg.textContent = 'Network error. Please try again.';
    btn.disabled = false;
    btn.textContent = 'Subscribe';
  }});
  return false;
}}
</script>

</body>
</html>'''


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def _section_if(panel_html: str, section_id: str, title: str) -> str:
    """Wrap panel HTML in a dash-section div, or return empty string if panel is empty."""
    if not panel_html or not panel_html.strip():
        return ""
    return f'''<div class="dash-section" id="{section_id}">
  <div class="section-title">{title}</div>
  {panel_html}
</div>'''


def _build_intelligence_section(whales: dict, narrative: dict) -> str:
    """Build the Intelligence section, hiding sub-panels that have no data."""
    whale_html = build_whale_panel(whales)
    mm_html = build_market_makers_panel(narrative)
    xpulse_html = build_xpulse_panel(narrative)

    # If nothing has data, hide the whole section
    has_whales = bool(whales.get("whale_news") or whales.get("staking_flows", {}).get("protocols"))
    has_mm = bool(mm_html)
    has_xpulse = bool(xpulse_html)

    if not has_whales and not has_mm and not has_xpulse:
        return ""

    cols = []
    if has_whales:
        cols.append(f'<div><h4>Whales &amp; Staking</h4>{whale_html}</div>')
    if has_mm:
        cols.append(f'<div><h4>Market Makers</h4>{mm_html}</div>')

    cols_html = f'<div class="section-cols">{"".join(cols)}</div>' if cols else ""
    xpulse_section = f'<div style="margin-top:20px"><h4>X Pulse</h4>{xpulse_html}</div>' if has_xpulse else ""

    return f'''<div class="dash-section" id="intelligence">
  <div class="section-title">Intelligence</div>
  {cols_html}
  {xpulse_section}
</div>'''


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

    # Data freshness check — warn if market data is >6 hours old
    market_ts = market.get("timestamp", "")
    stale_badge = ""
    try:
        from datetime import datetime, timezone
        gen_dt = datetime.strptime(market_ts[:16], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - gen_dt).total_seconds() / 3600
        if age_hours > 6:
            stale_badge = f' <span style="color:var(--red);font-size:0.7rem;font-weight:600">DATA {int(age_hours)}H OLD</span>'
    except Exception:
        pass

    # Build all sections; skip empty ones
    # Each entry: (id, nav_label, html)
    sections = []

    sections.append(("market", "Market", f'''<div class="dash-section" id="market">
  <div class="section-title">Market Overview</div>
  <div class="section-sources">Sources: <a href="https://www.coingecko.com" target="_blank">CoinGecko</a> &middot; <a href="https://alternative.me/crypto/fear-and-greed-index/" target="_blank">Fear &amp; Greed Index</a></div>
  {build_market_panel(prices, global_data, fg, wow)}
</div>'''))

    sections.append(("technical", "Technical", f'''<div class="dash-section" id="technical">
  <div class="section-title">SOL Technical Analysis</div>
  <div class="section-sources">Source: <a href="https://www.coingecko.com/en/coins/solana" target="_blank">CoinGecko</a></div>
  {build_technical_panel(technicals, monthly_returns)}
</div>'''))

    sections.append(("solana", "Solana", f'''<div class="dash-section" id="solana">
  <div class="section-title">Solana Ecosystem</div>
  <div class="section-sources">Sources: <a href="https://defillama.com/chain/Solana" target="_blank">DeFiLlama</a> &middot; <a href="https://solscan.io" target="_blank">Solscan</a> &middot; <a href="https://defillama.com/stablecoins/Solana" target="_blank">Stablecoins</a></div>
  {build_solana_panel(solana, wow)}
</div>'''))

    sections.append(("dex", "DEX", f'''<div class="dash-section" id="dex">
  <div class="section-title">DEX Volume</div>
  <div class="section-sources">Source: <a href="https://defillama.com/dexs/chain/Solana" target="_blank">DeFiLlama DEXs</a></div>
  {build_dex_panel(dex)}
</div>'''))

    sections.append(("protocols", "Protocols", f'''<div class="dash-section" id="protocols">
  <div class="section-title">Top Protocols</div>
  <div class="section-sources">Source: <a href="https://defillama.com/chain/Solana" target="_blank">DeFiLlama</a></div>
  {build_protocols_panel(protocols)}
</div>'''))

    yields_panel = build_defi_yields_panel(solana)
    if yields_panel:
        sections.append(("yields", "Yields", f'''<div class="dash-section" id="yields">
  <div class="section-title">DeFi Yields</div>
  <div class="section-sources">Source: <a href="https://defillama.com/yields?chain=Solana" target="_blank">DeFiLlama Yields</a></div>
  {yields_panel}
</div>'''))

    sectors_panel = build_sectors_panel(solana)
    if sectors_panel:
        sections.append(("sectors", "Sectors", f'''<div class="dash-section" id="sectors">
  <div class="section-title">Sectors &amp; DePIN</div>
  <div class="section-sources">Sources: <a href="https://defillama.com/categories" target="_blank">DeFiLlama Categories</a> &middot; <a href="https://defillama.com/protocols/DePin" target="_blank">DePIN</a></div>
  {sectors_panel}
</div>'''))

    sections.append(("upgrades", "Upgrades", f'''<div class="dash-section" id="upgrades">
  <div class="section-title">Network Upgrades</div>
  <div class="section-sources">Sources: <a href="https://github.com/solana-foundation/solana-improvement-documents/pulls" target="_blank">SIMDs (GitHub)</a> &middot; <a href="https://www.validators.app" target="_blank">Validators.app</a></div>
  {build_upgrades_panel(upgrades)}
</div>'''))

    tx_econ_html = _section_if(build_tx_economics_panel(solana), "tx-econ", "Transaction Economics")
    if tx_econ_html:
        sections.append(("tx-econ", "Fees", tx_econ_html))

    signal_html = _section_if(build_signal_panel(signal), "signal", "The Signal")
    if signal_html:
        sections.append(("signal", "Signal", signal_html))

    intel_html = _build_intelligence_section(whales, narrative)
    if intel_html:
        sections.append(("intelligence", "Intel", intel_html))

    trending_html = ''.join(f'<span class="trending-tag">#{t.get("market_cap_rank","?")} <strong>{esc(t.get("symbol","?"))}</strong></span>' for t in trending[:10])
    sections.append(("competitive", "Share", f'''<div class="dash-section" id="competitive">
  <div class="section-title">Chain Market Share</div>
  <div class="section-sources">Sources: <a href="https://defillama.com/chains" target="_blank">DeFiLlama Chains</a> &middot; <a href="https://www.coingecko.com/en/trending" target="_blank">CoinGecko Trending</a></div>
  {build_competitive_panel(chain_tvls)}
  <div style="margin-top:20px">
    <h4>Trending on CoinGecko {source_link("https://www.coingecko.com/en/trending", "View All")}</h4>
    <div class="trending-row">{trending_html}</div>
  </div>
</div>'''))

    sections.append(("news", "News", f'''<div class="dash-section" id="news">
  <div class="section-title">News Feed</div>
  {build_news_panel(news)}
</div>'''))

    # Build nav dynamically from active sections
    nav_links = "".join(f'<a href="#{sid}">{label}</a>' for sid, label, _ in sections)
    body_sections = "\n".join(html for _, _, html in sections)

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

<div class="header">
  <div class="header-left">
    <h1><a href="/">SOLANA WEEKLY</a></h1>
    <div class="meta">Updated: {esc(generated_at)}{f" &middot; Run #{run_num}" if run_num else ""}{stale_badge}</div>
  </div>
  <div class="header-right">
    <a href="https://solanaweekly.fun" target="_blank" rel="noopener" class="header-link">Podcast</a>
    <div class="fg-mini">
      {gauge_mini}
      <div class="fg-label-text">{esc(str(fg_label))}</div>
    </div>
  </div>
</div>

<nav class="section-nav">{nav_links}</nav>

<div class="nl-banner">
  <div class="nl-text"><strong>Get daily Solana intelligence in your inbox.</strong> Key numbers + what they mean, every morning.</div>
  <form class="nl-form" id="dash-nl-form" onsubmit="return dashSubscribe(event)">
    <input type="email" class="nl-input" placeholder="your@email.com" required id="dash-nl-email">
    <button type="submit" class="nl-btn">Subscribe</button>
  </form>
  <div id="dash-nl-msg" style="font-size:0.78rem;display:none"></div>
</div>

{body_sections}

<footer>
  <a href="/">solanaweekly.io</a> &middot; Daily Solana Intelligence &middot; {esc(generated_at)}
</footer>

<button class="back-top" id="back-top" onclick="window.scrollTo({{top:0,behavior:'smooth'}})" title="Back to top">&#9650;</button>

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
