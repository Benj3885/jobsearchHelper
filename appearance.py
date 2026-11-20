"""Theme (light / dark) and UI scale of the editor. No Tk widgets here.

The choice is kept in data/editor_settings.json (git-ignored) and read when this module is imported,
so every module that builds widgets sees the chosen colours in `COLORS`. Changing it from the
View menu saves the file and restarts the editor (`cv_editor.Editor._apply_view`).

UI scale: font sizes go through `pt()` and fixed pixel sizes (paddings, row heights, icons) through
`px()`. Fonts are given in pixels (negative Tk sizes) because Tk builds with Xft ignore `tk scaling`
for point sizes, so changing that alone leaves the text the same size.
"""

from __future__ import annotations

import json
from pathlib import Path

SETTINGS = Path(__file__).resolve().parent / "data" / "editor_settings.json"
SCALES = [0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0]
THEME_NAMES = {"light": "Light", "dark": "Dark"}

THEMES = {
    "light": {
        "bg": "#EEF1F5",          # window background
        "card": "#FFFFFF",        # panels
        "field": "#FFFFFF",       # text boxes, entries, tables
        "border": "#D5DCE5",
        "ink": "#1E2530",
        "muted": "#6B7686",
        "disabled": "#A9B2BE",    # disabled button text
        "accent": "#1F5FA8",
        "accent_hover": "#174C87",
        "accent_disabled": "#9DB3CF",
        "on_accent": "#FFFFFF",   # text on accent / green backgrounds
        "hover": "#F1F4F8",
        "scroll": "#C9D0DA",
        "scroll_active": "#AEB7C4",
        "preview_bg": "#E3E7ED",
        "page_shadow": "#C3CAD4",
        "error": "#C62828",
        "error_hover": "#FDF1F1",
        "good": "#2E7D32",        # ready document / posting text saved
        "quote_ink": "#3A4656",
        "quote_bg": "#F4F6F9",
        # quicklist icons: state -> fill, outline, text
        "icon_none": ("#F4F6F9", "#DDE2E9", "#BCC4CF"),
        "icon_saved": ("#E3E7ED", "#AEB7C4", "#4F5A6A"),
        "status": {
            "Contacted": "#1F5FA8",
            "Applied": "#6A3FA0",
            "Interview": "#B26A00",
            "Offer": "#2E7D32",
            "Rejected": "#8A94A3",
            "Not an option": "#8A94A3",
        },
    },
    "dark": {
        "bg": "#16191E",
        "card": "#1F232A",
        "field": "#262B33",
        "border": "#353B45",
        "ink": "#DDE2E9",
        "muted": "#8E98A7",
        "disabled": "#5C6573",
        "accent": "#4C8DDB",
        "accent_hover": "#6AA2E6",
        "accent_disabled": "#2F4A6B",
        "on_accent": "#FFFFFF",
        "hover": "#2A3038",
        "scroll": "#3E4550",
        "scroll_active": "#56606D",
        "preview_bg": "#121418",
        "page_shadow": "#07080A",
        "error": "#EF6B6B",
        "error_hover": "#3A2224",
        "good": "#3E9B45",
        "quote_ink": "#B6BFCB",
        "quote_bg": "#262B33",
        "icon_none": ("#22262D", "#323842", "#4F5763"),
        "icon_saved": ("#3A414C", "#56606D", "#C9D0DA"),
        "status": {
            "Contacted": "#6AA2E6",
            "Applied": "#B08BE0",
            "Interview": "#E0A040",
            "Offer": "#5DBB63",
            "Rejected": "#7A8494",
            "Not an option": "#7A8494",
        },
    },
}


def load() -> dict:
    try:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    theme = data.get("theme") if data.get("theme") in THEMES else "light"
    try:
        scale = min(max(float(data.get("scale", 1.0)), SCALES[0]), SCALES[-1])
    except (TypeError, ValueError):
        scale = 1.0
    return {"theme": theme, "scale": scale}


def save(theme: str, scale: float) -> None:
    SETTINGS.parent.mkdir(exist_ok=True)
    SETTINGS.write_text(json.dumps({"theme": theme, "scale": scale}, indent=2) + "\n", encoding="utf-8")


_settings = load()
THEME: str = _settings["theme"]
SCALE: float = _settings["scale"]
COLORS: dict = dict(THEMES[THEME])


PX_PER_PT = 96 / 72        # replaced by the display's value in cv_editor.setup_style


def pt(points: float) -> int:
    """A font size in points at 100 %, as a Tk pixel size (negative) scaled to the chosen UI size."""
    return -max(1, round(points * PX_PER_PT * SCALE))


def px(n: float) -> int:
    """A size in pixels at 100 %, scaled to the chosen UI size."""
    return round(n * SCALE)
