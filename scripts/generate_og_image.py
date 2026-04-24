"""Generate a 1200x630 Open Graph share card PNG for the daily dashboard.

Output: output/og.png

Card layout (brand-aligned with the dashboard):
    - Dark purple gradient background
    - "SOLANA WEEKLY" wordmark top-left, date top-right
    - Big TLDR line in center (Claude-generated newsletter_tldr)
    - Bottom row: SOL price / F&G / Solana TVL / DEX 24h
    - Small brand footer

Pillow only — no external fonts required; falls back to default if
a system font isn't found so CI never blocks on font availability.
"""

from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
from PIL import Image, ImageDraw, ImageFont
from config import load_json, get_logger, OUTPUT_DIR

log = get_logger("og_image")

WIDTH, HEIGHT = 1200, 630
BG_TOP = (15, 11, 26)        # #0f0b1a
BG_BOT = (26, 20, 37)        # #1a1425
ACCENT = (147, 51, 234)      # #9333ea
ACCENT_LIGHT = (168, 85, 247)
CYAN = (6, 182, 212)
TEXT = (240, 237, 245)
MUTED = (161, 161, 170)
GREEN = (34, 197, 94)
RED = (239, 68, 68)


def _try_font(candidates: list, size: int):
    """Try each font path until one loads. Fall back to PIL default."""
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


# Ordered list — GitHub Actions ubuntu-latest ships DejaVu; macOS uses system;
# we fall back to PIL default if nothing matches.
FONT_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]
FONT_REG = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


def _vertical_gradient(size: tuple, top: tuple, bottom: tuple) -> Image.Image:
    """Smooth top->bottom RGB gradient."""
    w, h = size
    img = Image.new("RGB", size, top)
    px = img.load()
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        for x in range(w):
            px[x, y] = (r, g, b)
    return img


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int, max_lines: int) -> list:
    """Greedy word wrap honoring a pixel width budget."""
    if not text:
        return []
    words = text.split()
    lines = []
    cur = ""
    for w in words:
        probe = f"{cur} {w}".strip()
        bbox = draw.textbbox((0, 0), probe, font=font)
        if bbox[2] - bbox[0] <= max_w:
            cur = probe
        else:
            if cur:
                lines.append(cur)
            cur = w
        if len(lines) == max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    # Truncate with ellipsis if we hit the line limit mid-text
    joined = " ".join(lines)
    if len(joined) < len(text):
        # Chop trailing word from last line, append …
        last = lines[-1]
        if len(last) > 3:
            lines[-1] = last[:-3].rstrip() + "…"
    return lines


def _fmt_usd(v) -> str:
    if not v:
        return "$?"
    if v >= 1e12:
        return f"${v/1e12:.2f}T"
    if v >= 1e9:
        return f"${v/1e9:.2f}B"
    if v >= 1e6:
        return f"${v/1e6:.1f}M"
    return f"${v:,.0f}"


def _fmt_price(v) -> str:
    if v is None:
        return "$?"
    if v >= 100:
        return f"${v:,.0f}"
    if v >= 1:
        return f"${v:,.2f}"
    return f"${v:.4f}"


def build_tldr(narrative: dict, compiled: dict) -> str:
    """Reuse Claude's TLDR, with a data-driven fallback."""
    tldr = (narrative.get("newsletter_tldr") or "").strip()
    if tldr:
        return tldr
    signal = narrative.get("the_signal", {}) or {}
    context = (signal.get("market_context") or "").strip()
    if context:
        first = context.split(".")[0].strip()
        if len(first) > 10:
            return first + "."
    # Hard fallback
    fg = (compiled.get("market", {}) or {}).get("fear_greed", {}) or {}
    label = fg.get("label", "Neutral")
    return f"Solana markets are sitting at {label.lower()} — the data below tells the story."


