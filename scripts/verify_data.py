"""Verify compiled data quality before dashboard generation.

Acts as an editorial gate — checks for blank sections, stale data,
unreasonable numbers, and missing critical fields. Logs warnings for
minor issues and raises errors for critical failures.

Returns a report dict and optionally blocks the pipeline if data
quality is below threshold.
"""

from config import load_json, save_json, get_logger, now_utc
from datetime import datetime, timezone

log = get_logger("verify")

# Reasonable bounds for sanity checks
BOUNDS = {
    "sol_price": (1, 2000),
    "btc_price": (1000, 500000),
    "eth_price": (50, 50000),
    "total_market_cap": (1e11, 2e14),     # $100B - $200T
    "solana_tvl": (1e8, 1e12),             # $100M - $1T
    "dex_volume_24h": (1e6, 1e12),         # $1M - $1T
    "fear_greed": (0, 100),
    "tps": (100, 200000),
    "validator_count": (500, 10000),
}


def check_prices(market: dict, issues: list, warnings: list):
    """Verify price data exists and is reasonable."""
    prices = market.get("prices", {})
    if not prices:
        issues.append("CRITICAL: No price data at all")
        return

    required = ["BTC", "ETH", "SOL"]
    for ticker in required:
        if ticker not in prices:
            issues.append(f"Missing price for {ticker}")
            continue
        p = prices[ticker]
        price = p.get("price")
        if price is None or price == 0:
            issues.append(f"{ticker} price is zero/null")
            continue
        # Bounds check
        key = f"{ticker.lower()}_price"
        if key in BOUNDS:
            lo, hi = BOUNDS[key]
            if price < lo or price > hi:
                issues.append(f"{ticker} price ${price:,.2f} outside expected range ${lo:,.0f}-${hi:,.0f}")

    # Check for stale data (all prices identical to previous — unlikely)
    price_values = [p.get("price", 0) for p in prices.values() if p.get("price")]
    if len(set(price_values)) == 1 and len(price_values) > 2:
        warnings.append("All coin prices are identical — possible stale/cached data")


def check_global(market: dict, issues: list, warnings: list):
    """Verify global market data."""
    g = market.get("global", {})
    if not g:
        warnings.append("No global market data")
        return

    mcap = g.get("total_market_cap", 0)
    lo, hi = BOUNDS["total_market_cap"]
    if mcap and (mcap < lo or mcap > hi):
        issues.append(f"Total market cap ${mcap:,.0f} outside expected range")

    btc_dom = g.get("btc_dominance", 0)
    if btc_dom and (btc_dom < 10 or btc_dom > 90):
        warnings.append(f"BTC dominance {btc_dom}% seems unusual")


def check_fear_greed(market: dict, issues: list, warnings: list):
    """Verify Fear & Greed index."""
    fg = market.get("fear_greed", {})
    val = fg.get("value")
    if val is None or val == "N/A":
        warnings.append("Fear & Greed value is missing")
        return
    if isinstance(val, (int, float)):
        lo, hi = BOUNDS["fear_greed"]
        if val < lo or val > hi:
            issues.append(f"Fear & Greed {val} outside 0-100 range")


def check_solana(solana: dict, issues: list, warnings: list):
    """Verify Solana ecosystem data."""
    if not solana:
        issues.append("CRITICAL: No Solana ecosystem data")
        return

    tvl = solana.get("solana_tvl", {})
    tvl_current = tvl.get("current", 0)
    if not tvl_current:
        warnings.append("Solana TVL is zero/missing")
    else:
        lo, hi = BOUNDS["solana_tvl"]
        if tvl_current < lo or tvl_current > hi:
            issues.append(f"Solana TVL ${tvl_current:,.0f} outside expected range")

    dex = solana.get("dex_volumes", {})
    combined = dex.get("combined_24h", 0)
    if not combined:
        warnings.append("DEX volume is zero/missing")

    network = solana.get("network", {})
    tps = network.get("tps_total")
    if tps:
        lo, hi = BOUNDS["tps"]
        if tps < lo or tps > hi:
            warnings.append(f"TPS {tps} outside expected range {lo}-{hi}")

    protocols = solana.get("protocol_rankings", [])
    if not protocols:
        warnings.append("No protocol rankings data")


def check_news(news: dict, issues: list, warnings: list):
    """Verify news data."""
    if not news:
        warnings.append("No news data")
        return
    sol_news = news.get("solana_news", [])
    gen_news = news.get("general_news", [])
    if not sol_news and not gen_news:
        warnings.append("No news stories at all")


