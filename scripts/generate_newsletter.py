"""Generate a newsletter email draft from compiled dashboard data.

The email is the product's most visible surface. Every unused metric in
compiled.json that could belong in the email is a missed opportunity.

Produces JSON with subject, preheader, text_body, html_body, and a
`ready` flag so deliver_newsletter can refuse to send a broken draft.

The editorial hook comes from `narrative.newsletter_tldr` (a dedicated
Claude-generated field). If the narrative is missing or errored we
mark the newsletter `ready=False` and bail — never ship debug strings.

Content sections:
    Subject line   — dynamic lead (biggest signal of the day)
    Preheader      — Gmail preview text (hidden div in HTML)
    TL;DR          — one-line editorial hook from Claude
    The Numbers    — SOL/BTC/ETH + F&G + market cap + SOL TVL + DEX vol
    Top Mover      — biggest 24h % change in watchlist
    Solana Movers  — top 3 protocols by 1d TVL change
    Sector Rotation — one-line: which sector is flowing in/out
    Hyperliquid    — new listings alert (only if any)
    Best Stable APY — highest stablecoin yield on Solana today
    The Trade      — today's sector + coin pick from trade_thesis
    Watch          — top divergence alert
    CTA            — dashboard link
Footer         — Kit merge tags for unsubscribe
"""

from __future__ import annotations

import html as html_mod
from datetime import datetime, timezone
from config import load_json, save_json, get_logger, now_utc
from utils import esc, fmt_usd

log = get_logger("newsletter")

DASHBOARD_URL = "https://solanaweekly.io/dashboard"
SITE_URL = "https://solanaweekly.io"

# Kit merge tags — resolved per-subscriber at broadcast send time
KIT_FIRST_NAME = "{{ subscriber.first_name | default: \"there\" }}"
KIT_UNSUBSCRIBE = "{{ unsubscribe_url }}"

# Brand colors
PURPLE = "#9333ea"
PURPLE_LIGHT = "#a855f7"
CYAN = "#06b6d4"
DARK_BG = "#0f0b1a"
CARD_BG = "#1a1425"
BORDER_COLOR = "#2d2640"
TEXT_PRIMARY = "#f0edf5"
TEXT_MUTED = "#9ca3af"
GREEN = "#22c55e"
RED = "#ef4444"


# ---------------------------------------------------------------------------
# Small formatting helpers
# ---------------------------------------------------------------------------

def fmt_price(value):
    if value is None:
        return "$?"
    if value >= 100:
        return f"${value:,.0f}"
    if value >= 1:
        return f"${value:,.2f}"
    if value >= 0.01:
        return f"${value:.4f}"
    return f"${value:.6f}"


def fmt_change(value, suffix="%"):
    if value is None:
        return "n/a"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}{suffix}"


def change_color(value):
    if value is None or value == 0:
        return TEXT_MUTED
    return GREEN if value > 0 else RED


def today_short():
    """Return today's date formatted like 'Mar 24'."""
    return datetime.now(timezone.utc).strftime("%b %d").replace(" 0", " ")


# ---------------------------------------------------------------------------
# Data extraction — compute each content block from compiled + narrative
# ---------------------------------------------------------------------------

def extract_top_mover(prices: dict) -> dict | None:
    """Biggest absolute 24h move among watchlist tickers."""
    best = None
    best_abs = 0
    for ticker, d in prices.items():
        chg = d.get("change_24h")
        if chg is None:
            continue
        if abs(chg) > best_abs:
            best_abs = abs(chg)
            best = {
                "ticker": ticker,
                "price": d.get("price"),
                "change_24h": chg,
            }
    return best


def extract_protocol_movers(protocols: list, n: int = 3) -> list:
    """Top N Solana protocols by 1d TVL change (require TVL > $10M to filter noise)."""
    filtered = [
        p for p in protocols
        if (p.get("tvl") or 0) >= 10_000_000 and p.get("change_1d") is not None
    ]
    filtered.sort(key=lambda p: abs(p.get("change_1d") or 0), reverse=True)
    return filtered[:n]


def extract_sector_rotation(solana: dict) -> str | None:
    """Return a one-line sector rotation summary, or None if no data."""
    sectors_data = solana.get("sectors", {}) or {}
    sectors = sectors_data.get("sectors", []) or []
    if not sectors:
        return None

    scored = [s for s in sectors if s.get("change_1d") is not None]
    if not scored:
        return None

    scored.sort(key=lambda s: s.get("change_1d") or 0, reverse=True)
    top = scored[0]
    bottom = scored[-1]

    if abs(top.get("change_1d", 0) - bottom.get("change_1d", 0)) < 0.5:
        # Rotation isn't meaningful — skip the section
        return None

    return (
        f"{top['sector']} leads at {fmt_change(top.get('change_1d'))}, "
        f"{bottom['sector']} lags at {fmt_change(bottom.get('change_1d'))}"
    )


def extract_hl_listings(hyperliquid: dict) -> list:
    """New Hyperliquid listings since last run. Only returns if not first run."""
    listings = hyperliquid.get("new_listings", {}) or {}
    if listings.get("first_run"):
        return []
    return listings.get("added", []) or []


