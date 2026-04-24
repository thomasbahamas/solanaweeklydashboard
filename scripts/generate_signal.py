"""Generate AI narrative content using Claude API.

Produces:
- The Signal: Market analysis with divergence alerts
- Market Maker Activity: Institutional signals and positioning
- X Pulse: Protocol updates, influencer takes, trending narratives
- Story Pitches: 3 content ideas with hooks
- Tweet Options: 2 draft tweets
- Briefing Script: 3-4 minute daily briefing
"""

import json
import anthropic
from config import (
    load_json, save_json, get_logger, now_utc,
    ANTHROPIC_API_KEY,
)

log = get_logger("generate_signal")

SYSTEM_PROMPT = """You are the editorial intelligence engine for Solana Weekly, a Solana ecosystem media brand.

Your job is to analyze compiled market data and produce actionable content for video journalist Thomas Bahamas (@thomasbahamas).

Your voice: Direct, data-driven, no fluff. You think in frameworks. You identify divergences between sentiment and fundamentals. You spot the story beneath the noise.

RULES:
- Lead with data, not opinion
- Flag divergences between price action and on-chain activity
- Identify institutional vs retail positioning gaps
- Connect macro (F&G, BTC dominance, oil, DXY) to Solana-specific data
- IDENTIFY TRADE OPPORTUNITIES: When yields spike while TVL drops, when sector rotation is underway, when DePIN metrics diverge from price — call it out explicitly
- Include sector rotation analysis: which Solana sectors (DeFi, DEX, Lending, Liquid Staking, DePIN, Gaming) are gaining/losing capital
- Story pitches should be VIDEO-ready — strong hooks, clear narrative arc
- Tweets should be data-forward, no emojis, no shilling
- Briefing script should be readable in 3-4 minutes at a natural pace

MARKET MAKER ACTIVITY INSTRUCTIONS:
- Analyze the news for any mentions of institutional firms, market makers, large funds
- Firms to watch: Galaxy Digital, Jump Trading, Wintermute, Jane Street, Citadel, Multicoin Capital, a16z, Paradigm, Pantera, Alameda successors
- Only emit a signal if it is DIRECTLY supported by a news headline in the provided data. Quote the source headline in the `detail` field.
- Do NOT fabricate firm activity when no news exists. Return an EMPTY `signals` array rather than inventing positioning. Credibility depends on this.
- Include Arkham-style analysis ONLY when on-chain flow is referenced in the provided data

TRENDING NARRATIVES INSTRUCTIONS:
- trending_narratives: Identify 4-6 emerging narrative threads from the combined data and news. These should be data-driven observations, not attributed to any specific person or account.

"SO WHAT'S THE TRADE?" INSTRUCTIONS:
This is the most important section. Readers are traders — they need to know what the data means and where to look. Structure your answer top-down:
- macro: What's the macro environment saying? (F&G, BTC dominance, rates, risk-on/risk-off)
- crypto: Where is crypto as an asset class positioned? (BTC trend, altcoin rotation, capital flows)
- solana: What's the Solana-specific setup? (TVL trend, DEX activity, fee revenue, staking flows)
- sector: Which Solana sector is best positioned right now and why? (DeFi, Liquid Staking, DEX, Lending, DePIN, Gaming, RWA, etc.)
- coins: Name 2-4 specific tokens in that sector that the data supports. Cite the data point (TVL growth, volume spike, yield, etc.)
- conviction: Rate the overall setup as "Strong", "Moderate", or "Wait" with a one-line reason.
Always lead with "NFA, but here's what the data is saying:" and be direct. This is for experienced traders, not beginners.

BRIEFING SCRIPT INSTRUCTIONS:
- Include a [SEGMENT 5: CT PULSE] section that covers trending narratives and notable takes
- Keep the script readable in 3-4 minutes

NEWSLETTER TLDR INSTRUCTIONS:
- `newsletter_tldr` is the single line that will open the daily email to thousands of traders.
- 1-2 sentences. Maximum 240 characters. No emojis. No "NFA". Punchy.
- It MUST name a specific number, token, or event from today's data — not generic commentary.
- Good: "Solana TVL crossed $9B for the first time since June while SOL price lags BTC by 4pts — leverage is still cheap."
- Bad: "Markets are mixed today with some interesting action across the board."
- `newsletter_preheader` is the Gmail preview text (≤90 chars). Tease the data without repeating the TLDR verbatim.

OUTPUT FORMAT: Return valid JSON with these keys:
{
  "newsletter_tldr": "1-2 sentence data-forward hook for the daily email (<=240 chars)",
  "newsletter_preheader": "Gmail preview text (<=90 chars)",
  "the_signal": {
    "market_context": "2-3 paragraph market analysis",
    "divergence_alerts": [
      {"title": "...", "severity": "high|medium|low", "description": "..."}
    ],
    "story_angles": ["...", "...", "..."],
    "key_data_relationships": "1-2 sentences on the most important data connections"
  },
  "market_maker_activity": {
    "signals": [
      {
        "firm": "Galaxy Digital|Jump Trading|Wintermute|Jane Street|Citadel|etc",
        "signal": "Short description of what they did",
        "detail": "Why it matters",
        "sentiment": "Extremely Bullish|Bullish|Cautiously Bullish|Neutral|Neutral/Legal|Bearish"
      }
    ]
  },
  "trade_thesis": {
    "macro": "1-2 sentences on macro positioning",
    "crypto": "1-2 sentences on crypto as asset class",
    "solana": "1-2 sentences on Solana-specific setup",
    "sector": "Which sector and why",
    "coins": [
      {"ticker": "JTO", "reason": "Data point supporting this pick"}
    ],
    "conviction": "Strong|Moderate|Wait",
    "conviction_reason": "One line why"
  },
  "x_pulse": {
    "trending_narratives": [
      {"title": "AI Agents on Solana", "detail": "Supporting context from news/data"}
    ]
  },
  "story_pitches": [
    {"title": "...", "hook": "...", "is_new": true}
  ],
  "tweet_options": [
    {"label": "Data Banger", "text": "..."},
    {"label": "Narrative Angle", "text": "..."}
  ],
  "briefing_script": "Full script with [COLD OPEN], [SEGMENT 1: MARKET], [SEGMENT 2: NEWS], [SEGMENT 3: SOLANA], [SEGMENT 4: WHALE WATCH], [SEGMENT 5: CT PULSE], [CLOSE]"
}"""


