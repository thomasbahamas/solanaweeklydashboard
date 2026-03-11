"""Fetch Solana ecosystem data: TVL, DEX volumes, protocol rankings, network stats."""

from config import (
    api_get, rpc_post, save_json, get_logger, now_utc,
    TOP_CHAINS,
)

log = get_logger("fetch_solana")


def fetch_chain_tvls() -> list:
    """DeFiLlama TVL by chain — returns top chains."""
    data = api_get("https://api.llama.fi/v2/chains")
    if not data:
        return []

    chains = []
    for chain in data:
        name = chain.get("name", "")
        tvl = chain.get("tvl", 0)
        chains.append({
            "name": name,
            "tvl": tvl,
            "change_1d": chain.get("change_1d"),
            "change_7d": chain.get("change_7d"),
        })

    # Sort by TVL, return top 10 + ensure Solana is included
    chains.sort(key=lambda x: x["tvl"], reverse=True)
    top = chains[:10]
    solana_in_top = any(c["name"] == "Solana" for c in top)
    if not solana_in_top:
        sol = next((c for c in chains if c["name"] == "Solana"), None)
        if sol:
            top.append(sol)
    return top


def fetch_solana_tvl() -> dict:
    """DeFiLlama Solana-specific TVL with history."""
    data = api_get("https://api.llama.fi/v2/historicalChainTvl/Solana")
    if not data or not isinstance(data, list):
        return {"current": 0, "change_1d": 0}

    if len(data) >= 2:
        current = data[-1].get("tvl", 0)
        yesterday = data[-2].get("tvl", 1)
        change = round((current - yesterday) / yesterday * 100, 1) if yesterday else 0
        return {"current": current, "change_1d": change}
    return {"current": data[-1].get("tvl", 0) if data else 0, "change_1d": 0}


def fetch_dex_volumes() -> dict:
    """DeFiLlama DEX volumes — Solana spot + perp."""
    # Overall DEX volumes
    data = api_get("https://api.llama.fi/overview/dexs/solana?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyVolume")

    spot_volume = 0
    spot_change = 0
    top_spot = []

    if data:
        spot_volume = data.get("totalDataChart", [[0, 0]])
        spot_volume = data.get("total24h", 0)
        spot_change = round(data.get("change_1d", 0), 1)

        protocols = data.get("protocols", [])
        protocols.sort(key=lambda x: x.get("total24h", 0), reverse=True)
        for p in protocols[:15]:
            top_spot.append({
                "name": p.get("name", "Unknown"),
                "volume_24h": p.get("total24h", 0),
                "change_1d": round(p.get("change_1d", 0), 1),
            })

    # Perp DEX volumes
    perp_data = api_get("https://api.llama.fi/overview/derivatives/solana?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyVolume")

    perp_volume = 0
    top_perps = []

    if perp_data:
        perp_volume = perp_data.get("total24h", 0)
        protocols = perp_data.get("protocols", [])
        protocols.sort(key=lambda x: x.get("total24h", 0), reverse=True)
        for p in protocols[:10]:
            top_perps.append({
                "name": p.get("name", "Unknown"),
                "volume_24h": p.get("total24h", 0),
                "change_1d": round(p.get("change_1d", 0), 1),
            })

    return {
        "spot_24h": spot_volume,
        "spot_change_1d": spot_change,
        "perp_24h": perp_volume,
        "combined_24h": spot_volume + perp_volume,
        "top_spot": top_spot,
        "top_perps": top_perps,
    }


def fetch_protocol_rankings() -> list:
    """DeFiLlama top Solana protocols by TVL."""
    data = api_get("https://api.llama.fi/protocols")
    if not data:
        return []

    solana_protocols = []
    for p in data:
        chains = p.get("chains", [])
        if "Solana" not in chains:
            continue
        # Get Solana-specific TVL if available
        chain_tvls = p.get("chainTvls", {})
        sol_tvl = chain_tvls.get("Solana", p.get("tvl", 0))
        if isinstance(sol_tvl, dict):
            sol_tvl = sol_tvl.get("tvl", 0) if isinstance(sol_tvl, dict) else 0

        solana_protocols.append({
            "name": p.get("name", "Unknown"),
            "category": p.get("category", "Unknown"),
            "tvl": sol_tvl if isinstance(sol_tvl, (int, float)) else p.get("tvl", 0),
            "change_1d": round(p.get("change_1d", 0) or 0, 1),
            "change_7d": round(p.get("change_7d", 0) or 0, 1),
        })

    solana_protocols.sort(key=lambda x: x["tvl"], reverse=True)
    return solana_protocols[:20]


