"""Small presentation helpers for Telegram messages."""

from __future__ import annotations


PROGRESS_WIDTH = 10


def format_number(value: float | int) -> str:
    """Format a nutrition number compactly with Indonesian separators."""
    rounded = round(value)
    if abs(value - rounded) < 0.05:
        return f"{rounded:,}".replace(",", ".")
    return f"{value:.1f}".replace(".", ",")


def format_progress(
    value: float | None,
    reference: float,
    *,
    kind: str = "target",
    width: int = PROGRESS_WIDTH,
) -> str | None:
    """Build a progress bar without inventing values for missing data."""
    if value is None:
        return None
    if reference <= 0 or value < 0 or width <= 0:
        raise ValueError("value, reference, and width must form a valid progress bar")

    ratio = value / reference
    percentage = round(ratio * 100)
    filled = min(width, max(0, round(ratio * width)))
    bar = "█" * filled + "░" * (width - filled)

    indicator = ""
    if kind == "limit":
        if ratio >= 1:
            indicator = " ⛔"
        elif ratio >= 0.8:
            indicator = " ⚠️"
    elif kind == "target":
        if ratio >= 1:
            indicator = " ✓"
    else:
        raise ValueError("kind must be 'target' or 'limit'")
    return f"{bar} {percentage}%{indicator}"