def build_data_prompt(compiled: dict) -> str:
    """Format compiled data into a prompt for Claude."""
    market = compiled.get("market", {})
    solana = compiled.get("solana", {})
    news = compiled.get("news", {})
    whales = compiled.get("whales", {})
    stocks = compiled.get("stocks", {})

    # Extract key metrics
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

    # Build readable data summary
    sections = []

    # Market overview
    sections.append("=== MARKET OVERVIEW ===")
    sections.append(f"Fear & Greed: {fg.get('value', 'N/A')} — {fg.get('label', 'N/A')} (Yesterday: {fg.get('yesterday', 'N/A')})")
    sections.append(f"Total Market Cap: ${(global_data.get('total_market_cap') or 0)/1e12:.2f}T ({(global_data.get('market_cap_change_24h') or 0):+.1f}% 24h)")
    sections.append(f"BTC Dominance: {global_data.get('btc_dominance', 0)}% | SOL Dominance: {global_data.get('sol_dominance', 0)}%")
    sections.append("")
    for ticker, data in prices.items():
        sections.append(f"  {ticker}: ${(data.get('price') or 0):,.2f} ({(data.get('change_24h') or 0):+.1f}% 24h)")

    # SOL technicals
    sections.append("")
    sections.append("=== SOL TECHNICALS ===")
    sections.append(f"Price: ${(technicals.get('price') or 0):,.2f}")
    sections.append(f"50-Day MA: ${(technicals.get('ma_50') or 0):,.2f}")
    sections.append(f"200-Day MA: ${(technicals.get('ma_200') or 0):,.2f}")
    sections.append(f"RSI(14): {technicals.get('rsi_14') or 'N/A'}")
    sections.append(f"MA Signal: {technicals.get('ma_signal') or 'N/A'}")

    # Solana ecosystem
    sections.append("")
    sections.append("=== SOLANA ECOSYSTEM ===")
    sol_tvl = solana.get("solana_tvl", {})
    sections.append(f"Solana TVL: ${(sol_tvl.get('current') or 0)/1e9:.2f}B ({(sol_tvl.get('change_1d') or 0):+.1f}% 24h)")
    sections.append(f"Spot DEX Volume: ${(dex.get('spot_24h') or 0)/1e9:.2f}B ({(dex.get('spot_change_1d') or 0):+.1f}%)")
    sections.append(f"Perp DEX Volume: ${(dex.get('perp_24h') or 0)/1e9:.2f}B")
    sections.append(f"Combined DEX: ${(dex.get('combined_24h') or 0)/1e9:.2f}B")
    sections.append(f"Fees 24h: ${(fees.get('total_24h') or 0)/1e6:.1f}M")
    sections.append(f"Stablecoins on Solana: ${(stables.get('total') or 0)/1e9:.2f}B")
    sections.append(f"TPS (total): {network.get('tps_total') or 'N/A'} | Non-vote: {network.get('tps_non_vote') or 'N/A'}")

    # Top DEXes
    sections.append("")
    sections.append("Top Spot DEXes:")
    for d in dex.get("top_spot", [])[:10]:
        sections.append(f"  {d['name']}: ${(d.get('volume_24h') or 0)/1e6:.1f}M ({(d.get('change_1d') or 0):+.1f}%)")

    sections.append("")
    sections.append("Top Perp DEXes:")
    for d in dex.get("top_perps", [])[:7]:
        sections.append(f"  {d['name']}: ${(d.get('volume_24h') or 0)/1e6:.1f}M ({(d.get('change_1d') or 0):+.1f}%)")

    # Top protocols
    sections.append("")
    sections.append("Top 20 Solana Protocols by TVL:")
    for i, p in enumerate(protocols[:20], 1):
        sections.append(f"  {i}. {p['name']} ({p.get('category', '')}): ${(p.get('tvl') or 0)/1e6:.1f}M ({(p.get('change_1d') or 0):+.1f}% 1d, {(p.get('change_7d') or 0):+.1f}% 7d)")

    # Chain TVLs (top 10)
    sections.append("")
    sections.append("Chain TVLs (Top 10):")
    for c in chain_tvls[:10]:
        sections.append(f"  {c['name']}: ${(c.get('tvl') or 0)/1e9:.2f}B")

    # News
    sections.append("")
    sections.append("=== TOP NEWS ===")
    solana_news = news.get("solana_news", [])
    general_news = news.get("general_news", [])
    rss = news.get("rss_feeds", [])

    for story in (solana_news + general_news)[:12]:
        cats = ", ".join(story.get("categories", ["General"]))
        sections.append(f"  [{cats}] {story['title']} — {story['source']}")

    if rss:
        sections.append("")
        sections.append("RSS Headlines:")
        for story in rss[:8]:
            sections.append(f"  {story['title']} — {story['source']}")

    # Whale data
    sections.append("")
    sections.append("=== WHALE INTELLIGENCE ===")
    whale_news = whales.get("whale_news", [])
    staking_flows = whales.get("staking_flows", {})

    for w in whale_news[:6]:
        sections.append(f"  {w['title']} — {w['source']}")

    if staking_flows:
        sections.append(f"Total Liquid Staking TVL: ${(staking_flows.get('total_staked_tvl') or 0)/1e9:.2f}B")
        for p in staking_flows.get("protocols", [])[:5]:
            sections.append(f"  {p['name']}: ${(p.get('tvl') or 0)/1e6:.0f}M ({(p.get('change_1d') or 0):+.1f}% 1d)")

    # Sector breakdown
    sectors_data = solana.get("sectors", {})
    sectors = sectors_data.get("sectors", [])
    depin = sectors_data.get("depin", [])
    if sectors:
        sections.append("")
        sections.append("=== SECTOR ROTATION (Solana) ===")
        for s in sectors[:12]:
            sections.append(f"  {s['sector']}: {(s.get('tvl') or 0)/1e6:.0f}M TVL ({(s.get('change_1d') or 0):+.1f}% 24h) — {s.get('protocol_count', 0)} protocols, top: {s.get('top_protocol', 'N/A')}")
    if depin:
        sections.append("")
        sections.append("DePIN / Infrastructure:")
        for d in depin[:8]:
            sections.append(f"  {d['name']}: ${(d.get('tvl') or 0)/1e6:.1f}M ({(d.get('change_1d') or 0):+.1f}% 1d, {(d.get('change_7d') or 0):+.1f}% 7d)")

    # DeFi yields
    defi_yields = solana.get("defi_yields", {})
    if defi_yields:
        sections.append("")
        sections.append("=== DEFI YIELDS (Solana) ===")
        summary = defi_yields.get("summary", {})
        sections.append(f"Total DeFi TVL: ${(summary.get('total_tvl') or 0)/1e9:.2f}B | Avg APY: {(summary.get('avg_apy') or 0):.1f}% | Best Stable: {(summary.get('best_stable_apy') or 0):.1f}%")
        for p in defi_yields.get("top_yields", [])[:10]:
            sections.append(f"  {p.get('project', '')}: {p.get('symbol', '')} — {(p.get('apy') or 0):.1f}% APY (TVL: ${(p.get('tvlUsd') or 0)/1e6:.0f}M)")

    # Trending
    sections.append("")
    sections.append("=== TRENDING (CoinGecko) ===")
    for t in market.get("trending", []):
        sections.append(f"  #{t.get('market_cap_rank', '?')} {t['symbol']} ({t['name']})")

    # Hyperliquid cross-market signal
    hl = compiled.get("hyperliquid", {})
    hl_top = hl.get("top_coins", [])
    if hl_top:
        sections.append("")
        sections.append("=== HYPERLIQUID PERPS (cross-market flow) ===")
        sections.append(f"Total 24h Volume: ${(hl.get('total_vlm_24h') or 0)/1e9:.2f}B | OI: ${(hl.get('total_oi_notional') or 0)/1e9:.2f}B | Markets: {hl.get('num_markets', 0)}")
        sections.append("Top 10 by 24h volume:")
        for i, c in enumerate(hl_top, 1):
            chg = c.get("change_24h")
            chg_str = f"{chg:+.1f}%" if isinstance(chg, (int, float)) else "n/a"
            fund = (c.get("funding") or 0) * 100
            sections.append(f"  {i}. {c.get('name','?')}: ${(c.get('day_ntl_vlm') or 0)/1e9:.2f}B vol, {chg_str}, funding {fund:+.4f}%/hr")
        listings = hl.get("new_listings", {}) or {}
        added = listings.get("added", []) or []
        if added and not listings.get("first_run"):
            sections.append(f"NEW LISTINGS this run: {', '.join(added)}")

    # Tokenized equities / RWA markets
    xstocks = stocks.get("xstocks", []) if isinstance(stocks, dict) else []
    prestocks = stocks.get("prestocks", []) if isinstance(stocks, dict) else []
    if xstocks or prestocks:
        sections.append("")
        sections.append("=== TOKENIZED STOCKS / RWA WATCH ===")
        if xstocks:
            sections.append("xStocks on Solana:")
            for t in xstocks[:10]:
                chg = t.get("change_24h")
                chg_str = f"{chg:+.1f}%" if isinstance(chg, (int, float)) else "n/a"
                sections.append(
                    f"  {t.get('symbol','?')} ({t.get('name','')}): "
                    f"${(t.get('price') or 0):,.2f}, {chg_str}, "
                    f"${(t.get('volume_24h') or 0)/1e6:.2f}M volume, "
                    f"${(t.get('liquidity') or 0)/1e6:.2f}M liquidity"
                )
        if prestocks:
            sections.append("PreStocks on Solana:")
            for t in prestocks[:10]:
                chg = t.get("change_24h")
                chg_str = f"{chg:+.1f}%" if isinstance(chg, (int, float)) else "n/a"
                sections.append(
                    f"  {t.get('symbol','?')} ({t.get('name','')}): "
                    f"${(t.get('price') or 0):,.4f}, {chg_str}, "
                    f"${(t.get('volume_24h') or 0)/1e6:.2f}M volume"
                )

    # Network upgrades
    upgrades = compiled.get("upgrades", {})
    if upgrades:
        sections.append("")
        sections.append("=== SOLANA NETWORK UPGRADES ===")
        infra = upgrades.get("infrastructure", {})
        for key, item in infra.items():
            metric = f" — {item['metric_label']}: {item['metric_value']}" if item.get("metric_value") else ""
            sections.append(f"  {item['name']}: {item['status']}{metric}")
            sections.append(f"    {item['description']}")

        simds = upgrades.get("simds", {})
        stats = simds.get("stats", {})
        if stats:
            sections.append(f"  SIMDs: {stats.get('open', 0)} open, {stats.get('merged', 0)} merged")
            for s in simds.get("recent", [])[:8]:
                state_tag = s['state'].upper()
                sections.append(f"    [{state_tag}] {s['title']} (by {s['author']}, {s['updated']})")

        upgrade_news = upgrades.get("upgrade_news", [])
        if upgrade_news:
            sections.append("  Upgrade-related news:")
            for n in upgrade_news[:5]:
                sections.append(f"    {n['title']} — {n['source']}")

    return "\n".join(sections)


