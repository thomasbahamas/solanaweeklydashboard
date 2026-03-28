"""Shared formatting and rendering utilities for Solana Weekly."""

import html as html_mod
from datetime import datetime, timezone


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


def fmt_change(value):
    """Format a percentage change with arrow and color."""
    if value is None:
        return ""
    arrow = "&#9650;" if value > 0 else "&#9660;" if value < 0 else "&#8211;"
    color = "var(--green)" if value > 0 else "var(--red)" if value < 0 else "var(--muted)"
    return f'<span style="color:{color}">{arrow} {value:+.1f}%</span>'


def fmt_wow(wow: dict, key: str) -> str:
    """Format a week-over-week delta."""
    if not wow or key not in wow:
        return '<span class="wow">WoW: collecting</span>'
    val = wow[key]
    color = "var(--green)" if val > 0 else "var(--red)" if val < 0 else "var(--muted)"
    arrow = "&#9650;" if val > 0 else "&#9660;" if val < 0 else "&#8211;"
    return f'<span class="wow" style="color:{color}">{arrow} {val:+.1f}% WoW</span>'


def fmt_price(value):
    """Format a price with appropriate decimal places."""
    if value is None or value == 0:
        return "$0"
    if value < 0.01:
        return f"${value:,.6f}"
    if value < 1:
        return f"${value:,.4f}"
    return f"${value:,.2f}"


def source_link(url, name):
    """Clickable source attribution link."""
    return f'<a href="{url}" target="_blank" rel="noopener" class="source-link">{name} &#8599;</a>'


def sentiment_dot(sentiment: str) -> str:
    """Colored dot for sentiment indicator."""
    s = (sentiment or "").lower()
    if "extremely" in s and "bullish" in s:
        return '<span class="dot dot-green-bright"></span>'
    if "bullish" in s:
        return '<span class="dot dot-green"></span>'
    if "bearish" in s:
        return '<span class="dot dot-red"></span>'
    return '<span class="dot dot-gray"></span>'


def freshness_badge(timestamp_str: str) -> str:
    """Return an HTML badge showing how old the data is."""
    if not timestamp_str:
        return ""
    try:
        dt = datetime.strptime(timestamp_str[:16], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - dt
        hours = age.total_seconds() / 3600
        if hours < 0:
            hours = 0
        if hours < 1:
            text = f"{max(1, int(age.total_seconds() / 60))}m ago"
        elif hours < 24:
            text = f"{int(hours)}h ago"
        else:
            text = f"{int(hours / 24)}d ago"
        color = "var(--green)" if hours < 4 else "#f97316" if hours < 12 else "var(--red)"
        return f'<span class="freshness" style="color:{color}" title="Data fetched: {esc(timestamp_str)}">{text}</span>'
    except Exception:
        return ""