def extract_best_stable_apy(solana: dict) -> dict | None:
    """Highest-yielding stablecoin pool with >=$1M TVL."""
    yields = solana.get("defi_yields", {}) or {}
    stables = yields.get("top_stablecoin_yields", []) or []
    # Filter to reasonable TVL to avoid ghost pools
    eligible = [
        p for p in stables
        if (p.get("tvlUsd") or 0) >= 1_000_000 and p.get("apy") is not None
    ]
    if not eligible:
        return None
    eligible.sort(key=lambda p: p.get("apy") or 0, reverse=True)
    return eligible[0]


def extract_trade_pick(narrative: dict) -> dict | None:
    """Today's sector + top coin from trade_thesis."""
    thesis = narrative.get("trade_thesis", {}) or {}
    coins = thesis.get("coins", []) or []
    if not thesis.get("sector") or not coins:
        return None
    return {
        "sector": thesis.get("sector"),
        "conviction": thesis.get("conviction", "Moderate"),
        "top_coin": coins[0],
    }


def extract_names_to_watch(compiled: dict, narrative: dict, n: int = 5) -> list:
    """Compact cross-signal watchlist for the email."""
    market = compiled.get("market", {}) or {}
    solana = compiled.get("solana", {}) or {}
    hyperliquid = compiled.get("hyperliquid", {}) or {}
    stocks = compiled.get("stocks", {}) or {}
    prices = market.get("prices", {}) or {}
    candidates = {}

    def add(symbol, source, score, detail):
        symbol = (symbol or "").upper().strip()
        if not symbol or symbol in {"?", "USD", "USDC", "USDT"}:
            return
        item = candidates.setdefault(symbol, {"symbol": symbol, "score": 0.0, "sources": [], "details": []})
        item["score"] += score
        if source not in item["sources"]:
            item["sources"].append(source)
        if detail and detail not in item["details"]:
            item["details"].append(detail)

    for ticker, data in prices.items():
        chg = data.get("change_24h")
        if chg is not None and abs(chg) >= 2:
            add(ticker, "price", min(abs(chg) * 1.8, 20), f"{fmt_change(chg)} 24h")
        if ticker in {"SOL", "ONDO", "HYPE"}:
            add(ticker, "core", 5, "core watchlist")

    for rank, coin in enumerate((hyperliquid.get("top_coins") or [])[:10], 1):
        vol = coin.get("day_ntl_vlm", 0) or 0
        add(coin.get("name"), "HL flow", max(15 - rank, 0) + min(vol / 250_000_000, 10), f"#{rank} HL volume, {fmt_usd(vol)}")

    for coin in (hyperliquid.get("top_movers") or {}).get("gainers", [])[:4]:
        add(coin.get("name"), "breakout", min(abs(coin.get("change_24h") or 0), 18), f"{fmt_change(coin.get('change_24h'))} on {fmt_usd(coin.get('day_ntl_vlm', 0) or 0)} vol")

    for c in (hyperliquid.get("stock_perps") or [])[:8]:
        add(c.get("name"), "stock perp", min(abs(c.get("change_24h") or 0) + (c.get("day_ntl_vlm", 0) or 0) / 5_000_000, 22), f"{fmt_change(c.get('change_24h'))}, {fmt_usd(c.get('day_ntl_vlm', 0) or 0)} vol")

    for bucket, source in (("xstocks", "xStocks"), ("prestocks", "PreStocks")):
        for t in (stocks.get(bucket) or [])[:6]:
            add(t.get("symbol"), source, min(abs(t.get("change_24h") or 0) + (t.get("volume_24h", 0) or 0) / 100_000, 18), f"{fmt_change(t.get('change_24h'))}, {fmt_usd(t.get('volume_24h', 0) or 0)} vol")

    for d in (solana.get("dex_volumes", {}) or {}).get("top_spot", [])[:5]:
        if (d.get("change_1d") is not None and abs(d.get("change_1d") or 0) >= 5) or (d.get("volume_24h", 0) or 0) >= 250_000_000:
            add(d.get("name"), "DEX flow", min(abs(d.get("change_1d") or 0) + (d.get("volume_24h", 0) or 0) / 100_000_000, 18), f"{fmt_usd(d.get('volume_24h', 0) or 0)} DEX vol")

    trade = extract_trade_pick(narrative)
    if trade:
        coin = trade.get("top_coin", {})
        add(coin.get("ticker"), "trade", 22, coin.get("reason", "trade setup"))

    return sorted(candidates.values(), key=lambda item: item["score"], reverse=True)[:n]


def extract_dex_flow(compiled: dict, n: int = 4) -> list:
    dex = ((compiled.get("solana", {}) or {}).get("dex_volumes", {}) or {})
    rows = [d for d in (dex.get("top_spot") or []) if d.get("volume_24h", 0) > 0]
    rows.sort(key=lambda d: (d.get("volume_24h", 0), d.get("change_1d") or 0), reverse=True)
    total = dex.get("spot_24h", 0) or 0
    result = []
    for d in rows[:n]:
        share = (d.get("volume_24h", 0) or 0) / total * 100 if total else None
        result.append({**d, "share": share})
    return result


def extract_treasury_signal(compiled: dict) -> dict | None:
    btc = ((compiled.get("treasuries", {}) or {}).get("btc", {}) or {})
    strategy = btc.get("strategy", {}) or {}
    if not btc and not strategy:
        return None
    return {
        "total_holdings_btc": btc.get("total_holdings_btc", 0) or 0,
        "tracked_entities": btc.get("tracked_entities", 0) or 0,
        "strategy_holdings_btc": strategy.get("holdings_btc", 0) or 0,
        "strategy_value_usd": strategy.get("current_value_usd", 0) or 0,
        "strategy_share": strategy.get("share_of_tracked_treasury_btc", 0) or 0,
    }


