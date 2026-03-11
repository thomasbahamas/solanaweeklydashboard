"""Fetch market overview data: prices, dominance, F&G, trending, technicals."""

from config import (
    api_get, save_json, get_logger, now_utc,
    WATCHLIST,
)

log = get_logger("fetch_market")


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


def fetch_prices() -> dict:
    """CoinGecko prices, market cap, 24h change for watchlist."""
    ids = ",".join(WATCHLIST.keys())
    data = api_get(
        "https://api.coingecko.com/api/v3/coins/markets",
        params={
            "vs_currency": "usd",
            "ids": ids,
            "order": "market_cap_desc",
            "sparkline": "false",
            "price_change_percentage": "24h,7d",
        },
    )
    if not data:
        return {}

    prices = {}
    for coin in data:
        ticker = WATCHLIST.get(coin["id"], coin["symbol"].upper())
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
        }
    return prices


def fetch_global() -> dict:
    """CoinGecko global market data: total market cap, BTC/SOL dominance."""
    data = api_get("https://api.coingecko.com/api/v3/global")
    if not data or "data" not in data:
        return {}
    g = data["data"]
    return {
        "total_market_cap": g["total_market_cap"].get("usd", 0),
        "total_volume_24h": g["total_volume"].get("usd", 0),
        "market_cap_change_24h": round(g.get("market_cap_change_percentage_24h_usd", 0), 1),
        "btc_dominance": round(g["market_cap_percentage"].get("btc", 0), 1),
        "eth_dominance": round(g["market_cap_percentage"].get("eth", 0), 1),
        "sol_dominance": round(g["market_cap_percentage"].get("sol", 0), 2),
    }


def fetch_trending() -> list:
    """CoinGecko trending coins."""
    data = api_get("https://api.coingecko.com/api/v3/search/trending")
    if not data or "coins" not in data:
        return []
    trending = []
    for item in data["coins"][:10]:
        c = item["item"]
        trending.append({
            "name": c["name"],
            "symbol": c["symbol"],
            "market_cap_rank": c.get("market_cap_rank"),
        })
    return trending


def fetch_sol_technicals(prices: dict) -> dict:
    """Basic SOL technical analysis using CoinGecko OHLC data."""
    # Get 90-day OHLC for moving averages
    ohlc = api_get(
        "https://api.coingecko.com/api/v3/coins/solana/ohlc",
        params={"vs_currency": "usd", "days": "200"},
    )
    if not ohlc or len(ohlc) < 50:
        return {"note": "Insufficient data for technicals"}

    closes = [candle[4] for candle in ohlc]  # close prices
    current = closes[-1] if closes else prices.get("SOL", {}).get("price", 0)

    # Simple moving averages
    ma_50 = round(sum(closes[-50:]) / 50, 2) if len(closes) >= 50 else None
    ma_200 = round(sum(closes[-200:]) / 200, 2) if len(closes) >= 200 else None

    # RSI (14-period)
    rsi = _calc_rsi(closes, 14)

    # 52-week range from available data
    high_52w = max(closes) if closes else None
    low_52w = min(closes) if closes else None

    # MA signal
    if ma_50 and ma_200:
        if current > ma_50 > ma_200:
            ma_signal = "Bullish (Price > 50MA > 200MA)"
        elif current < ma_50 < ma_200:
            ma_signal = "Bearish (Price < 50MA < 200MA)"
        else:
            ma_signal = "Mixed"
    else:
        ma_signal = "Insufficient data"

    return {
        "price": current,
        "ma_50": ma_50,
        "ma_200": ma_200,
        "rsi_14": rsi,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "ma_signal": ma_signal,
    }


def _calc_rsi(prices: list, period: int = 14) -> float | None:
    """Calculate RSI from price series."""
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
