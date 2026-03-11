"""Compile all fetched data into a single payload for dashboard + AI generation."""

from config import load_json, save_json, get_logger, now_utc

log = get_logger("compile")


def run() -> dict:
    log.info("Compiling data...")

    market = load_json("market.json")
    solana = load_json("solana.json")
    news = load_json("news.json")
    whales = load_json("whales.json")

    compiled = {
        "generated_at": now_utc(),
        "market": market,
        "solana": solana,
        "news": news,
        "whales": whales,
    }

    save_json(compiled, "compiled.json")
    log.info("Compiled data saved.")
    return compiled


if __name__ == "__main__":
    run()
