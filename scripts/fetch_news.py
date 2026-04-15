"""Fetch news via CryptoPanic API and RSS feeds as CT proxy."""

import feedparser
from config import (
    api_get, save_json, get_logger, now_utc,
    CRYPTOPANIC_API_KEY,
)

log = get_logger("fetch_news")

# RSS feeds as Twitter/CT proxy
RSS_FEEDS = {
    # Solana-specific
    "Solana Foundation": "https://solana.com/news/rss.xml",
    # Major crypto news
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "The Block": "https://www.theblock.co/rss.xml",
    "Blockworks": "https://blockworks.co/feed",
    "Decrypt": "https://decrypt.co/feed",
    "CoinTelegraph": "https://cointelegraph.com/rss",
    "DL News": "https://www.dlnews.com/arc/outboundfeeds/rss/",
    # DeFi / Solana ecosystem
    "Messari": "https://messari.io/rss",
}


def fetch_cryptopanic(filter_currencies: str = "SOL") -> list:
    """CryptoPanic API — aggregated crypto news with sentiment.

    Note: the `filter=important` parameter is intentionally not used because
    it returns near-empty results most days; we rely on sorting by default
    (recency) and cap at 15 items.
    """
    if not CRYPTOPANIC_API_KEY:
        log.warning("No CryptoPanic API key — skipping")
        return []

    data = api_get(
        "https://cryptopanic.com/api/v1/posts/",
        params={
            "auth_token": CRYPTOPANIC_API_KEY,
            "currencies": filter_currencies,
            "kind": "news",
            "public": "true",
        },
    )
    if not data or "results" not in data:
        return []

    stories = []
    for item in data["results"][:15]:
        votes = item.get("votes", {})
        stories.append({
            "title": item.get("title", ""),
            "source": item.get("source", {}).get("title", "Unknown"),
            "url": item.get("url", ""),
            "published": item.get("published_at", ""),
            "currencies": [c.get("code", "") for c in item.get("currencies", [])],
            "sentiment": {
                "positive": votes.get("positive", 0),
                "negative": votes.get("negative", 0),
                "important": votes.get("important", 0),
                "liked": votes.get("liked", 0),
            },
            "kind": item.get("kind", "news"),
        })
    return stories


def fetch_cryptopanic_general() -> list:
    """CryptoPanic general crypto news (not SOL-filtered)."""
    if not CRYPTOPANIC_API_KEY:
        return []

    data = api_get(
        "https://cryptopanic.com/api/v1/posts/",
        params={
            "auth_token": CRYPTOPANIC_API_KEY,
            "kind": "news",
            "filter": "hot",
            "public": "true",
        },
    )
    if not data or "results" not in data:
        return []

    stories = []
    for item in data["results"][:10]:
        stories.append({
            "title": item.get("title", ""),
            "source": item.get("source", {}).get("title", "Unknown"),
            "url": item.get("url", ""),
            "published": item.get("published_at", ""),
            "currencies": [c.get("code", "") for c in item.get("currencies", [])],
        })
    return stories


def fetch_rss_feeds() -> list:
    """Parse RSS feeds for recent crypto/Solana news."""
    all_entries = []

    for source_name, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:
                all_entries.append({
                    "title": entry.get("title", ""),
                    "source": source_name,
                    "url": entry.get("link", ""),
                    "published": entry.get("published", ""),
                    "summary": (entry.get("summary", "") or "")[:200],
                })
        except Exception as e:
            log.warning(f"RSS feed {source_name} failed: {e}")

    # Sort by published date (newest first), dedupe by title similarity
    all_entries.sort(key=lambda x: x.get("published", ""), reverse=True)

    # Basic dedup — skip entries with very similar titles
    seen_titles = set()
    deduped = []
    for entry in all_entries:
        title_key = entry["title"].lower()[:50]
        if title_key not in seen_titles:
            seen_titles.add(title_key)
            deduped.append(entry)

    return deduped[:20]


# YouTube channels to track (channel_id -> display name)
YOUTUBE_CHANNELS = {
    "UCXq4wViP47PV1T3GxOEBpUw": "Solana Foundation",
    "UCqK_GSMbpiV8spgD3ZGloSw": "Coin Bureau",
    "UCAl9Ld79qaZxp7JnEufyLRg": "Bankless",
    "UCJgHxpqfcOB0dGkEMzWlZbA": "Altcoin Daily",
    "UCRvqjQPSeaWn-uEx-w0XOIg": "CoinDesk",
    "UC4FVvg_HfyflVKTMZMjoYeg": "Paul Barron Network",
}


