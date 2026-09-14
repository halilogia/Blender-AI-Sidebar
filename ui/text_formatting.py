"""Small, Blender-independent helpers for presenting assistant text."""

from html import unescape


def clean_assistant_text(text: str) -> str:
    """Decode HTML entities and normalize line endings for UI display.

    Provider responses are plain text, but some compatible endpoints return
    entities such as ``&#x20;``.  Decode only at the presentation boundary so
    the canonical conversation/history data remains unchanged.
    """
    if not text:
        return ""
    normalized = unescape(str(text)).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in normalized.split("\n"))
