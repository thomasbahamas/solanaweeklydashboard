"""Generate a newsletter email draft from compiled dashboard data.

Produces a JSON file with subject, text_body, and html_body fields
ready for sending via any email provider.

Format:
    Subject: SOL $142 | F&G 34 | DEX vol up 21% — Mar 24
    [One-line editorial take]
    THE NUMBERS (key metrics)
    WHAT IT MEANS (2-3 sentences)
    ONE THING TO WATCH (single divergence/angle)
    -> Full dashboard link
    -- Thomas
"""

import html as html_mod
from datetime import datetime, timezone
from config import load_json, save_json, get_logger, now_utc

log = get_logger("newsletter")

DASHBOARD_URL = "https://solanaweekly.io/dashboard"
SITE_URL = "https://solanaweekly.io"

# Brand colors
PURPLE = "#9333ea"
PURPLE_LIGHT = "#a855f7"
DARK_BG = "#0f0b1a"
CARD_BG = "#1a1425"
BORDER_COLOR = "#2d2640"
TEXT_PRIMARY = "#f0edf5"
TEXT_MUTED = "#9ca3af"
GREEN = "#22c55e"
RED = "#ef4444"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def esc(text):
    """HTML-escape user-supplied text."""
    if text is None:
        return ""
    return html_mod.escape(str(text))


def fmt_usd(value, decimals=2, compact=True):
    """Format a number as compact USD."""
    if value is None or value == 0:
        return "$0"
    if compact:
        if abs(value) >= 1e12:
            return f"${value/1e12:.{decimals}f}T"
        if abs(value) >= 1e9:
            return f"${value/1e9:.{decimals}f}B"
        if abs(value) >= 1e6:
            return f"${value/1e6:.{decimals}f}M"
        if abs(value) >= 1e3:
            return f"${value/1e3:.{decimals}f}K"
    return f"${value:,.{decimals}f}"


def fmt_price(value):
    """Format a price — use more decimals for small values."""
    if value is None:
        return "$?"
    if value >= 1:
        return f"${value:,.0f}" if value >= 100 else f"${value:,.2f}"
    return f"${value:.4f}"


def fmt_change(value, suffix="%"):
    """Format a percentage change with + prefix."""
    if value is None:
        return "n/a"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}{suffix}"


def change_color(value):
    """Return green/red color string based on sign."""
    if value is None or value == 0:
        return TEXT_MUTED
    return GREEN if value > 0 else RED


def change_arrow(value):
    """Return up/down arrow based on sign."""
    if value is None or value == 0:
        return ""
    return " ^" if value > 0 else " v"


def today_short():
    """Return today's date formatted like 'Mar 24'."""
    return datetime.now(timezone.utc).strftime("%b %d").replace(" 0", " ")


# ---------------------------------------------------------------------------
# Subject line
# ---------------------------------------------------------------------------

def build_subject(compiled: dict, wow: dict) -> str:
    """Build a concise, data-forward subject line.

    Target: SOL $142 | F&G 34 | DEX vol up 21% — Mar 24
    """
    market = compiled.get("market", {})
    prices = market.get("prices", {})
    fg = market.get("fear_greed", {})

    sol_price = prices.get("SOL", {}).get("price")
    fg_value = fg.get("value")
    dex_delta = wow.get("dex_volume")

    parts = []

    # SOL price
    if sol_price is not None:
        parts.append(f"SOL {fmt_price(sol_price)}")

    # Fear & Greed
    if fg_value is not None:
        parts.append(f"F&G {fg_value}")

    # DEX volume WoW
    if dex_delta is not None:
        direction = "up" if dex_delta > 0 else "down"
        parts.append(f"DEX vol {direction} {abs(dex_delta):.0f}%")
    else:
        # Fallback — use SOL 24h change
        sol_change = prices.get("SOL", {}).get("change_24h")
        if sol_change is not None:
            direction = "up" if sol_change > 0 else "down"
            parts.append(f"SOL {direction} {abs(sol_change):.1f}% today")

    date_str = today_short()
    return " | ".join(parts) + f" -- {date_str}"


