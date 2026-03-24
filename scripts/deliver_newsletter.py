"""Send the newsletter draft via Kit (ConvertKit) broadcast API.

Reads the generated newsletter from data/newsletter.json and sends it
as a broadcast to all Kit subscribers.
"""

import os
from config import load_json, get_logger, now_utc, api_get
from dotenv import load_dotenv
import requests

load_dotenv()

log = get_logger("deliver_newsletter")

KIT_API_SECRET = os.getenv("KIT_API_SECRET", "")
KIT_API_KEY = os.getenv("KIT_API_KEY", "")

KIT_V3_BASE = "https://api.convertkit.com/v3"


def create_broadcast(subject: str, html_body: str, text_body: str) -> dict | None:
    """Create and send a Kit broadcast."""
    if not KIT_API_SECRET:
        log.error("No KIT_API_SECRET set — cannot send broadcast")
        return None

    # Step 1: Create the broadcast
    log.info("Creating Kit broadcast...")
    resp = requests.post(
        f"{KIT_V3_BASE}/broadcasts",
        json={
            "api_secret": KIT_API_SECRET,
            "subject": subject,
            "content": html_body,
            "description": f"Solana Weekly — {now_utc()[:10]}",
            "public": True,
            "published_at": now_utc()[:19] + "Z",
        },
        timeout=30,
    )

    if resp.status_code not in (200, 201):
        log.error(f"Failed to create broadcast: {resp.status_code} — {resp.text[:300]}")
        return None

    data = resp.json()
    broadcast = data.get("broadcast", {})
    broadcast_id = broadcast.get("id")
    log.info(f"  Broadcast created: ID {broadcast_id}")

    return broadcast


def run() -> dict:
    """Send the newsletter via Kit broadcast."""
    newsletter = load_json("newsletter.json")
    if not newsletter:
        log.error("No newsletter.json found — run generate_newsletter.py first")
        return {}

    subject = newsletter.get("subject", "Solana Weekly")
    html_body = newsletter.get("html_body", "")
    text_body = newsletter.get("text_body", "")

    if not html_body:
        log.error("Newsletter has no HTML body")
        return {}

    log.info(f"Sending newsletter: {subject}")

    broadcast = create_broadcast(subject, html_body, text_body)
    if not broadcast:
        return {"status": "failed"}

    log.info("Newsletter broadcast created successfully.")
    return {
        "status": "sent",
        "broadcast_id": broadcast.get("id"),
        "subject": subject,
        "timestamp": now_utc(),
    }


if __name__ == "__main__":
    run()