# ---------------------------------------------------------------------------
# Subject line — dynamic lead based on biggest signal of the day
# ---------------------------------------------------------------------------

def build_subject(compiled: dict, narrative: dict, wow: dict, blocks: dict) -> str:
    """Rotate the subject line based on the biggest signal available.

    Priority order (first match wins):
      1. New Hyperliquid listings        — most interesting + rare
      2. Big F&G shift (>=5 points)      — sentiment flips move markets
      3. Watchlist top mover >6% (24h)   — real price action
      4. DEX volume WoW >20%             — on-chain regime change
      5. SOL 24h change >3%              — ordinary bread-and-butter
      6. Fallback: SOL price + F&G        — always works
    """
    date_str = today_short()
    market = compiled.get("market", {})
    prices = market.get("prices", {})
    fg = market.get("fear_greed", {})

    # 1. New HL listings
    hl_added = blocks.get("hl_listings") or []
    if hl_added:
        n = len(hl_added)
        if n <= 2:
            tag = " + ".join(hl_added)
        else:
            tag = f"{hl_added[0]} + {n - 1} more"
        return f"New on Hyperliquid: {tag} — {date_str}"

    # 2. Big F&G shift
    fg_val = fg.get("value")
    fg_yest = fg.get("yesterday")
    if isinstance(fg_val, (int, float)) and isinstance(fg_yest, (int, float)):
        delta = fg_val - fg_yest
        if abs(delta) >= 5:
            direction = "surges" if delta > 0 else "drops"
            return f"Fear & Greed {direction} to {fg_val} ({delta:+.0f}) — {date_str}"

    # 3. Watchlist top mover
    mover = blocks.get("top_mover")
    if mover and abs(mover.get("change_24h") or 0) >= 6:
        direction = "up" if mover["change_24h"] > 0 else "down"
        return f"{mover['ticker']} {direction} {abs(mover['change_24h']):.1f}% — {date_str}"

    # 4. DEX volume WoW
    dex_wow = wow.get("dex_volume")
    if dex_wow is not None and abs(dex_wow) >= 20:
        direction = "up" if dex_wow > 0 else "down"
        return f"Solana DEX volume {direction} {abs(dex_wow):.0f}% WoW — {date_str}"

    # 5. SOL 24h
    sol_chg = prices.get("SOL", {}).get("change_24h")
    if sol_chg is not None and abs(sol_chg) >= 3:
        direction = "up" if sol_chg > 0 else "down"
        sol_price = prices.get("SOL", {}).get("price")
        price_str = fmt_price(sol_price) if sol_price else "?"
        return f"SOL {direction} {abs(sol_chg):.1f}% to {price_str} — {date_str}"

    # 6. Fallback
    sol_price = prices.get("SOL", {}).get("price")
    price_str = fmt_price(sol_price) if sol_price else "?"
    fg_str = f"F&G {fg_val}" if isinstance(fg_val, (int, float)) else "Solana Weekly"
    return f"SOL {price_str} | {fg_str} — {date_str}"


# ---------------------------------------------------------------------------
# Content block precomputation
# ---------------------------------------------------------------------------

def compute_blocks(compiled: dict, narrative: dict) -> dict:
    """Compute all dynamic content blocks once so subject + bodies stay in sync."""
    market = compiled.get("market", {})
    solana = compiled.get("solana", {})
    hyperliquid = compiled.get("hyperliquid", {})

    prices = market.get("prices", {})
    protocol_rankings = solana.get("protocol_rankings", []) or []

    return {
        "top_mover": extract_top_mover(prices),
        "names_to_watch": extract_names_to_watch(compiled, narrative),
        "dex_flow": extract_dex_flow(compiled),
        "treasury_signal": extract_treasury_signal(compiled),
        "protocol_movers": extract_protocol_movers(protocol_rankings, n=3),
        "sector_rotation": extract_sector_rotation(solana),
        "hl_listings": extract_hl_listings(hyperliquid),
        "best_stable_apy": extract_best_stable_apy(solana),
        "trade_pick": extract_trade_pick(narrative),
    }


# ---------------------------------------------------------------------------
# Narrative TLDR + preheader — with graceful fallback
# ---------------------------------------------------------------------------

def get_tldr(narrative: dict, compiled: dict, blocks: dict) -> str:
    """Use Claude's `newsletter_tldr`, or synthesize one from the strongest signal."""
    tldr = (narrative.get("newsletter_tldr") or "").strip()
    if tldr:
        return tldr

    # Fallback: hand-craft a one-liner from the biggest block
    hl = blocks.get("hl_listings")
    if hl:
        return f"{len(hl)} new listing(s) on Hyperliquid: {', '.join(hl[:3])}. Volume mix is shifting — here's the rest of the tape."

    mover = blocks.get("top_mover")
    if mover and abs(mover.get("change_24h") or 0) >= 5:
        direction = "up" if mover["change_24h"] > 0 else "down"
        return f"{mover['ticker']} leads the tape {direction} {abs(mover['change_24h']):.1f}% today. Here's what the rest of the Solana data is telling us."

    sector = blocks.get("sector_rotation")
    if sector:
        return f"Sector rotation is the story today — {sector}. Full breakdown below."

    fg = compiled.get("market", {}).get("fear_greed", {})
    fg_label = fg.get("label", "Neutral")
    return f"Market sitting at {fg_label} — here's what the data says about Solana today."