def fetch_youtube_videos() -> list:
    """Fetch latest videos from top crypto/Solana YouTube channels via RSS."""
    all_videos = []
    for channel_id, name in YOUTUBE_CHANNELS.items():
        try:
            url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
            feed = feedparser.parse(url)
            for entry in feed.entries[:3]:
                title = entry.get("title", "")
                # Filter for Solana-relevant content
                title_lower = title.lower()
                sol_relevant = any(kw in title_lower for kw in [
                    "solana", "sol", "jito", "jupiter", "raydium", "phantom",
                    "crypto", "defi", "market", "altcoin", "bull", "bear",
                    "bitcoin", "ethereum", "token", "blockchain", "web3",
                ])
                if sol_relevant:
                    all_videos.append({
                        "title": title,
                        "channel": name,
                        "url": entry.get("link", ""),
                        "published": entry.get("published", ""),
                        "video_id": entry.get("yt_videoid", ""),
                    })
        except Exception as e:
            log.warning(f"YouTube RSS {name} failed: {e}")

    all_videos.sort(key=lambda x: x.get("published", ""), reverse=True)
    return all_videos[:12]


def categorize_stories(stories: list) -> list:
    """Add category tags based on keywords in titles."""
    categories = {
        "Whale": ["whale", "transfer", "moved", "staked", "unstaked", "million sol"],
        "DeFi": ["defi", "tvl", "dex", "swap", "lend", "borrow", "yield", "jupiter", "raydium", "kamino"],
        "Solana": ["solana", "sol ", "sol,", "phantom", "jito", "marinade"],
        "Bitcoin": ["bitcoin", "btc", "mining", "halving"],
        "Regulation": ["sec", "regulation", "lawsuit", "legal", "compliance", "etf"],
        "AI": ["ai agent", "artificial intelligence", "machine learning", "llm"],
        "Stablecoin": ["stablecoin", "usdc", "usdt", "tether", "circle"],
        "NFT": ["nft", "collection", "mint", "pudgy"],
        "Macro": ["fed", "interest rate", "cpi", "inflation", "employment", "gdp"],
    }

    for story in stories:
        title_lower = story["title"].lower()
        story["categories"] = []
        for cat, keywords in categories.items():
            if any(kw in title_lower for kw in keywords):
                story["categories"].append(cat)
        if not story["categories"]:
            story["categories"] = ["General"]

    return stories


def run() -> dict:
    log.info("Fetching news...")

    solana_news = fetch_cryptopanic("SOL")
    log.info(f"  CryptoPanic SOL: {len(solana_news)} stories")

    general_news = fetch_cryptopanic_general()
    log.info(f"  CryptoPanic general: {len(general_news)} stories")

    rss_news = fetch_rss_feeds()
    log.info(f"  RSS feeds: {len(rss_news)} stories")

    youtube = fetch_youtube_videos()
    log.info(f"  YouTube: {len(youtube)} videos")

    # Merge and categorize
    all_stories = categorize_stories(solana_news + general_news)
    rss_categorized = categorize_stories(rss_news)

    # Fallback: if CryptoPanic returned nothing (API key stale / rate-limited /
    # v1 endpoint deprecated), promote Solana-tagged RSS items into solana_news
    # so the AI narrative and dashboard news panels are never empty.
    if not solana_news:
        promoted = [
            s for s in rss_categorized
            if "Solana" in s.get("categories", []) or "DeFi" in s.get("categories", [])
        ]
        if promoted:
            log.info(f"  CryptoPanic empty — promoting {len(promoted)} RSS items to solana_news")
            solana_news = promoted[:15]
    if not general_news and rss_categorized:
        general_news = [s for s in rss_categorized if s not in solana_news][:10]

    result = {
        "timestamp": now_utc(),
        "solana_news": solana_news,
        "general_news": general_news,
        "rss_feeds": rss_categorized,
        "youtube_videos": youtube,
        "total_stories": len(solana_news) + len(general_news) + len(rss_news) + len(youtube),
    }

    save_json(result, "news.json")
    log.info(f"News saved: {result['total_stories']} total stories.")
    return result


if __name__ == "__main__":
    run()
