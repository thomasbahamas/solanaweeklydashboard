"""Send dashboard summary to Telegram."""

import requests
from config import (
    load_json, get_logger,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
)

log = get_logger("telegram")


def send_message(text: str) -> bool:
    """Send a message via Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram not configured — skipping")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        log.info("Telegram message sent.")
        return True
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
        return False


def build_summary() -> str:
    """Build a concise Telegram summary from compiled + narrative data."""
    compiled = load_json("compiled.json")
    narrative = load_json("narrative.json")

    if not compiled:
        return "⚠️ Dashboard pipeline failed — no data available."

    market = compiled.get("market", {})
    solana = compiled.get("solana", {})
    fg = market.get("fear_greed", {})
    prices = market.get("prices", {})
    global_data = market.get("global", {})
    dex = solana.get("dex_volumes", {})
    sol_tvl = solana.get("solana_tvl", {})

    lines = [
        "<b>SOLANA FLOOR DAILY</b>",
        f"F&G: {fg.get('value', 'N/A')} — {fg.get('label', '')}",
        "",
    ]

    # Key prices
    for ticker in ["BTC", "ETH", "SOL"]:
        if ticker in prices:
            p = prices[ticker]
            arrow = "🟢" if p['change_24h'] > 0 else "🔴"
            lines.append(f"{arrow} {ticker}: ${p['price']:,.2f} ({p['change_24h']:+.1f}%)")

    lines.append(f"\nMcap: ${global_data.get('total_market_cap', 0)/1e12:.2f}T")
    lines.append(f"SOL TVL: ${sol_tvl.get('current', 0)/1e9:.2f}B")
    lines.append(f"DEX Vol: ${dex.get('combined_24h', 0)/1e9:.2f}B")

    # Top pitch
    pitches = narrative.get("story_pitches", [])
    if pitches:
        lines.append(f"\n📝 <b>Top Story:</b> {pitches[0].get('title', '')}")

    # Signal summary
    signal = narrative.get("the_signal", {})
    context = signal.get("market_context", "")
    if context:
        # First sentence only
        first_sentence = context.split(".")[0] + "."
        lines.append(f"\n💡 {first_sentence}")

    lines.append("\n🔗 Dashboard: [link to GitHub Pages]")

    return "\n".join(lines)


def run():
    summary = build_summary()
    send_message(summary)


if __name__ == "__main__":
    run()
