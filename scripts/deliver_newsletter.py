"""Send the newsletter draft via Kit broadcast API (v4).

Reads the generated newsletter from data/newsletter.json and sends it
as a broadcast to all Kit subscribers using the v4 API with send_at
to trigger actual delivery.
"""

import os
from datetime import datetime, timezone
from config import load_json, get_logger, now_utc, api_get
from dotenv import load_dotenv
import requests

load_dotenv()

log = get_logger("deliver_newsletter")

KIT_API_SECRET = os.getenv("KIT_API_SECRET", "").strip()
KIT_API_KEY = os.getenv("KIT_API_KEY", "").strip()

KIT_V4_BASE = "https://api.kit.com/v4"


def create_broadcast(subject: str, html_body: str, text_body: str) -> dict | None:
    """Create and send a Kit broadcast via v4 API."""
    api_key = KIT_API_SECRET or KIT_API_KEY
    if not api_key:
        log.error("No KIT_API_SECRET or KIT_API_KEY set — cannot send broadcast")
        return None

    send_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "subject": subject,
        "content": html_body,
        "description": f"Solana Weekly — {now_utc()[:10]}",
        "public": True,
        "send_at": send_at,
    }

    # Try X-Kit-Api-Key header first, then Bearer token
    auth_methods = [
        {"Content-Type": "application/json", "X-Kit-Api-Key": api_key},
        {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    ]

    resp = None
    for headers in auth_methods:
        auth_type = "Bearer" if "Authorization" in headers else "X-Kit-Api-Key"
        log.info(f"Creating Kit v4 broadcast ({auth_type}, send_at={send_at})...")
        resp = requests.post(
            f"{KIT_V4_BASE}/broadcasts",
            headers=headers,
            json=payload,
            timeout=30,
        )
        if resp.status_code in (200, 201):
            break
        log.warning(f"  {auth_type} auth failed: {resp.status_code} — {resp.text[:200]}")

    if resp.status_code not in (200, 201):
        log.error(f"Failed to create broadcast with all auth methods")
        return None

    data = resp.json()
    broadcast = data.get("broadcast", {})
    broadcast_id = broadcast.get("id")
    log.info(f"  Broadcast created & scheduled: ID {broadcast_id}")

    return broadcast


def run() -> dict:
    """Send the newsletter via Kit broadcast."""
    newsletter = load_json("newsletter.json")
    if not newsletter:
        log.error("No newsletter.json found — run generate_newsletter.py first")
        return {}

    # Readiness gate — generate_newsletter sets `ready: False` when
    # narrative is missing/errored so we don't ship debug strings.
    if newsletter.get("ready") is False:
        reason = newsletter.get("reason", "not ready")
        log.error(f"Newsletter marked NOT ready — aborting send ({reason})")
        return {"status": "skipped", "reason": reason}

    subject = newsletter.get("subject", "Solana Weekly")
    html_body = newsletter.get("html_body", "")
    text_body = newsletter.get("text_body", "")

    if not html_body:
        log.error("Newsletter has no HTML body")
        return {"status": "skipped", "reason": "no html body"}

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