def generate_narrative(compiled: dict) -> dict:
    """Call Claude API to generate The Signal, pitches, tweets, briefing."""
    if not ANTHROPIC_API_KEY:
        log.error("No ANTHROPIC_API_KEY set — cannot generate narrative")
        return {"error": "No API key"}

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    data_prompt = build_data_prompt(compiled)

    user_message = f"""Here is today's compiled Solana Weekly data. Analyze it and produce the full editorial package.

{data_prompt}

Return ONLY valid JSON matching the schema described in your instructions. No markdown fences, no preamble."""

    log.info("Calling Claude API for narrative generation...")
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )

        text = response.content[0].text.strip()

        # Strip markdown fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        narrative = json.loads(text)
        log.info("Narrative generated successfully.")
        log.info(f"  Token usage: {response.usage.input_tokens} in / {response.usage.output_tokens} out")
        return narrative

    except json.JSONDecodeError as e:
        log.error(f"Failed to parse Claude response as JSON: {e}")
        log.error(f"Raw response: {text[:500]}")
        return {"error": "JSON parse failed", "raw": text[:1000]}
    except Exception as e:
        log.error(f"Claude API call failed: {e}")
        return {"error": str(e)}


def run() -> dict:
    compiled = load_json("compiled.json")
    if not compiled:
        log.error("No compiled data found — run compile_data.py first")
        return {}

    narrative = generate_narrative(compiled)

    result = {
        "timestamp": now_utc(),
        **narrative,
    }

    save_json(result, "narrative.json")
    log.info("Narrative saved.")
    return result


if __name__ == "__main__":
    run()
