"""Fetch tokenized stock data from xStocks and PreStocks on Solana.

xStocks (by Backed Finance): Fully collateralized US equities as SPL tokens
on Solana (xAAPL, xTSLA, xNVDA, etc.). NOTE: xStocks tokens are not currently
indexed by DexScreener — the search will return empty until we add a Bitquery
API key or discover their DEX pair addresses.

PreStocks: Pre-IPO tokenized stocks on Solana tracking private companies
(SpaceX, OpenAI, Anthropic, etc.). Actively traded on Raydium/Orca.

Data source: DexScreener API (public, free, unauthenticated).
  GET https://api.dexscreener.com/latest/dex/search?q={query}
"""

from __future__ import annotations

import time
import requests
from config import save_json, get_logger, now_utc, DEFAULT_HEADERS

log = get_logger("fetch_stocks")

DEXSCREENER_SEARCH = "https://api.dexscreener.com/latest/dex/search"

# Minimum 24h volume ($) to include a token — filters out noise from generic name matches
MIN_VOLUME_24H = 10_000
# Minimum liquidity ($) for a valid pair
MIN_LIQUIDITY = 5_000

# Popular xStocks tickers to track (tokenized US equities on Solana)
XSTOCK_TICKERS = [
    "xAAPL", "xTSLA", "xNVDA", "xMSFT", "xAMZN",
    "xGOOG", "xMETA", "xAMD", "xNFLX", "xCOIN",
    "xMSTR", "xPLTR",
]

# PreStocks tokens — use the exact token symbols from PreStocks platform.
# Each entry: (search_query, expected_symbol_substring)
PRESTOCK_SEARCHES = [
    ("prestocks SpaceX", "SPACEX"),
    ("prestocks OpenAI", "OPENAI"),
    ("prestocks Anthropic", "ANTHROPIC"),
    ("prestocks Anduril", "ANDURIL"),
    ("prestocks Neuralink", "NEURALINK"),
    ("prestocks Stripe", "STRIPE"),
    ("prestocks Databricks", "DATABRICKS"),
    ("prestocks Perplexity", "PERPLEXITY"),
    ("prestocks Kraken", "KRAKEN"),
    ("prestocks Discord", "DISCORD"),
    ("prestocks Epic Games", "EPIC"),
]


def _search_dex(query: str) -> list:
    """Search DexScreener and return Solana pairs."""
    try:
        resp = requests.get(
            DEXSCREENER_SEARCH,
            params={"q": query},
            headers=DEFAULT_HEADERS,
            timeout=15,
        )
        if resp.status_code != 200:
            log.warning(f"  DexScreener {resp.status_code} for '{query}'")
            return []
        pairs = resp.json().get("pairs") or []
        return [p for p in pairs if p.get("chainId") == "solana"]
    except Exception as e:
        log.warning(f"  DexScreener search failed for '{query}': {e}")
        return []


def _best_pair(pairs: list) -> dict | None:
    """Pick the pair with highest liquidity from a list, applying quality filters."""
    if not pairs:
        return None
    valid = [
        p for p in pairs
        if float((p.get("liquidity") or {}).get("usd", 0) or 0) >= MIN_LIQUIDITY
        and float((p.get("volume") or {}).get("h24", 0) or 0) >= MIN_VOLUME_24H
    ]
    if not valid:
        return None
    return max(valid, key=lambda p: float((p.get("liquidity") or {}).get("usd", 0) or 0))


def _safe_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pair_to_record(pair: dict) -> dict:
    """Extract relevant fields from a DexScreener pair."""
    base = pair.get("baseToken", {})
    volume = pair.get("volume") or {}
    change = pair.get("priceChange") or {}
    liq = pair.get("liquidity") or {}
    return {
        "symbol": base.get("symbol", ""),
        "name": base.get("name", ""),
        "address": base.get("address", ""),
        "price": float(pair.get("priceUsd") or 0),
        "change_24h": _safe_float(change.get("h24")),
        "change_6h": _safe_float(change.get("h6")),
        "volume_24h": float(volume.get("h24") or 0),
        "liquidity": float(liq.get("usd") or 0),
        "fdv": float(pair.get("fdv") or 0),
        "market_cap": float(pair.get("marketCap") or 0),
        "pair_url": pair.get("url", ""),
        "dex": pair.get("dexId", ""),
    }


def _fmt_change(val) -> str:
    if val is None:
        return "N/A"
    return f"{val:+.1f}%"


def fetch_xstocks() -> list:
    """Fetch xStocks tokenized equity data from DexScreener."""
    log.info("  Searching for xStocks tokens...")
    results = []
    seen_addrs = set()

    for ticker in XSTOCK_TICKERS:
        pairs = _search_dex(ticker)
        # Find pairs where the base token symbol matches exactly
        matching = [
            p for p in pairs
            if (p.get("baseToken", {}).get("symbol") or "").upper() == ticker.upper()
        ]

        best = _best_pair(matching)
        if best:
            addr = best.get("baseToken", {}).get("address", "")
            if addr and addr not in seen_addrs:
                seen_addrs.add(addr)
                record = _pair_to_record(best)
                results.append(record)
                log.info(f"    {record['symbol']}: ${record['price']:.2f} ({_fmt_change(record['change_24h'])})")

        time.sleep(0.25)

    results.sort(key=lambda r: r["volume_24h"], reverse=True)
    log.info(f"  Found {len(results)} xStocks tokens")
    return results


def fetch_prestocks() -> list:
    """Fetch PreStocks pre-IPO token data from DexScreener."""
    log.info("  Searching for PreStocks tokens...")
    results = []
    seen_addrs = set()

    for query, expected_sym in PRESTOCK_SEARCHES:
        pairs = _search_dex(query)

        # Filter: symbol or name must contain our expected substring
        matching = []
        expected_lower = expected_sym.lower()
        for p in pairs:
            base_sym = (p.get("baseToken", {}).get("symbol") or "").lower()
            base_name = (p.get("baseToken", {}).get("name") or "").lower()
            if expected_lower in base_sym or expected_lower in base_name:
                matching.append(p)

        best = _best_pair(matching)
        if best:
            addr = best.get("baseToken", {}).get("address", "")
            if addr and addr not in seen_addrs:
                seen_addrs.add(addr)
                record = _pair_to_record(best)
                results.append(record)
                price = record["price"]
                price_str = f"${price:.6f}" if price < 0.01 else f"${price:.2f}"
                log.info(f"    {record['symbol']}: {price_str} ({_fmt_change(record['change_24h'])})")

        time.sleep(0.25)

    results.sort(key=lambda r: r["volume_24h"], reverse=True)
    log.info(f"  Found {len(results)} PreStocks tokens")
    return results


def run() -> dict:
    log.info("Fetching tokenized stock data (xStocks + PreStocks)...")

    xstocks = fetch_xstocks()
    prestocks = fetch_prestocks()

    result = {
        "timestamp": now_utc(),
        "source": "dexscreener",
        "xstocks": xstocks,
        "prestocks": prestocks,
        "xstocks_count": len(xstocks),
        "prestocks_count": len(prestocks),
    }

    save_json(result, "stocks.json")
    log.info(f"Stocks data saved: {len(xstocks)} xStocks, {len(prestocks)} PreStocks")
    return result


if __name__ == "__main__":
    run()
