"""Fetch market overview data: prices, dominance, F&G, trending, technicals.

Uses CoinGecko as primary source with CoinPaprika as free fallback
when CoinGecko credits are exhausted.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from config import (
    api_get, save_json, get_logger, now_utc,
    WATCHLIST,
)

log = get_logger("fetch_market")

# CoinPaprika ID mapping for fallback
PAPRIKA_IDS = {
    "bitcoin": "btc-bitcoin",
    "ethereum": "eth-ethereum",
    "solana": "sol-solana",
    "jito-governance-token": "jto-jito",
    "bonk": "bonk-bonk",
    "hyperliquid": "hype-hyperliquid",
    "ondo-finance": "ondo-ondo-finance",
    "helium": "hnt-helium",
    "zcash": "zec-zcash",
    "render-token": "rndr-render-token",
}


def fetch_fear_greed() -> dict:
    """Alternative.me Fear & Greed Index."""
    data = api_get("https://api.alternative.me/fng/?limit=2")
    if not data or "data" not in data:
        return {"value": "N/A", "label": "N/A", "yesterday": "N/A"}
    entries = data["data"]
    return {
        "value": int(entries[0]["value"]),
        "label": entries[0]["value_classification"],
        "yesterday": int(entries[1]["value"]) if len(entries) > 1 else "N/A",
    }


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------

def _fetch_prices_coingecko() -> dict | None:
    ids = ",".join(WATCHLIST.keys())
    data = api_get(
        "https://api.coingecko.com/api/v3/coins/markets",
        params={
            "vs_currency": "usd",
            "ids": ids,
            "order": "market_cap_desc",
            "sparkline": "true",
            "price_change_percentage": "24h,7d",
        },
    )
    if not data or not isinstance(data, list):
        return None
    prices = {}
    for coin in data:
        ticker = WATCHLIST.get(coin["id"], coin["symbol"].upper())
        sparkline_raw = coin.get("sparkline_in_7d", {}).get("price", [])
        # Downsample to ~28 points (every 6th point from 168 hourly)
        sparkline = sparkline_raw[::6] if len(sparkline_raw) > 28 else sparkline_raw
        prices[ticker] = {
            "name": coin["name"],
            "price": coin["current_price"],
            "market_cap": coin["market_cap"],
            "change_24h": round(coin.get("price_change_percentage_24h") or 0, 1),
            "change_7d": round(coin.get("price_change_percentage_7d_in_currency") or 0, 1),
            "high_24h": coin.get("high_24h"),
            "low_24h": coin.get("low_24h"),
            "ath": coin.get("ath"),
            "atl": coin.get("atl"),
            "sparkline_7d": sparkline,
        }
    return prices


def _fetch_prices_paprika() -> dict | None:
    data = api_get("https://api.coinpaprika.com/v1/tickers")
    if not data or not isinstance(data, list):
        return None
    lookup = {coin["id"]: coin for coin in data}
    prices = {}
    for cg_id, ticker in WATCHLIST.items():
        paprika_id = PAPRIKA_IDS.get(cg_id)
        if not paprika_id or paprika_id not in lookup:
            continue
        coin = lookup[paprika_id]
        usd = coin.get("quotes", {}).get("USD", {})
        prices[ticker] = {
            "name": coin.get("name", ticker),
            "price": usd.get("price", 0),
            "market_cap": usd.get("market_cap", 0),
            "change_24h": round(usd.get("percent_change_24h", 0) or 0, 1),
            "change_7d": round(usd.get("percent_change_7d", 0) or 0, 1),
            "high_24h": None,
            "low_24h": None,
            "ath": usd.get("ath_price"),
            "atl": None,
        }
    return prices if prices else None


def fetch_prices() -> dict:
    prices = _fetch_prices_coingecko()
    if prices:
        return prices
    log.warning("CoinGecko prices failed, falling back to CoinPaprika...")
    return _fetch_prices_paprika() or {}


# ---------------------------------------------------------------------------
# Global market data
# ---------------------------------------------------------------------------

def _fetch_global_coingecko() -> dict | None:
    data = api_get("https://api.coingecko.com/api/v3/global")
    if not data or "data" not in data:
        return None
    g = data["data"]
    return {
        "total_market_cap": g["total_market_cap"].get("usd", 0),
        "total_volume_24h": g["total_volume"].get("usd", 0),
        "market_cap_change_24h": round(g.get("market_cap_change_percentage_24h_usd", 0), 1),
        "btc_dominance": round(g["market_cap_percentage"].get("btc", 0), 1),
        "eth_dominance": round(g["market_cap_percentage"].get("eth", 0), 1),
        "sol_dominance": round(g["market_cap_percentage"].get("sol", 0), 2),
    }


def _fetch_global_paprika() -> dict | None:
    data = api_get("https://api.coinpaprika.com/v1/global")
    if not data:
        return None
    return {
        "total_market_cap": data.get("market_cap_usd", 0),
        "total_volume_24h": data.get("volume_24h_usd", 0),
        "market_cap_change_24h": round(data.get("market_cap_change_24h", 0) or 0, 1),
        "btc_dominance": round(data.get("bitcoin_dominance_percentage", 0), 1),
        "eth_dominance": 0,
        "sol_dominance": 0,
    }


def fetch_global() -> dict:
    result = _fetch_global_coingecko()
    if result:
        return result
    log.warning("CoinGecko global failed, falling back to CoinPaprika...")
    return _fetch_global_paprika() or {}


# ---------------------------------------------------------------------------
# Trending
# ---------------------------------------------------------------------------

def _fetch_trending_coingecko() -> list | None:
    data = api_get("https://api.coingecko.com/api/v3/search/trending")
    if not data or "coins" not in data:
        return None
    trending = []
    for item in data["coins"]:
        c = item["item"]
        rank = c.get("market_cap_rank")
        # Drop unranked and obscure tokens (keep top-100 only)
        if not isinstance(rank, int) or rank > 100:
            continue
        trending.append({
            "name": c["name"],
            "symbol": c["symbol"],
            "market_cap_rank": rank,
        })
        if len(trending) >= 10:
            break
    return trending


def _fetch_trending_paprika() -> list | None:
    """Top movers by 24h change as a trending proxy."""
    data = api_get("https://api.coinpaprika.com/v1/tickers", params={"limit": 100})
    if not data or not isinstance(data, list):
        return None
    for coin in data:
        usd = coin.get("quotes", {}).get("USD", {})
        coin["_abs_change"] = abs(usd.get("percent_change_24h", 0) or 0)
    data.sort(key=lambda x: x["_abs_change"], reverse=True)
    return [
        {
            "name": coin.get("name", "Unknown"),
            "symbol": coin.get("symbol", "?"),
            "market_cap_rank": coin.get("rank"),
        }
        for coin in data[:10]
    ]


def fetch_trending() -> list:
    result = _fetch_trending_coingecko()
    if result:
        return result
    log.warning("CoinGecko trending failed, falling back to CoinPaprika...")
    return _fetch_trending_paprika() or []


# ---------------------------------------------------------------------------
# SOL Technicals
# ---------------------------------------------------------------------------

def _fetch_sol_ohlc_coingecko() -> list | None:
    """Fetch ~365 daily SOL closes from CoinGecko's market_chart endpoint.

    NOTE: CoinGecko's `ohlc` endpoint with days=365 returns 4-day candles on the
    free tier (≈91 points), which is not enough for a 200-day moving average.
    `market_chart` with days=365 returns daily granularity (≈366 points) on the
    free tier because days>90 automatically yields daily points.
    """
    data = api_get(
        "https://api.coingecko.com/api/v3/coins/solana/market_chart",
        params={"vs_currency": "usd", "days": "365"},
    )
    if not data or "prices" not in data:
        return None
    points = data.get("prices") or []
    if len(points) < 50:
        return None
    # market_chart returns [[timestamp_ms, price], ...]. We treat each point
    # as a daily close for MA/RSI calculations.
    return [{"ts": p[0] / 1000, "close": p[1]} for p in points]


def _fetch_sol_ohlc_paprika() -> list | None:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=365)
    data = api_get(
        "https://api.coinpaprika.com/v1/coins/sol-solana/ohlcv/historical",
        params={
            "start": start.strftime("%Y-%m-%d"),
            "end": end.strftime("%Y-%m-%d"),
        },
    )
    if not data or not isinstance(data, list) or len(data) < 50:
        return None
    candles = []
    for candle in data:
        if "close" not in candle:
            continue
        ts = candle.get("timestamp", candle.get("time_open", ""))
        # Parse ISO timestamp to unix timestamp
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            unix_ts = dt.timestamp()
        except (ValueError, AttributeError):
            unix_ts = 0
        candles.append({"ts": unix_ts, "close": candle["close"]})
    return candles if candles else None


def _calc_monthly_returns(candles: list) -> list:
    """Group daily closes by month; return (last_close - first_close)/first_close * 100."""
    from collections import OrderedDict

    months = OrderedDict()
    for c in candles:
        dt = datetime.fromtimestamp(c["ts"], tz=timezone.utc)
        key = dt.strftime("%Y-%m")
        if key not in months:
            months[key] = {"first_close": c["close"], "last_close": c["close"]}
        else:
            months[key]["last_close"] = c["close"]

    results = []
    for month_key, vals in months.items():
        first_close = vals["first_close"]
        last_close = vals["last_close"]
        if first_close and first_close != 0:
            ret = round((last_close - first_close) / first_close * 100, 1)
        else:
            ret = 0.0
        results.append({"month": month_key, "return_pct": ret})
    return results


def _calc_rsi(prices: list, period: int = 14) -> float | None:
    if len(prices) < period + 1:
        return None
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    recent = deltas[-period:]
    gains = [d for d in recent if d > 0]
    losses = [-d for d in recent if d < 0]
    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 0.001
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def fetch_sol_technicals(prices: dict) -> dict:
    candles = _fetch_sol_ohlc_coingecko()
    if not candles:
        log.warning("CoinGecko OHLC failed, falling back to CoinPaprika...")
        candles = _fetch_sol_ohlc_paprika()
    if not candles:
        return {"note": "Insufficient data for technicals"}

    # Extract close prices from candle dicts
    closes = [c["close"] for c in candles]

    current = closes[-1] if closes else prices.get("SOL", {}).get("price", 0)

    ma_50 = round(sum(closes[-50:]) / 50, 2) if len(closes) >= 50 else None
    ma_200 = round(sum(closes[-200:]) / 200, 2) if len(closes) >= 200 else None
    rsi = _calc_rsi(closes, 14)
    high_52w = max(closes) if closes else None
    low_52w = min(closes) if closes else None

    if ma_50 and ma_200:
        if current > ma_50 > ma_200:
            ma_signal = "Bullish (Price > 50MA > 200MA)"
        elif current < ma_50 < ma_200:
            ma_signal = "Bearish (Price < 50MA < 200MA)"
        else:
            ma_signal = "Mixed"
    else:
        ma_signal = "Insufficient data"

    monthly_returns = _calc_monthly_returns(candles)

    return {
        "price": current,
        "ma_50": ma_50,
        "ma_200": ma_200,
        "rsi_14": rsi,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "ma_signal": ma_signal,
        "monthly_returns": monthly_returns,
    }


# ---------------------------------------------------------------------------
# Pipeline entry point
# ---------------------------------------------------------------------------

def run() -> dict:
    log.info("Fetching market data...")

    fear_greed = fetch_fear_greed()
    log.info(f"  F&G: {fear_greed['value']} — {fear_greed['label']}")

    prices = fetch_prices()
    log.info(f"  Prices: {len(prices)} coins")

    global_data = fetch_global()
    log.info(f"  Global: ${global_data.get('total_market_cap', 0)/1e12:.2f}T market cap")

    trending = fetch_trending()
    log.info(f"  Trending: {len(trending)} coins")

    technicals = fetch_sol_technicals(prices)
    log.info(f"  SOL Technicals: RSI {technicals.get('rsi_14')}")

    result = {
        "timestamp": now_utc(),
        "fear_greed": fear_greed,
        "prices": prices,
        "global": global_data,
        "trending": trending,
        "sol_technicals": technicals,
    }

    save_json(result, "market.json")
    log.info("Market data saved.")
    return result


if __name__ == "__main__":
    run()