def check_upgrades(upgrades: dict, issues: list, warnings: list):
    """Verify network upgrade data."""
    if not upgrades:
        warnings.append("No upgrade data")
        return

    adoption = upgrades.get("validator_adoption", {})
    clients = adoption.get("clients", [])
    if not clients:
        warnings.append("No validator client adoption data")
    else:
        total_pct = sum(c.get("stake_pct", 0) for c in clients)
        if total_pct < 95 or total_pct > 105:
            warnings.append(f"Client adoption totals {total_pct:.1f}% (expected ~100%)")

        total_validators = adoption.get("total_validators", 0)
        if total_validators:
            lo, hi = BOUNDS["validator_count"]
            if total_validators < lo or total_validators > hi:
                warnings.append(f"Validator count {total_validators} outside expected range")


def check_narrative(narrative: dict, issues: list, warnings: list):
    """Verify AI-generated narrative data."""
    if not narrative:
        warnings.append("No narrative/signal data (AI step may have been skipped)")
        return

    signal = narrative.get("the_signal", {})
    if not signal.get("market_context"):
        warnings.append("Signal market_context is empty")
    if not signal.get("divergence_alerts"):
        warnings.append("No divergence alerts generated")
    if not signal.get("story_angles"):
        warnings.append("No story angles generated")


def check_section_completeness(compiled: dict, narrative: dict, warnings: list):
    """Check that no major dashboard section will be blank."""
    blank_sections = []

    market = compiled.get("market", {})
    if not market.get("prices"):
        blank_sections.append("Market Overview (prices)")
    if not market.get("sol_technicals") or not market.get("sol_technicals", {}).get("price"):
        blank_sections.append("Technical Analysis")
    if not compiled.get("solana"):
        blank_sections.append("Solana Ecosystem")
    if not compiled.get("solana", {}).get("dex_volumes"):
        blank_sections.append("DEX Volume")
    if not compiled.get("solana", {}).get("protocol_rankings"):
        blank_sections.append("Protocols")
    if not compiled.get("upgrades"):
        blank_sections.append("Network Upgrades")
    if not narrative.get("the_signal", {}).get("market_context"):
        blank_sections.append("The Signal")

    if blank_sections:
        warnings.append(f"These sections will appear empty: {', '.join(blank_sections)}")


def run() -> dict:
    """Run all data quality checks. Returns a verification report."""
    log.info("Verifying data quality...")

    compiled = load_json("compiled.json")
    narrative = load_json("narrative.json")

    if not compiled:
        log.error("No compiled data found — cannot verify")
        return {"status": "FAIL", "issues": ["No compiled data"], "warnings": []}

    issues = []   # Serious problems — data is wrong
    warnings = []  # Minor problems — data is incomplete but usable

    market = compiled.get("market", {})
    solana = compiled.get("solana", {})
    news = compiled.get("news", {})
    upgrades = compiled.get("upgrades", {})

    check_prices(market, issues, warnings)
    check_global(market, issues, warnings)
    check_fear_greed(market, issues, warnings)
    check_solana(solana, issues, warnings)
    check_news(news, issues, warnings)
    check_upgrades(upgrades, issues, warnings)
    check_narrative(narrative, issues, warnings)
    check_section_completeness(compiled, narrative, warnings)

    # Score
    issue_count = len(issues)
    warning_count = len(warnings)

    if issue_count == 0 and warning_count == 0:
        status = "PASS"
        log.info("  Data quality: PASS — all checks passed")
    elif issue_count == 0:
        status = "PASS_WITH_WARNINGS"
        log.warning(f"  Data quality: PASS WITH WARNINGS — {warning_count} warnings")
        for w in warnings:
            log.warning(f"    ⚠ {w}")
    else:
        status = "FAIL"
        log.error(f"  Data quality: FAIL — {issue_count} issues, {warning_count} warnings")
        for i in issues:
            log.error(f"    ✗ {i}")
        for w in warnings:
            log.warning(f"    ⚠ {w}")

    report = {
        "timestamp": now_utc(),
        "status": status,
        "issues": issues,
        "warnings": warnings,
        "issue_count": issue_count,
        "warning_count": warning_count,
        "sections_checked": [
            "prices", "global", "fear_greed", "solana",
            "news", "upgrades", "narrative", "completeness",
        ],
    }

    save_json(report, "verification.json")
    log.info(f"  Verification report saved ({issue_count} issues, {warning_count} warnings)")
    return report


if __name__ == "__main__":
    report = run()
    if report.get("status") == "FAIL":
        exit(1)
