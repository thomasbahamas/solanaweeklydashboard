"""Fetch Solana network upgrade and infrastructure data.

Tracks:
- Validator client adoption (Agave, Firedancer, Jito-Solana) — from Solana RPC
- SIMDs (Solana Improvement Documents) — from GitHub API
- Network upgrade news (Alpenglow, Harmonic, DoubleZero, Jito BAM, etc.)
- Key infrastructure metrics
"""

from config import api_get, rpc_post, save_json, get_logger, now_utc, CRYPTOPANIC_API_KEY

log = get_logger("fetch_upgrades")


# ---------------------------------------------------------------------------
# Validator client adoption — real on-chain data
# ---------------------------------------------------------------------------

# Known version prefixes/patterns for client identification
CLIENT_PATTERNS = {
    "firedancer": {
        "name": "Firedancer",
        "match": lambda v: "fd_" in v.lower() or v.startswith("0.") and "firedancer" in v.lower(),
    },
    "frankendancer": {
        "name": "Frankendancer",
        "match": lambda v: "frankendancer" in v.lower(),
    },
    "jito": {
        "name": "Jito-Solana",
        "match": lambda v: "jito" in v.lower(),
    },
}


def classify_client(version: str) -> str:
    """Classify a validator's client from its version string.

    Version patterns:
    - Agave (Anza): "2.x.x", "3.x.x" (standard semver, major >= 1)
    - Firedancer (Jump): "0.8xx.xxxxx" format (e.g., 0.814.30108)
      These use 0.{build}.{revision} numbering.
    - Jito-Solana: Usually same version as Agave but may include "jito"
    - Unknown/other: "unknown", or unrecognized patterns
    """
    v = version.lower().strip()

    # Explicit labels
    if "frankendancer" in v or "fd_" in v:
        return "Frankendancer"
    if "firedancer" in v:
        return "Firedancer"
    if "jito" in v:
        return "Jito-Solana"

    if v in ("unknown", ""):
        return "Unknown"

    # Parse version numbers
    try:
        parts = v.split(".")
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0

        # Firedancer pattern: major=0, minor >= 100 (e.g., 0.814.30108)
        # These have very high minor/patch numbers
        if major == 0 and minor >= 100:
            return "Firedancer"

        # Standard Agave: major >= 1 (e.g., 2.2.1, 3.1.10)
        if major >= 1:
            return "Agave"

        # 0.1.x could be early Firedancer test builds
        if major == 0 and minor <= 10:
            return "Firedancer"

    except (ValueError, IndexError):
        pass

    return "Agave"


def fetch_validator_adoption() -> dict:
    """Fetch validator client distribution using Solana RPC.

    Uses getClusterNodes for version info + getVoteAccounts for stake weights.
    Cross-references to compute stake-weighted client adoption percentages.
    """
    # Step 1: Get all cluster nodes with their versions
    nodes = rpc_post("getClusterNodes")
    if not nodes:
        log.warning("Failed to fetch cluster nodes")
        return {}

    # Build pubkey -> version map
    version_map = {}
    for node in nodes:
        pubkey = node.get("pubkey", "")
        version = node.get("version") or "unknown"
        if pubkey:
            version_map[pubkey] = version

    log.info(f"  Cluster nodes: {len(version_map)} total")

    # Step 2: Get vote accounts with stake weights
    vote_data = rpc_post("getVoteAccounts")
    if not vote_data:
        log.warning("Failed to fetch vote accounts")
        return {}

    current_validators = vote_data.get("current", [])
    delinquent_validators = vote_data.get("delinquent", [])
    all_validators = current_validators + delinquent_validators

    log.info(f"  Vote accounts: {len(current_validators)} current, {len(delinquent_validators)} delinquent")

    # Step 3: Cross-reference — compute stake-weighted client distribution
    client_stake = {}    # client_name -> total_lamports
    client_count = {}    # client_name -> validator_count
    version_stakes = {}  # full_version -> total_lamports
    total_stake = 0

    for v in all_validators:
        node_pubkey = v.get("nodePubkey", "")
        stake = v.get("activatedStake", 0)
        total_stake += stake

        version = version_map.get(node_pubkey, "unknown")
        client = classify_client(version)

        client_stake[client] = client_stake.get(client, 0) + stake
        client_count[client] = client_count.get(client, 0) + 1

        # Track top versions
        version_stakes[version] = version_stakes.get(version, 0) + stake

    # Step 4: Build adoption percentages
    if total_stake == 0:
        return {}

    clients = []
    for name in sorted(client_stake.keys(), key=lambda k: client_stake[k], reverse=True):
        stake = client_stake[name]
        count = client_count.get(name, 0)
        pct = round(stake / total_stake * 100, 2)
        clients.append({
            "name": name,
            "stake_pct": pct,
            "validator_count": count,
            "stake_sol": round(stake / 1e9, 0),  # lamports to SOL
        })

    # Top versions by stake
    top_versions = sorted(version_stakes.items(), key=lambda x: x[1], reverse=True)[:10]
    versions = []
    for ver, stake in top_versions:
        pct = round(stake / total_stake * 100, 2)
        client = classify_client(ver)
        versions.append({
            "version": ver,
            "client": client,
            "stake_pct": pct,
        })

    return {
        "total_validators": len(all_validators),
        "total_stake_sol": round(total_stake / 1e9, 0),
        "clients": clients,
        "top_versions": versions,
    }