def fetch_fees() -> dict:
    """DeFiLlama Solana fees."""
    data = api_get("https://api.llama.fi/overview/fees/solana?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyFees")
    if not data:
        return {"total_24h": 0}
    return {
        "total_24h": data.get("total24h", 0),
        "change_1d": round(data.get("change_1d", 0), 1),
    }


def fetch_network_stats() -> dict:
    """Solana RPC network statistics."""
    stats = {}

    # Recent performance (TPS)
    perf = rpc_post("getRecentPerformanceSamples", [5])
    if perf and isinstance(perf, list):
        total_txs = sum(s.get("numTransactions", 0) for s in perf)
        total_slots = sum(s.get("numSlots", 0) for s in perf)
        total_seconds = sum(s.get("samplePeriodSecs", 60) for s in perf)
        non_vote = sum(s.get("numNonVoteTransactions", 0) for s in perf)

        stats["tps_total"] = round(total_txs / total_seconds, 1) if total_seconds else 0
        stats["tps_non_vote"] = round(non_vote / total_seconds, 1) if total_seconds else 0
        stats["vote_pct"] = round((1 - non_vote / total_txs) * 100, 1) if total_txs else 0

    # Epoch info
    epoch = rpc_post("getEpochInfo")
    if epoch:
        stats["epoch"] = epoch.get("epoch")
        stats["slot_index"] = epoch.get("slotIndex")
        stats["slots_in_epoch"] = epoch.get("slotsInEpoch")

    return stats


def fetch_stablecoin_data() -> dict:
    """DeFiLlama stablecoins on Solana."""
    data = api_get("https://stablecoins.llama.fi/stablecoins?includePrices=false")
    if not data or "peggedAssets" not in data:
        return {"total": 0, "breakdown": []}

    solana_stables = []
    total = 0
    for asset in data["peggedAssets"]:
        chains = asset.get("chainCirculating", {})
        if "Solana" in chains:
            sol_data = chains["Solana"]
            # peggedUSD is the key for USD-pegged stablecoins
            amount = 0
            for peg_type in sol_data.values():
                if isinstance(peg_type, (int, float)):
                    amount += peg_type
                elif isinstance(peg_type, dict):
                    amount += sum(v for v in peg_type.values() if isinstance(v, (int, float)))

            if amount > 1_000_000:  # Only show > $1M
                solana_stables.append({
                    "name": asset.get("name", "Unknown"),
                    "symbol": asset.get("symbol", "?"),
                    "amount": amount,
                })
                total += amount

    solana_stables.sort(key=lambda x: x["amount"], reverse=True)
    return {
        "total": total,
        "breakdown": solana_stables[:10],
    }


def run() -> dict:
    log.info("Fetching Solana ecosystem data...")

    chain_tvls = fetch_chain_tvls()
    log.info(f"  Chain TVLs: {len(chain_tvls)} chains")

    solana_tvl = fetch_solana_tvl()
    log.info(f"  Solana TVL: ${solana_tvl['current']/1e9:.2f}B")

    dex_volumes = fetch_dex_volumes()
    log.info(f"  DEX Volume: ${dex_volumes['combined_24h']/1e9:.2f}B combined")

    protocols = fetch_protocol_rankings()
    log.info(f"  Protocols: {len(protocols)} ranked")

    fees = fetch_fees()
    log.info(f"  Fees: ${fees['total_24h']/1e6:.1f}M")

    network = fetch_network_stats()
    log.info(f"  Network: {network.get('tps_total', 'N/A')} TPS")

    stablecoins = fetch_stablecoin_data()
    log.info(f"  Stablecoins: ${stablecoins['total']/1e9:.2f}B")

    result = {
        "timestamp": now_utc(),
        "chain_tvls": chain_tvls,
        "solana_tvl": solana_tvl,
        "dex_volumes": dex_volumes,
        "protocol_rankings": protocols,
        "fees": fees,
        "network": network,
        "stablecoins": stablecoins,
    }

    save_json(result, "solana.json")
    log.info("Solana data saved.")
    return result


if __name__ == "__main__":
    run()