def get_preheader(narrative: dict, blocks: dict) -> str:
    """Gmail preview text — Claude-generated or synthesized."""
    pre = (narrative.get("newsletter_preheader") or "").strip()
    if pre:
        return pre[:90]

    # Fallback: tease the most interesting block
    if blocks.get("hl_listings"):
        return "New Hyperliquid listings, top movers, and the trade setup inside."
    if blocks.get("trade_pick"):
        sector = blocks["trade_pick"]["sector"]
        return f"Today's setup: {sector} rotation + top movers + key yields."
    return "Top movers, sector rotation, and the Solana tape in 2 minutes."


# ---------------------------------------------------------------------------
# Plain text body
# ---------------------------------------------------------------------------

def build_text_body(compiled: dict, narrative: dict, wow: dict, blocks: dict) -> str:
    market = compiled.get("market", {})
    solana = compiled.get("solana", {})
    prices = market.get("prices", {})
    fg = market.get("fear_greed", {})
    global_data = market.get("global", {})
    dex = solana.get("dex_volumes", {})
    sol_tvl = solana.get("solana_tvl", {})
    signal = narrative.get("the_signal", {})

    tldr = get_tldr(narrative, compiled, blocks)

    lines = [
        f"Hey {{{{ subscriber.first_name | default: 'there' }}}},",
        "",
        tldr,
        "",
        "THE NUMBERS",
        "-" * 30,
    ]

    sol = prices.get("SOL", {})
    btc = prices.get("BTC", {})
    eth = prices.get("ETH", {})

    if sol.get("price"):
        wow_sol = wow.get("sol_price")
        wow_str = f" (WoW {fmt_change(wow_sol)})" if wow_sol is not None else ""
        lines.append(f"  SOL: {fmt_price(sol['price'])} ({fmt_change(sol.get('change_24h'))} 24h){wow_str}")
    if btc.get("price"):
        lines.append(f"  BTC: {fmt_price(btc['price'])} ({fmt_change(btc.get('change_24h'))} 24h)")
    if eth.get("price"):
        lines.append(f"  ETH: {fmt_price(eth['price'])} ({fmt_change(eth.get('change_24h'))} 24h)")

    lines.append("")
    if fg.get("value") is not None:
        lines.append(f"  Fear & Greed: {fg['value']} -- {fg.get('label', '')} (was {fg.get('yesterday', '?')})")

    total_mcap = global_data.get("total_market_cap", 0)
    if total_mcap:
        wow_mcap = wow.get("total_market_cap")
        wow_str = f" | WoW {fmt_change(wow_mcap)}" if wow_mcap is not None else ""
        lines.append(f"  Total Market Cap: {fmt_usd(total_mcap)}{wow_str}")

    tvl_current = sol_tvl.get("current", 0)
    if tvl_current:
        wow_tvl = wow.get("sol_tvl")
        wow_str = f" | WoW {fmt_change(wow_tvl)}" if wow_tvl is not None else ""
        lines.append(f"  Solana TVL: {fmt_usd(tvl_current)}{wow_str}")

    combined_dex = dex.get("combined_24h", 0)
    if combined_dex:
        wow_dex = wow.get("dex_volume")
        wow_str = f" | WoW {fmt_change(wow_dex)}" if wow_dex is not None else ""
        lines.append(f"  DEX Volume (24h): {fmt_usd(combined_dex)}{wow_str}")

    # Top mover
    mover = blocks.get("top_mover")
    if mover:
        lines.append("")
        lines.append("TOP MOVER (24h)")
        lines.append("-" * 30)
        lines.append(f"  {mover['ticker']}: {fmt_price(mover.get('price'))} ({fmt_change(mover.get('change_24h'))})")

    names = blocks.get("names_to_watch") or []
    if names:
        lines.append("")
        lines.append("NAMES TO WATCH")
        lines.append("-" * 30)
        for item in names[:5]:
            detail = item["details"][0] if item.get("details") else ", ".join(item.get("sources", []))
            lines.append(f"  {item['symbol']}: {detail}")

    dex_flow = blocks.get("dex_flow") or []
    if dex_flow:
        lines.append("")
        lines.append("SOLANA DEX FLOW")
        lines.append("-" * 30)
        for d in dex_flow:
            share = f", {d['share']:.1f}% share" if d.get("share") is not None else ""
            lines.append(f"  {d['name']}: {fmt_usd(d.get('volume_24h', 0))} ({fmt_change(d.get('change_1d'))} 1d{share})")

    treasury = blocks.get("treasury_signal")
    if treasury:
        lines.append("")
        lines.append("BTC TREASURY BID")
        lines.append("-" * 30)
        lines.append(
            f"  Strategy/MSTR: {treasury.get('strategy_holdings_btc', 0):,.0f} BTC "
            f"({treasury.get('strategy_share', 0)}% of tracked treasury BTC)"
        )
        lines.append(
            f"  Total tracked treasury BTC: {treasury.get('total_holdings_btc', 0):,.0f} "
            f"across {treasury.get('tracked_entities', 0)} entities"
        )

    # Solana protocol movers
    movers = blocks.get("protocol_movers") or []
    if movers:
        lines.append("")
        lines.append("SOLANA MOVERS (1d TVL)")
        lines.append("-" * 30)
        for p in movers:
            lines.append(f"  {p['name']} ({p.get('category','')}): {fmt_usd(p.get('tvl',0))} ({fmt_change(p.get('change_1d'))})")

    # Sector rotation
    if blocks.get("sector_rotation"):
        lines.append("")
        lines.append("SECTOR ROTATION")
        lines.append("-" * 30)
        lines.append(f"  {blocks['sector_rotation']}")

    # Hyperliquid new listings
    hl_added = blocks.get("hl_listings") or []
    if hl_added:
        lines.append("")
        lines.append("NEW ON HYPERLIQUID")
        lines.append("-" * 30)
        lines.append(f"  {len(hl_added)} new listing(s): {', '.join(hl_added)}")

    # Best stablecoin yield
    best_yield = blocks.get("best_stable_apy")
    if best_yield:
        lines.append("")
        lines.append("BEST STABLE YIELD TODAY")
        lines.append("-" * 30)
        lines.append(
            f"  {best_yield.get('project','?')} — {best_yield.get('symbol','?')}: "
            f"{(best_yield.get('apy') or 0):.1f}% APY "
            f"(TVL {fmt_usd(best_yield.get('tvlUsd',0))})"
        )

    # Trade pick
    trade = blocks.get("trade_pick")
    if trade:
        coin = trade["top_coin"]
        lines.append("")
        lines.append(f"SO, WHAT'S THE TRADE? ({trade.get('conviction')})")
        lines.append("-" * 30)
        lines.append(f"  Sector: {trade.get('sector')}")
        lines.append(f"  Pick: {coin.get('ticker','?')} — {coin.get('reason','')}")

    # One thing to watch
    lines.append("")
    lines.append("ONE THING TO WATCH")
    lines.append("-" * 30)
    divergences = signal.get("divergence_alerts", []) if signal else []
    if divergences:
        top = divergences[0]
        lines.append(f"  {top.get('title', 'Divergence detected')}")
        if top.get("description"):
            lines.append(f"  {top['description']}")
    elif signal and signal.get("key_data_relationships"):
        lines.append(f"  {signal['key_data_relationships']}")

    # CTA + sign-off
    lines.append("")
    lines.append(f"-> Full dashboard: {DASHBOARD_URL}")
    lines.append("")
    lines.append("-- Thomas")
    lines.append("")
    lines.append(f"Solana Weekly | {SITE_URL}")
    lines.append("You're getting this because you signed up for the daily Solana briefing.")
    lines.append(f"Unsubscribe: {KIT_UNSUBSCRIBE}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML body (email-compatible)
# ---------------------------------------------------------------------------

def _metric_row(label: str, value: str, change: float = None, wow_val: float = None) -> str:
    change_html = ""
    if change is not None:
        change_html = (
            f'<span style="color:{change_color(change)};font-size:13px;margin-left:6px;">'
            f'{fmt_change(change)} 24h</span>'
        )
    wow_html = ""
    if wow_val is not None:
        wow_html = (
            f'<span style="color:{change_color(wow_val)};font-size:12px;margin-left:6px;">'
            f'WoW {fmt_change(wow_val)}</span>'
        )
    return f"""<tr>
  <td style="padding:6px 0;color:{TEXT_MUTED};font-size:13px;border-bottom:1px solid {BORDER_COLOR};">{esc(label)}</td>
  <td style="padding:6px 0;color:{TEXT_PRIMARY};font-size:15px;font-weight:600;text-align:right;border-bottom:1px solid {BORDER_COLOR};">
    {esc(value)}{change_html}{wow_html}
  </td>
</tr>"""


def _section_heading(text: str, accent: str = PURPLE_LIGHT) -> str:
    return (
        f'<tr><td style="padding:20px 24px 4px 24px;">'
        f'<h2 style="margin:0;font-size:12px;text-transform:uppercase;letter-spacing:2px;color:{accent};">'
        f'{esc(text)}</h2></td></tr>'
    )


def _card_row(content_html: str, accent: str = PURPLE) -> str:
    return f"""<tr><td style="padding:4px 24px 4px 24px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:{CARD_BG};border-radius:8px;">
    <tr>
      <td style="width:3px;background-color:{accent};border-top-left-radius:8px;border-bottom-left-radius:8px;"></td>
      <td style="padding:12px 16px;">{content_html}</td>
    </tr>
  </table>
</td></tr>"""


def build_html_body(compiled: dict, narrative: dict, wow: dict, blocks: dict) -> str:
    market = compiled.get("market", {})
    solana = compiled.get("solana", {})
    prices = market.get("prices", {})
    fg = market.get("fear_greed", {})
    global_data = market.get("global", {})
    dex = solana.get("dex_volumes", {})
    sol_tvl = solana.get("solana_tvl", {})
    signal = narrative.get("the_signal", {})

    tldr = get_tldr(narrative, compiled, blocks)
    preheader = get_preheader(narrative, blocks)
    date_str = today_short()

    # --- The Numbers ---
    sol = prices.get("SOL", {})
    btc = prices.get("BTC", {})
    eth = prices.get("ETH", {})

    metric_rows = []
    if sol.get("price"):
        metric_rows.append(_metric_row(
            "SOL", fmt_price(sol["price"]),
            sol.get("change_24h"), wow.get("sol_price"),
        ))
    if btc.get("price"):
        metric_rows.append(_metric_row(
            "BTC", fmt_price(btc["price"]), btc.get("change_24h"),
        ))
    if eth.get("price"):
        metric_rows.append(_metric_row(
            "ETH", fmt_price(eth["price"]), eth.get("change_24h"),
        ))

    if fg.get("value") is not None:
        metric_rows.append(_metric_row(
            "Fear & Greed",
            f"{fg['value']} — {fg.get('label', '')}",
        ))
    if global_data.get("total_market_cap"):
        metric_rows.append(_metric_row(
            "Total Mcap", fmt_usd(global_data["total_market_cap"]),
            wow_val=wow.get("total_market_cap"),
        ))
    if sol_tvl.get("current"):
        metric_rows.append(_metric_row(
            "Solana TVL", fmt_usd(sol_tvl["current"]),
            wow_val=wow.get("sol_tvl"),
        ))
    if dex.get("combined_24h"):
        metric_rows.append(_metric_row(
            "DEX Volume", fmt_usd(dex["combined_24h"]),
            wow_val=wow.get("dex_volume"),
        ))

    metrics_html = "\n".join(metric_rows)

    # --- Top mover ---
    mover = blocks.get("top_mover")
    top_mover_section = ""
    if mover:
        chg = mover.get("change_24h")
        color = change_color(chg)
        top_mover_section = (
            _section_heading("Top Mover (24h)")
            + _card_row(
                f'<div style="font-size:18px;font-weight:700;color:{TEXT_PRIMARY};">'
                f'{esc(mover["ticker"])} '
                f'<span style="color:{color};font-size:16px;">{fmt_change(chg)}</span>'
                f'</div>'
                f'<div style="font-size:13px;color:{TEXT_MUTED};margin-top:2px;">'
                f'Now {fmt_price(mover.get("price"))}</div>'
            )
        )

    # --- Names to watch ---
    names_section = ""
    names = blocks.get("names_to_watch") or []
    if names:
        rows = ""
        for item in names[:5]:
            detail = item["details"][0] if item.get("details") else ", ".join(item.get("sources", []))
            source = " / ".join(item.get("sources", [])[:2])
            rows += (
                '<tr>'
                f'<td style="padding:5px 0;color:{PURPLE_LIGHT};font-size:15px;font-weight:800;">{esc(item["symbol"])}</td>'
                f'<td style="padding:5px 0;color:{TEXT_PRIMARY};font-size:13px;">{esc(detail)}</td>'
                f'<td style="padding:5px 0;color:{TEXT_MUTED};font-size:12px;text-align:right;">{esc(source)}</td>'
                '</tr>'
            )
        names_section = (
            _section_heading("Names to Watch")
            + _card_row(f'<table width="100%" cellpadding="0" cellspacing="0" border="0">{rows}</table>')
        )

    # --- Solana DEX flow ---
    dex_flow_section = ""
    dex_flow = blocks.get("dex_flow") or []
    if dex_flow:
        rows = ""
        for d in dex_flow:
            chg = d.get("change_1d")
            share = f"{d['share']:.1f}% share" if d.get("share") is not None else ""
            rows += (
                '<tr>'
                f'<td style="padding:5px 0;color:{TEXT_PRIMARY};font-size:14px;font-weight:700;">{esc(d.get("name",""))}</td>'
                f'<td style="padding:5px 0;color:{TEXT_PRIMARY};font-size:13px;text-align:right;">{fmt_usd(d.get("volume_24h",0))}</td>'
                f'<td style="padding:5px 0 5px 8px;color:{change_color(chg)};font-size:13px;font-weight:600;text-align:right;">{fmt_change(chg)}</td>'
                f'<td style="padding:5px 0 5px 8px;color:{TEXT_MUTED};font-size:12px;text-align:right;">{esc(share)}</td>'
                '</tr>'
            )
        dex_flow_section = (
            _section_heading("Solana DEX Flow")
            + _card_row(
                f'<div style="color:{TEXT_MUTED};font-size:13px;line-height:1.5;margin-bottom:8px;">'
                f'Where spot volume is concentrating across Solana venues.</div>'
                f'<table width="100%" cellpadding="0" cellspacing="0" border="0">{rows}</table>',
                accent=CYAN,
            )
        )

    # --- BTC treasury bid ---
    treasury_section = ""
    treasury = blocks.get("treasury_signal")
    if treasury:
        treasury_section = (
            _section_heading("BTC Treasury Bid")
            + _card_row(
                f'<div style="color:{TEXT_MUTED};font-size:13px;line-height:1.5;margin-bottom:8px;">'
                f'Saylor/Strategy accumulation as a public proxy for corporate BTC treasury demand.</div>'
                f'<div style="font-size:18px;font-weight:700;color:{TEXT_PRIMARY};">'
                f'Strategy: {treasury.get("strategy_holdings_btc", 0):,.0f} BTC</div>'
                f'<div style="font-size:13px;color:{TEXT_MUTED};margin-top:4px;">'
                f'{treasury.get("strategy_share", 0)}% of tracked treasury BTC &middot; '
                f'{treasury.get("tracked_entities", 0)} tracked entities total</div>',
                accent=CYAN,
            )
        )

    # --- Protocol movers ---
    movers = blocks.get("protocol_movers") or []
    protocol_section = ""
    if movers:
        rows = ""
        for p in movers:
            chg = p.get("change_1d")
            rows += (
                '<tr>'
                f'<td style="padding:4px 0;color:{TEXT_PRIMARY};font-size:14px;font-weight:600;">{esc(p["name"])}'
                f' <span style="color:{TEXT_MUTED};font-weight:400;font-size:12px;">{esc(p.get("category",""))}</span></td>'
                f'<td style="padding:4px 0;color:{TEXT_MUTED};font-size:13px;text-align:right;">{fmt_usd(p.get("tvl",0))}</td>'
                f'<td style="padding:4px 0 4px 8px;color:{change_color(chg)};font-size:13px;font-weight:600;text-align:right;">{fmt_change(chg)}</td>'
                '</tr>'
            )
        protocol_section = (
            _section_heading("Solana Movers (1d TVL)")
            + _card_row(f'<table width="100%" cellpadding="0" cellspacing="0" border="0">{rows}</table>')
        )

    # --- Sector rotation ---
    sector_section = ""
    if blocks.get("sector_rotation"):
        sector_section = (
            _section_heading("Sector Rotation")
            + _card_row(
                f'<span style="color:{TEXT_PRIMARY};font-size:14px;line-height:1.6;">'
                f'{esc(blocks["sector_rotation"])}</span>'
            )
        )

    # --- Hyperliquid new listings (high-emphasis cyan card) ---
    hl_section = ""
    hl_added = blocks.get("hl_listings") or []
    if hl_added:
        tag_html = "".join(
            f'<span style="display:inline-block;background:{CYAN};color:#000;font-size:12px;font-weight:700;padding:3px 8px;border-radius:4px;margin:2px 3px 2px 0;">{esc(n)}</span>'
            for n in hl_added
        )
        hl_section = (
            _section_heading("New on Hyperliquid", accent=CYAN)
            + _card_row(
                f'<div style="color:{TEXT_PRIMARY};font-size:13px;margin-bottom:6px;">'
                f'{len(hl_added)} new listing(s) since yesterday:</div>'
                f'<div>{tag_html}</div>',
                accent=CYAN,
            )
        )

    # --- Best stablecoin yield ---
    best_yield = blocks.get("best_stable_apy")
    yield_section = ""
    if best_yield:
        yield_section = (
            _section_heading("Best Stable Yield Today")
            + _card_row(
                f'<div style="font-size:18px;font-weight:700;color:{GREEN};">'
                f'{(best_yield.get("apy") or 0):.1f}% APY</div>'
                f'<div style="font-size:13px;color:{TEXT_PRIMARY};margin-top:4px;">'
                f'{esc(best_yield.get("project",""))} — {esc(best_yield.get("symbol",""))}</div>'
                f'<div style="font-size:12px;color:{TEXT_MUTED};margin-top:2px;">'
                f'TVL {fmt_usd(best_yield.get("tvlUsd",0))}</div>'
            )
        )

    # --- Trade pick ---
    trade_section = ""
    trade = blocks.get("trade_pick")
    if trade:
        coin = trade["top_coin"]
        conv = trade.get("conviction", "Moderate")
        conv_color = GREEN if conv == "Strong" else (PURPLE_LIGHT if conv == "Moderate" else TEXT_MUTED)
        trade_section = (
            _section_heading("So, What's the Trade?")
            + _card_row(
                f'<div style="font-size:12px;color:{conv_color};font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">'
                f'{esc(conv)} conviction</div>'
                f'<div style="color:{TEXT_PRIMARY};font-size:14px;">'
                f'<strong>Sector:</strong> {esc(trade.get("sector",""))}</div>'
                f'<div style="color:{TEXT_PRIMARY};font-size:14px;margin-top:4px;">'
                f'<strong>Pick:</strong> {esc(coin.get("ticker","?"))} — '
                f'<span style="color:{TEXT_MUTED};">{esc(coin.get("reason",""))}</span></div>'
            )
        )

    # --- One thing to watch ---
    divergences = signal.get("divergence_alerts", []) if signal else []
    if divergences:
        top = divergences[0]
        watch_html = (
            f'<div style="color:{PURPLE_LIGHT};font-weight:600;font-size:14px;">{esc(top.get("title","Divergence detected"))}</div>'
            f'<div style="color:{TEXT_MUTED};font-size:13px;margin-top:4px;">{esc(top.get("description",""))}</div>'
        )
    elif signal and signal.get("key_data_relationships"):
        watch_html = f'<div style="color:{TEXT_MUTED};font-size:13px;">{esc(signal["key_data_relationships"])}</div>'
    else:
        watch_html = (
            f'<div style="color:{TEXT_MUTED};font-size:13px;">Fear &amp; Greed day-over-day shift.</div>'
        )
    watch_section = _section_heading("One Thing to Watch") + _card_row(watch_html)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Solana Weekly — {esc(date_str)}</title>
</head>
<body style="margin:0;padding:0;background-color:{DARK_BG};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;">

<!-- Preheader (hidden preview text) -->
<div style="display:none;max-height:0;overflow:hidden;font-size:1px;line-height:1px;color:{DARK_BG};opacity:0;">
{esc(preheader)}
</div>

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:{DARK_BG};">
<tr><td align="center" style="padding:20px 10px;">

<table role="presentation" width="560" cellpadding="0" cellspacing="0" border="0" style="max-width:560px;width:100%;">

  <!-- Header -->
  <tr><td style="padding:24px 24px 16px 24px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr>
      <td style="font-size:18px;font-weight:700;color:{PURPLE_LIGHT};letter-spacing:0.5px;">SOLANA WEEKLY</td>
      <td style="text-align:right;font-size:13px;color:{TEXT_MUTED};">{esc(date_str)}</td>
    </tr>
    </table>
  </td></tr>

  <tr><td style="padding:0 24px;">
    <div style="height:1px;background:linear-gradient(to right,{PURPLE},{BORDER_COLOR});background-color:{PURPLE};"></div>
  </td></tr>

  <!-- Personalized greeting -->
  <tr><td style="padding:20px 24px 4px 24px;">
    <p style="margin:0;font-size:14px;color:{TEXT_MUTED};">
      Morning, {KIT_FIRST_NAME} —
    </p>
  </td></tr>

  <!-- TLDR -->
  <tr><td style="padding:8px 24px 12px 24px;">
    <p style="margin:0;font-size:17px;line-height:1.5;color:{TEXT_PRIMARY};">
      {esc(tldr)}
    </p>
  </td></tr>

  <!-- THE NUMBERS -->
  {_section_heading("The Numbers")}
  <tr><td style="padding:4px 24px 4px 24px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:{CARD_BG};border-radius:8px;padding:12px 16px;">
      {metrics_html}
    </table>
  </td></tr>

  {top_mover_section}
  {names_section}
  {dex_flow_section}
  {treasury_section}
  {protocol_section}
  {sector_section}
  {hl_section}
  {yield_section}
  {trade_section}
  {watch_section}

  <!-- CTA -->
  <tr><td align="center" style="padding:28px 24px 8px 24px;">
    <table role="presentation" cellpadding="0" cellspacing="0" border="0">
    <tr><td align="center" style="background-color:{PURPLE};border-radius:6px;">
      <a href="{DASHBOARD_URL}" target="_blank" style="display:inline-block;padding:12px 32px;font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;letter-spacing:0.3px;">
        View Full Dashboard &rarr;
      </a>
    </td></tr>
    </table>
  </td></tr>

  <!-- Sign-off -->
  <tr><td style="padding:20px 24px 8px 24px;">
    <p style="margin:0;font-size:15px;color:{TEXT_PRIMARY};">-- Thomas</p>
  </td></tr>

  <!-- Footer -->
  <tr><td style="padding:16px 24px 24px 24px;">
    <div style="height:1px;background-color:{BORDER_COLOR};margin-bottom:16px;"></div>
    <p style="margin:0;font-size:12px;line-height:1.5;color:{TEXT_MUTED};">
      <a href="{SITE_URL}" style="color:{PURPLE_LIGHT};text-decoration:none;">Solana Weekly</a> &mdash; Daily Solana ecosystem intelligence.<br/>
      You're getting this because you signed up for the daily briefing.<br/>
      <a href="{KIT_UNSUBSCRIBE}" style="color:{TEXT_MUTED};text-decoration:underline;">Unsubscribe</a>
    </p>
  </td></tr>

</table>

</td></tr>
</table>

</body>
</html>"""

    return html


# ---------------------------------------------------------------------------
# Readiness gate
# ---------------------------------------------------------------------------

def is_narrative_usable(narrative: dict) -> tuple[bool, str]:
    """Decide whether a narrative is good enough to ship.

    We require at minimum that the call didn't error and that either
    `newsletter_tldr` OR `the_signal.market_context` is present, so the
    email always has a real editorial line (never a debug string).
    """
    if not narrative:
        return False, "narrative.json empty"
    if narrative.get("error"):
        return False, f"narrative error: {narrative['error']}"
    tldr = (narrative.get("newsletter_tldr") or "").strip()
    context = ((narrative.get("the_signal") or {}).get("market_context") or "").strip()
    if not tldr and not context:
        return False, "no tldr or market_context in narrative"
    return True, "ok"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run() -> dict:
    compiled = load_json("compiled.json")
    if not compiled:
        log.error("No compiled data found -- run compile_data.py first")
        return {"ready": False, "reason": "compiled.json missing"}

    narrative = load_json("narrative.json") or {}

    usable, reason = is_narrative_usable(narrative)
    if not usable:
        log.error(f"Narrative not usable ({reason}) — marking newsletter NOT ready")
        result = {
            "timestamp": now_utc(),
            "ready": False,
            "reason": reason,
        }
        save_json(result, "newsletter.json")
        return result

    wow = compiled.get("wow", {}) or {}

    log.info("Building newsletter...")

    blocks = compute_blocks(compiled, narrative)
    subject = build_subject(compiled, narrative, wow, blocks)
    preheader = get_preheader(narrative, blocks)
    text_body = build_text_body(compiled, narrative, wow, blocks)
    html_body = build_html_body(compiled, narrative, wow, blocks)

    log.info(f"  Subject: {subject}")
    log.info(f"  Preheader: {preheader}")
    log.info(f"  Text body: {len(text_body)} chars")
    log.info(f"  HTML body: {len(html_body)} chars")

    # Log which blocks hit
    present = [k for k, v in blocks.items() if v]
    log.info(f"  Active blocks: {', '.join(present) if present else 'none'}")

    market = compiled.get("market", {})
    result = {
        "timestamp": now_utc(),
        "ready": True,
        "subject": subject,
        "preheader": preheader,
        "text_body": text_body,
        "html_body": html_body,
        "metadata": {
            "sol_price": market.get("prices", {}).get("SOL", {}).get("price"),
            "fear_greed": market.get("fear_greed", {}).get("value"),
            "date": today_short(),
            "dashboard_url": DASHBOARD_URL,
            "active_blocks": present,
        },
    }

    save_json(result, "newsletter.json")
    log.info("Newsletter draft saved to data/newsletter.json")
    return result


if __name__ == "__main__":
    run()