# ---------------------------------------------------------------------------
# Infrastructure tracker — status + context for major upgrades
# ---------------------------------------------------------------------------

def build_infra_metrics(adoption: dict) -> dict:
    """Build infrastructure metric cards using real adoption data.

    Only includes cards for which we have a live metric — showing empty
    placeholder cards for Jito/DoubleZero/Alpenglow/Harmonic was making the
    Infrastructure section look half-dead. Roadmap items (no live metric)
    are still exposed via the separate `roadmap` key so the dashboard can
    render them as a compact list instead of metric-style cards.

    Jito-Solana is intentionally omitted: Jito's validator client reports
    the same version strings as stock Agave, so there is no reliable way
    to distinguish them via `getClusterNodes` alone — the number was
    always 0%, which is misleading.
    """
    clients_by_name = {}
    for c in adoption.get("clients", []):
        clients_by_name[c["name"]] = c

    firedancer = clients_by_name.get("Firedancer", {})
    frankendancer = clients_by_name.get("Frankendancer", {})
    agave = clients_by_name.get("Agave", {})

    # Combined Firedancer family
    fd_pct = firedancer.get("stake_pct", 0) + frankendancer.get("stake_pct", 0)
    fd_count = firedancer.get("validator_count", 0) + frankendancer.get("validator_count", 0)

    result = {}

    if agave.get("stake_pct"):
        result["agave"] = {
            "name": "Agave (Anza)",
            "description": "Primary Solana validator client maintained by Anza",
            "metric_label": "Stake-Weighted Adoption",
            "metric_value": f"{agave['stake_pct']:.1f}%",
            "metric_detail": f"{agave.get('validator_count', 0)} validators",
            "status": "Live on mainnet",
            "url": "https://github.com/anza-xyz/agave",
        }

    if fd_pct:
        result["firedancer"] = {
            "name": "Firedancer / Frankendancer",
            "description": "Jump Crypto's independent validator client — key to client diversity",
            "metric_label": "Stake-Weighted Adoption",
            "metric_value": f"{fd_pct:.1f}%",
            "metric_detail": f"{fd_count} validators",
            "status": "Live on mainnet",
            "url": "https://github.com/firedancer-io/firedancer",
        }

    return result


