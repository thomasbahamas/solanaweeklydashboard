"""Fetch public crypto treasury data for BTC accumulation signals.

Primary use case: monitor Strategy/MicroStrategy BTC holdings as a proxy for
Saylor-led treasury demand. CoinGecko's public_treasury endpoint also gives
aggregate public company/government BTC holdings, which is useful context for
whether the treasury bid is expanding or fading.
"""

from __future__ import annotations

from config import api_get, save_json, get_logger, now_utc

log = get_logger("fetch_treasuries")


def _safe_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _find_strategy(companies: list) -> dict:
    """Find Strategy/MicroStrategy in CoinGecko's BTC treasury list."""
    for company in companies:
        name = (company.get("name") or "").lower()
        symbol = (company.get("symbol") or "").lower()
        if "strategy" in name or "microstrategy" in name or "mstr" in symbol:
            return company
    return {}


def fetch_btc_treasuries() -> dict:
    """Fetch BTC public treasury holdings from CoinGecko."""
    data = api_get("https://api.coingecko.com/api/v3/companies/public_treasury/bitcoin")
    if not data or not isinstance(data, dict):
        log.warning("  BTC treasury endpoint returned no data")
        return {}

    companies = data.get("companies") or []
    strategy = _find_strategy(companies)
    total_holdings = _safe_float(data.get("total_holdings"))
    strategy_holdings = _safe_float(strategy.get("total_holdings"))
    strategy_value = _safe_float(strategy.get("total_current_value_usd"))
    strategy_entry = _safe_float(strategy.get("total_entry_value_usd"))

    strategy_share = 0
    if total_holdings and strategy_holdings:
        strategy_share = round(strategy_holdings / total_holdings * 100, 2)

    unrealized_pnl = strategy_value - strategy_entry if strategy_value and strategy_entry else 0

    return {
        "total_holdings_btc": total_holdings,
        "total_value_usd": _safe_float(data.get("total_value_usd")),
        "market_cap_dominance": data.get("market_cap_dominance"),
        "tracked_entities": len(companies),
        "strategy": {
            "name": strategy.get("name", "Strategy"),
            "symbol": strategy.get("symbol", "MSTR.US"),
            "country": strategy.get("country", "US"),
            "holdings_btc": strategy_holdings,
            "current_value_usd": strategy_value,
            "entry_value_usd": strategy_entry,
            "unrealized_pnl_usd": unrealized_pnl,
            "percentage_of_total_supply": strategy.get("percentage_of_total_supply"),
            "share_of_tracked_treasury_btc": strategy_share,
        },
        "top_holders": [
            {
                "name": c.get("name", "Unknown"),
                "symbol": c.get("symbol", ""),
                "country": c.get("country", ""),
                "holdings_btc": _safe_float(c.get("total_holdings")),
                "current_value_usd": _safe_float(c.get("total_current_value_usd")),
                "percentage_of_total_supply": c.get("percentage_of_total_supply"),
            }
            for c in companies[:10]
        ],
    }


def run() -> dict:
    log.info("Fetching public BTC treasury data...")
    btc = fetch_btc_treasuries()
    result = {
        "timestamp": now_utc(),
        "source": "https://api.coingecko.com/api/v3/companies/public_treasury/bitcoin",
        "btc": btc,
    }
    save_json(result, "treasuries.json")

    strategy = btc.get("strategy", {}) if btc else {}
    if strategy.get("holdings_btc"):
        log.info(
            f"  Strategy BTC: {strategy['holdings_btc']:,.0f} BTC "
            f"({strategy.get('share_of_tracked_treasury_btc', 0)}% of tracked treasury BTC)"
        )
    else:
        log.warning("  Strategy BTC holdings unavailable")
    return result


if __name__ == "__main__":
    run()