def run() -> Path | None:
    compiled = load_json("compiled.json")
    if not compiled:
        log.warning("No compiled.json — skipping OG image")
        return None

    narrative = load_json("narrative.json") or {}

    market = compiled.get("market", {})
    solana = compiled.get("solana", {})
    prices = market.get("prices", {})
    fg = market.get("fear_greed", {})
    sol_tvl = solana.get("solana_tvl", {})
    dex = solana.get("dex_volumes", {})

    sol_price = prices.get("SOL", {}).get("price")
    sol_chg = prices.get("SOL", {}).get("change_24h")
    fg_val = fg.get("value")
    tvl_current = sol_tvl.get("current")
    dex_combined = dex.get("combined_24h")

    tldr = build_tldr(narrative, compiled)
    date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")

    # --- Compose the image ---
    img = _vertical_gradient((WIDTH, HEIGHT), BG_TOP, BG_BOT)
    draw = ImageDraw.Draw(img)

    # Accent bar top
    draw.rectangle([(0, 0), (WIDTH, 6)], fill=ACCENT)

    # Header
    wordmark_font = _try_font(FONT_BOLD, 36)
    date_font = _try_font(FONT_REG, 24)
    draw.text((60, 48), "SOLANA WEEKLY", font=wordmark_font, fill=ACCENT_LIGHT)
    date_bbox = draw.textbbox((0, 0), date_str, font=date_font)
    date_w = date_bbox[2] - date_bbox[0]
    draw.text((WIDTH - 60 - date_w, 58), date_str, font=date_font, fill=MUTED)

    # Divider
    draw.line([(60, 108), (WIDTH - 60, 108)], fill=(45, 38, 64), width=2)

    # TLDR — large, wrapped, up to 3 lines
    tldr_font = _try_font(FONT_BOLD, 50)
    tldr_lines = _wrap(draw, tldr, tldr_font, max_w=WIDTH - 120, max_lines=3)
    y = 170
    for line in tldr_lines:
        draw.text((60, y), line, font=tldr_font, fill=TEXT)
        y += 66

    # Metric row — bottom band
    band_top = HEIGHT - 170
    draw.rectangle([(0, band_top), (WIDTH, band_top + 2)], fill=(45, 38, 64))

    # Four metric cells
    label_font = _try_font(FONT_REG, 18)
    value_font = _try_font(FONT_BOLD, 34)

    metrics = []
    if sol_price is not None:
        chg_str = f"  {sol_chg:+.1f}%" if sol_chg is not None else ""
        chg_color = GREEN if (sol_chg or 0) >= 0 else RED
        metrics.append(("SOL", _fmt_price(sol_price), chg_str, chg_color))
    if fg_val is not None:
        label = fg.get("label", "")
        metrics.append(("FEAR & GREED", str(fg_val), f"  {label}", MUTED))
    if tvl_current:
        metrics.append(("SOLANA TVL", _fmt_usd(tvl_current), "", None))
    if dex_combined:
        metrics.append(("DEX 24H", _fmt_usd(dex_combined), "", None))

    # Lay out up to 4 cells evenly
    cells = metrics[:4]
    if cells:
        cell_w = (WIDTH - 120) / len(cells)
        for i, (label, value, suffix, suffix_color) in enumerate(cells):
            cx = 60 + int(i * cell_w)
            draw.text((cx, band_top + 30), label, font=label_font, fill=MUTED)
            draw.text((cx, band_top + 58), value, font=value_font, fill=TEXT)
            if suffix:
                val_bbox = draw.textbbox((0, 0), value, font=value_font)
                sx = cx + (val_bbox[2] - val_bbox[0])
                draw.text((sx, band_top + 68), suffix, font=label_font, fill=suffix_color or MUTED)

    # Footer
    footer_font = _try_font(FONT_REG, 18)
    draw.text((60, HEIGHT - 36), "solanaweekly.io", font=footer_font, fill=ACCENT_LIGHT)

    # Save
    out_path = OUTPUT_DIR / "og.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    log.info(f"OG image written → {out_path} ({out_path.stat().st_size // 1024} KB)")
    return out_path


if __name__ == "__main__":
    run()