def build_roadmap() -> list:
    """Static list of upcoming/roadmap infrastructure items.

    These don't have live metrics so rendering them as empty metric cards
    looks broken. They're exposed here as a lightweight list for a dedicated
    roadmap renderer.
    """
    return [
        {
            "name": "DoubleZero (D0)",
            "description": "Dedicated high-performance network backbone for validators",
            "status": "Live on mainnet",
            "url": "https://doublezero.xyz",
        },
        {
            "name": "Alpenglow",
            "description": "Next-gen consensus protocol replacing Tower BFT — faster finality",
            "status": "In development",
            "url": "https://www.anza.xyz/blog/alpenglow-a-new-consensus-for-solana",
        },
        {
            "name": "Harmonic",
            "description": "Network performance improvements for transaction processing",
            "status": "In development",
            "url": None,
        },
    ]


# ---------------------------------------------------------------------------
# SIMDs from GitHub
# ---------------------------------------------------------------------------

def fetch_simds() -> dict:
    """Fetch recent SIMDs from the Solana Foundation GitHub repo."""
    prs = api_get(
        "https://api.github.com/repos/solana-foundation/solana-improvement-documents/pulls",
        params={"state": "all", "sort": "updated", "direction": "desc", "per_page": 30},
    )
    if not prs:
        return {"recent": [], "stats": {}}

    recent = []
    open_count = 0
    merged_count = 0
    closed_count = 0

    for pr in prs:
        state = pr.get("state", "")
        merged = pr.get("merged_at") is not None
        if state == "open":
            open_count += 1
            display_state = "open"
        elif merged:
            merged_count += 1
            display_state = "merged"
        else:
            closed_count += 1
            display_state = "closed"

        title = pr.get("title", "")
        labels = [l.get("name", "") for l in pr.get("labels", [])]

        recent.append({
            "title": title,
            "number": pr.get("number"),
            "state": display_state,
            "author": pr.get("user", {}).get("login", ""),
            "updated": pr.get("updated_at", "")[:10],
            "url": pr.get("html_url", ""),
            "labels": labels,
        })

    return {
        "recent": recent[:15],
        "stats": {
            "open": open_count,
            "merged": merged_count,
            "closed": closed_count,
            "total_fetched": len(prs),
        },
    }


# ---------------------------------------------------------------------------
# Upgrade news from CryptoPanic
# ---------------------------------------------------------------------------

def fetch_upgrade_news() -> list:
    """Filter CryptoPanic for network upgrade keywords."""
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

    upgrade_keywords = [
        "upgrade", "simd", "alpenglow", "harmonic", "doublezero", "double zero",
        "jito", "bam", "firedancer", "validator", "consensus", "finality",
        "turbine", "quic", "tps", "block time", "block size", "agave",
        "anza", "lattice", "frankendancer",
    ]

    stories = []
    for item in data["results"]:
        title = item.get("title", "").lower()
        if any(kw in title for kw in upgrade_keywords):
            stories.append({
                "title": item.get("title", ""),
                "source": item.get("source", {}).get("title", "Unknown"),
                "url": item.get("url", ""),
                "published": item.get("published_at", ""),
            })
    return stories[:10]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> dict:
    log.info("Fetching network upgrade data...")

    # Validator client adoption (live from RPC)
    adoption = fetch_validator_adoption()
    if adoption:
        for c in adoption.get("clients", []):
            log.info(f"  {c['name']}: {c['stake_pct']}% stake ({c['validator_count']} validators)")
    else:
        log.warning("  Could not fetch validator adoption data")

    # Build infra metrics using real adoption data
    infrastructure = build_infra_metrics(adoption)

    # SIMDs from GitHub
    simds = fetch_simds()
    log.info(f"  SIMDs: {simds['stats'].get('open', 0)} open, {simds['stats'].get('merged', 0)} merged")

    # Upgrade news
    upgrade_news = fetch_upgrade_news()
    log.info(f"  Upgrade news: {len(upgrade_news)} stories")

    result = {
        "timestamp": now_utc(),
        "validator_adoption": adoption,
        "simds": simds,
        "upgrade_news": upgrade_news,
        "infrastructure": infrastructure,
        "roadmap": build_roadmap(),
    }

    save_json(result, "upgrades.json")
    log.info("Upgrade data saved.")
    return result


if __name__ == "__main__":
    run()
