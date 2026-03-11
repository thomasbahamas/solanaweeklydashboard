"""Fetch whale intelligence data — staking flows, large transfers, known wallets.

NOTE: Full whale tracking requires a paid indexer (Helius, Birdeye, etc).
This module uses free sources: DeFiLlama staking flows + CryptoPanic whale news.
Extend with Helius/Birdeye API if budget allows.
"""

from config import api_get, save_json, get_logger, now_utc, CRYPTOPANIC_API_KEY

log = get_logger("fetch_whales")


def fetch_whale_news() -> list:
    """CryptoPanic whale-tagged news."""
    if not CRYPTOPANIC_API_KEY:
        return []

    data = api_get(
        "https://cryptopanic.com/api/v1/posts/",
        params={
            "auth_token": CRYPTOPANIC_API_KEY,
            "currencies": "SOL",
            "kind": "news",
            "public": "true",
        },
    )
    if not data or "results" not in data:
        return []

    whale_stories = []
    whale_keywords = ["whale", "transfer", "moved", "staked", "unstaked", "million", "billion", "large"]
    for item in data["results"]:
        title = item.get("title", "").lower()
        if any(kw in title for kw in whale_keywords):
            whale_stories.append({
                "title": item.get("title", ""),
                "source": item.get("source", {}).get("title", "Unknown"),
                "url": item.get("url", ""),
                "published": item.get("published_at", ""),
            })
    return whale_stories[:10]


def fetch_staking_flows() -> dict:
    """DeFiLlama liquid staking data for Solana — proxy for institutional flows."""
    data = api_get("https://api.llama.fi/protocols")
    if not data:
        return {}

    staking_protocols = []
    for p in data:
        if p.get("category") in ("Liquid Staking", "Staking Pool") and "Solana" in p.get("chains", []):
            staking_protocols.append({
                "name": p.get("name", "Unknown"),
                "tvl": p.get("tvl", 0),
                "change_1d": round(p.get("change_1d", 0) or 0, 1),
                "change_7d": round(p.get("change_7d", 0) or 0, 1),
            })

    staking_protocols.sort(key=lambda x: x["tvl"], reverse=True)
    total_staked = sum(p["tvl"] for p in staking_protocols)

    return {
        "total_staked_tvl": total_staked,
        "protocols": staking_protocols[:10],
    }


def run() -> dict:
    log.info("Fetching whale intelligence...")

    whale_news = fetch_whale_news()
    log.info(f"  Whale news: {len(whale_news)} stories")

    staking = fetch_staking_flows()
    log.info(f"  Staking TVL: ${staking.get('total_staked_tvl', 0)/1e9:.2f}B")

    result = {
        "timestamp": now_utc(),
        "whale_news": whale_news,
        "staking_flows": staking,
        "note": "Full whale tracking requires Helius/Birdeye API. Currently using news + staking flows.",
    }

    save_json(result, "whales.json")
    log.info("Whale data saved.")
    return result


if __name__ == "__main__":
    run()