# ---------------------------------------------------------------------------
# Editorial take
# ---------------------------------------------------------------------------

def build_editorial_take(narrative: dict, compiled: dict) -> str:
    """Extract or build a one-line editorial take."""
    signal = narrative.get("the_signal", {})

    # Try to use the first story angle as the editorial take
    angles = signal.get("story_angles", [])
    if angles:
        return angles[0]

    # Fallback: first sentence of market context
    context = signal.get("market_context", "")
    if context:
        first_sentence = context.split(".")[0].strip()
        if first_sentence:
            return first_sentence + "."

    # Last resort: derive from data
    fg = compiled.get("market", {}).get("fear_greed", {})
    fg_label = fg.get("label", "Unknown")
    return f"Market sitting at {fg_label} — here's what the data says."


# ---------------------------------------------------------------------------
# Plain text body
# ---------------------------------------------------------------------------

def build_text_body(compiled: dict, narrative: dict, wow: dict) -> str:
    """Build the plain-text version of the newsletter."""
    market = compiled.get("market", {})
    solana = compiled.get("solana", {})
    prices = market.get("prices", {})
    fg = market.get("fear_greed", {})
    global_data = market.get("global", {})
    dex = solana.get("dex_volumes", {})
    sol_tvl = solana.get("solana_tvl", {})
    signal = narrative.get("the_signal", {})

    editorial = build_editorial_take(narrative, compiled)

    lines = []

    # Editorial take
    lines.append(editorial)
    lines.append("")

    # THE NUMBERS
    lines.append("THE NUMBERS")
    lines.append("-" * 30)

    sol = prices.get("SOL", {})
    btc = prices.get("BTC", {})
    eth = prices.get("ETH", {})

    if sol.get("price"):
        wow_sol = wow.get("sol_price")
        wow_str = f" (WoW {fmt_change(wow_sol)})" if wow_sol is not None else ""
        lines.append(f"  SOL: {fmt_price(sol['price'])} ({fmt_change(sol.get('change_24h'))} 24h){wow_str}")
    if btc.get("price"):
        lines.append(f"  BTC: {fmt_price(btc['price'])} ({fmt_change(btc.get('change_24h'))} 24h)")
    if eth.get("price"):
        lines.append(f"  ETH: {fmt_price(eth['price'])} ({fmt_change(eth.get('change_24h'))} 24h)")

    lines.append("")
    if fg.get("value") is not None:
        lines.append(f"  Fear & Greed: {fg['value']} -- {fg.get('label', '')} (was {fg.get('yesterday', '?')})")

    total_mcap = global_data.get("total_market_cap", 0)
    if total_mcap:
        wow_mcap = wow.get("total_market_cap")
        wow_str = f" | WoW {fmt_change(wow_mcap)}" if wow_mcap is not None else ""
        lines.append(f"  Total Market Cap: {fmt_usd(total_mcap)}{wow_str}")

    tvl_current = sol_tvl.get("current", 0)
    if tvl_current:
        wow_tvl = wow.get("sol_tvl")
        wow_str = f" | WoW {fmt_change(wow_tvl)}" if wow_tvl is not None else ""
        lines.append(f"  Solana TVL: {fmt_usd(tvl_current)}{wow_str}")

    combined_dex = dex.get("combined_24h", 0)
    if combined_dex:
        wow_dex = wow.get("dex_volume")
        wow_str = f" | WoW {fmt_change(wow_dex)}" if wow_dex is not None else ""
        lines.append(f"  DEX Volume (24h): {fmt_usd(combined_dex)}{wow_str}")

    # WHAT IT MEANS
    lines.append("")
    lines.append("WHAT IT MEANS")
    lines.append("-" * 30)
    context = signal.get("market_context", "")
    if context:
        # Take first 2-3 sentences
        sentences = [s.strip() for s in context.split(".") if s.strip()]
        summary = ". ".join(sentences[:3]) + "."
        lines.append(f"  {summary}")
    else:
        lines.append("  No narrative data available. Run generate_signal.py first.")

    # ONE THING TO WATCH
    lines.append("")
    lines.append("ONE THING TO WATCH")
    lines.append("-" * 30)
    divergences = signal.get("divergence_alerts", [])
    if divergences:
        top = divergences[0]
        lines.append(f"  {top.get('title', 'Divergence detected')}")
        lines.append(f"  {top.get('description', '')}")
    else:
        # Fallback to key_data_relationships
        relationships = signal.get("key_data_relationships", "")
        if relationships:
            lines.append(f"  {relationships}")
        else:
            lines.append("  Keep an eye on the Fear & Greed shift day-over-day.")

    # CTA + sign-off
    lines.append("")
    lines.append(f"-> Full dashboard: {DASHBOARD_URL}")
    lines.append("")
    lines.append("-- Thomas")
    lines.append("")
    lines.append(f"Solana Weekly | {SITE_URL}")
    lines.append("You're getting this because you signed up for the daily Solana briefing.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML body (email-compatible, inline styles, table layout)
# ---------------------------------------------------------------------------

def _metric_row_html(label: str, value: str, change: float = None, wow_val: float = None) -> str:
    """Build a single metric row for the HTML email."""
    change_html = ""
    if change is not None:
        color = change_color(change)
        change_html = (
            f'<span style="color:{color}; font-size:13px; margin-left:6px;">'
            f'{fmt_change(change)} 24h</span>'
        )

    wow_html = ""
    if wow_val is not None:
        color = change_color(wow_val)
        wow_html = (
            f'<span style="color:{color}; font-size:12px; margin-left:6px;">'
            f'WoW {fmt_change(wow_val)}</span>'
        )

    return f"""<tr>
  <td style="padding:6px 0; color:{TEXT_MUTED}; font-size:13px; border-bottom:1px solid {BORDER_COLOR};">{esc(label)}</td>
  <td style="padding:6px 0; color:{TEXT_PRIMARY}; font-size:15px; font-weight:600; text-align:right; border-bottom:1px solid {BORDER_COLOR};">
    {esc(value)}{change_html}{wow_html}
  </td>
</tr>"""


def build_html_body(compiled: dict, narrative: dict, wow: dict) -> str:
    """Build the HTML version of the newsletter (email-compatible)."""
    market = compiled.get("market", {})
    solana = compiled.get("solana", {})
    prices = market.get("prices", {})
    fg = market.get("fear_greed", {})
    global_data = market.get("global", {})
    dex = solana.get("dex_volumes", {})
    sol_tvl = solana.get("solana_tvl", {})
    signal = narrative.get("the_signal", {})

    editorial = build_editorial_take(narrative, compiled)

    # Build metric rows
    sol = prices.get("SOL", {})
    btc = prices.get("BTC", {})
    eth = prices.get("ETH", {})

    metric_rows = []
    if sol.get("price"):
        metric_rows.append(_metric_row_html(
            "SOL", fmt_price(sol["price"]),
            sol.get("change_24h"), wow.get("sol_price"),
        ))
    if btc.get("price"):
        metric_rows.append(_metric_row_html(
            "BTC", fmt_price(btc["price"]),
            btc.get("change_24h"),
        ))
    if eth.get("price"):
        metric_rows.append(_metric_row_html(
            "ETH", fmt_price(eth["price"]),
            eth.get("change_24h"),
        ))

    fg_label = f"{fg.get('value', '?')} -- {fg.get('label', '')}"
    metric_rows.append(_metric_row_html("Fear & Greed", fg_label))

    if global_data.get("total_market_cap"):
        metric_rows.append(_metric_row_html(
            "Total Mcap", fmt_usd(global_data["total_market_cap"]),
            wow_val=wow.get("total_market_cap"),
        ))
    if sol_tvl.get("current"):
        metric_rows.append(_metric_row_html(
            "Solana TVL", fmt_usd(sol_tvl["current"]),
            wow_val=wow.get("sol_tvl"),
        ))
    if dex.get("combined_24h"):
        metric_rows.append(_metric_row_html(
            "DEX Volume", fmt_usd(dex["combined_24h"]),
            wow_val=wow.get("dex_volume"),
        ))

    metrics_html = "\n".join(metric_rows)

    # WHAT IT MEANS
    context = signal.get("market_context", "")
    if context:
        sentences = [s.strip() for s in context.split(".") if s.strip()]
        what_it_means = ". ".join(sentences[:3]) + "."
    else:
        what_it_means = "No narrative data available. Run the signal generator first."

    # ONE THING TO WATCH
    divergences = signal.get("divergence_alerts", [])
    if divergences:
        top = divergences[0]
        watch_title = esc(top.get("title", "Divergence detected"))
        watch_desc = esc(top.get("description", ""))
        watch_html = (
            f'<span style="color:{PURPLE_LIGHT}; font-weight:600;">{watch_title}</span>'
            f'<br/><span style="color:{TEXT_MUTED};">{watch_desc}</span>'
        )
    else:
        relationships = signal.get("key_data_relationships", "")
        if relationships:
            watch_html = f'<span style="color:{TEXT_MUTED};">{esc(relationships)}</span>'
        else:
            watch_html = (
                f'<span style="color:{TEXT_MUTED};">'
                f'Keep an eye on the Fear &amp; Greed shift day-over-day.</span>'
            )

    date_str = today_short()

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Solana Weekly - {esc(date_str)}</title>
</head>
<body style="margin:0; padding:0; background-color:{DARK_BG}; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;">

<!-- Outer wrapper -->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:{DARK_BG};">
<tr><td align="center" style="padding:20px 10px;">

<!-- Inner container (max 560px for mobile friendliness) -->
<table role="presentation" width="560" cellpadding="0" cellspacing="0" border="0" style="max-width:560px; width:100%;">

  <!-- Header -->
  <tr><td style="padding:24px 24px 16px 24px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr>
      <td style="font-size:18px; font-weight:700; color:{PURPLE_LIGHT}; letter-spacing:0.5px;">SOLANA WEEKLY</td>
      <td style="text-align:right; font-size:13px; color:{TEXT_MUTED};">{esc(date_str)}</td>
    </tr>
    </table>
  </td></tr>

  <!-- Divider -->
  <tr><td style="padding:0 24px;">
    <div style="height:1px; background:linear-gradient(to right,{PURPLE},{BORDER_COLOR}); background-color:{PURPLE};"></div>
  </td></tr>

  <!-- Editorial take -->
  <tr><td style="padding:20px 24px 12px 24px;">
    <p style="margin:0; font-size:17px; line-height:1.5; color:{TEXT_PRIMARY}; font-style:italic;">
      {esc(editorial)}
    </p>
  </td></tr>

  <!-- THE NUMBERS -->
  <tr><td style="padding:16px 24px 4px 24px;">
    <h2 style="margin:0; font-size:12px; text-transform:uppercase; letter-spacing:2px; color:{PURPLE_LIGHT};">
      The Numbers
    </h2>
  </td></tr>

  <tr><td style="padding:4px 24px 16px 24px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:{CARD_BG}; border-radius:8px; padding:12px 16px;">
      {metrics_html}
    </table>
  </td></tr>

  <!-- WHAT IT MEANS -->
  <tr><td style="padding:16px 24px 4px 24px;">
    <h2 style="margin:0; font-size:12px; text-transform:uppercase; letter-spacing:2px; color:{PURPLE_LIGHT};">
      What It Means
    </h2>
  </td></tr>

  <tr><td style="padding:8px 24px 16px 24px;">
    <p style="margin:0; font-size:15px; line-height:1.6; color:{TEXT_MUTED};">
      {esc(what_it_means)}
    </p>
  </td></tr>

  <!-- ONE THING TO WATCH -->
  <tr><td style="padding:16px 24px 4px 24px;">
    <h2 style="margin:0; font-size:12px; text-transform:uppercase; letter-spacing:2px; color:{PURPLE_LIGHT};">
      One Thing to Watch
    </h2>
  </td></tr>

  <tr><td style="padding:8px 24px 16px 24px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr>
      <td style="width:3px; background-color:{PURPLE}; border-radius:2px;"></td>
      <td style="padding:10px 14px; font-size:14px; line-height:1.6;">
        {watch_html}
      </td>
    </tr>
    </table>
  </td></tr>

  <!-- CTA Button -->
  <tr><td align="center" style="padding:24px 24px 8px 24px;">
    <table role="presentation" cellpadding="0" cellspacing="0" border="0">
    <tr>
      <td align="center" style="background-color:{PURPLE}; border-radius:6px;">
        <a href="{DASHBOARD_URL}" target="_blank" style="display:inline-block; padding:12px 32px; font-size:15px; font-weight:600; color:#ffffff; text-decoration:none; letter-spacing:0.3px;">
          View Full Dashboard &rarr;
        </a>
      </td>
    </tr>
    </table>
  </td></tr>

  <!-- Sign-off -->
  <tr><td style="padding:20px 24px 8px 24px;">
    <p style="margin:0; font-size:15px; color:{TEXT_PRIMARY};">-- Thomas</p>
  </td></tr>

  <!-- Footer -->
  <tr><td style="padding:16px 24px 24px 24px;">
    <div style="height:1px; background-color:{BORDER_COLOR}; margin-bottom:16px;"></div>
    <p style="margin:0; font-size:12px; line-height:1.5; color:{TEXT_MUTED};">
      <a href="{SITE_URL}" style="color:{PURPLE_LIGHT}; text-decoration:none;">Solana Weekly</a> &mdash; Daily Solana ecosystem intelligence.<br/>
      You're getting this because you signed up for the daily briefing.<br/>
      <a href="{SITE_URL}/unsubscribe" style="color:{TEXT_MUTED}; text-decoration:underline;">Unsubscribe</a>
    </p>
  </td></tr>

</table>
<!-- /Inner container -->

</td></tr>
</table>
<!-- /Outer wrapper -->

</body>
</html>"""

    return html


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run() -> dict:
    """Generate newsletter email draft from compiled + narrative data.

    Returns a dict with subject, text_body, html_body, and metadata.
    Saves output to data/newsletter.json.
    """
    compiled = load_json("compiled.json")
    if not compiled:
        log.error("No compiled data found -- run compile_data.py first")
        return {}

    narrative = load_json("narrative.json")
    if not narrative:
        log.warning("No narrative data found -- newsletter will have limited editorial content")
        narrative = {}

    wow = compiled.get("wow", {})
    market = compiled.get("market", {})

    log.info("Building newsletter...")

    # Build all three components
    subject = build_subject(compiled, wow)
    text_body = build_text_body(compiled, narrative, wow)
    html_body = build_html_body(compiled, narrative, wow)

    log.info(f"  Subject: {subject}")
    log.info(f"  Text body: {len(text_body)} chars")
    log.info(f"  HTML body: {len(html_body)} chars")

    result = {
        "timestamp": now_utc(),
        "subject": subject,
        "text_body": text_body,
        "html_body": html_body,
        "metadata": {
            "sol_price": market.get("prices", {}).get("SOL", {}).get("price"),
            "fear_greed": market.get("fear_greed", {}).get("value"),
            "date": today_short(),
            "dashboard_url": DASHBOARD_URL,
        },
    }

    save_json(result, "newsletter.json")
    log.info("Newsletter draft saved to data/newsletter.json")

    return result


if __name__ == "__main__":
    run()
