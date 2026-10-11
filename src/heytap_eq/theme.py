"""Appearance tokens shared by the Qt shell and the embedded graph; no Qt imports.

The same resolved theme produces the Qt style sheet and the CSS variables of the web
editor, so switching mode or accent restyles both halves of the window together.
"""

import json
from pathlib import Path

MODES = {"system": "跟随系统", "light": "浅色", "dark": "深色"}
ACCENTS = {
    "teal": ("青绿", {"dark": "#3ccfb8", "light": "#08776a"}),
    "blue": ("蓝色", {"dark": "#5b9dff", "light": "#2563d4"}),
    "violet": ("紫色", {"dark": "#a98bf5", "light": "#6d44c9"}),
    "amber": ("琥珀", {"dark": "#e9b04f", "light": "#935c00"}),
}
DEFAULT = {"mode": "system", "accent": "teal"}

BASE = {
    "dark": {
        "bg": "#0f1520", "surface": "#151d29", "surface-2": "#1c2635", "surface-3": "#253145",
        "border": "#2a3647", "border-strong": "#3a4a60", "text": "#dbe4ef", "muted": "#8fa2b8",
        "warn": "#e8b04a", "danger": "#ff8f8f", "ok": "#5fd38d", "plot-top": "#131e2d",
        "plot-bottom": "#0c131d", "plot-edge": "#2a3647", "grid": "#253246", "tick": "#a5b5c8",
        "band": "#b29ad4", "raw-node": "#8fa2b8", "crosshair": "#a8b9cf", "shadow": "#00000099",
    },
    "light": {
        "bg": "#f3f5f8", "surface": "#ffffff", "surface-2": "#edf1f6", "surface-3": "#e1e7ef",
        "border": "#d3dbe5", "border-strong": "#b7c3d1", "text": "#1b2532", "muted": "#5d6d80",
        "warn": "#9a6400", "danger": "#c0392b", "ok": "#1d8a4c", "plot-top": "#ffffff",
        "plot-bottom": "#f2f5f9", "plot-edge": "#d3dbe5", "grid": "#e0e6ee", "tick": "#526276",
        "band": "#7b5fb0", "raw-node": "#77889b", "crosshair": "#5d6d80", "shadow": "#1b253233",
    },
}
# Curve families keep one meaning in both modes: blue = measured, teal = edited estimate,
# amber dashed = target, coral dashed = pending export, violet/pink = the two digital outputs.
CURVES = {
    "dark": {"original": "#6fa8dc", "current": "#a9c9ef", "estimated": "#3ccfb8", "target": "#f2c14e",
             "planned": "#ff8a5c", "correction": "#e6dcc4", "dac1": "#b48cf2", "dac2": "#f07ab0"},
    "light": {"original": "#2f6fb0", "current": "#4f80bd", "estimated": "#0f9d8a", "target": "#b07800",
              "planned": "#d9541e", "correction": "#6b5e45", "dac1": "#7b4fd6", "dac2": "#c4407f"},
}


def _luminance(color):
    rgb = [int(color[i:i+2], 16)/255 for i in (1, 3, 5)]
    rgb = [c/12.92 if c <= .03928 else ((c+.055)/1.055)**2.4 for c in rgb]
    return .2126*rgb[0]+.7152*rgb[1]+.0722*rgb[2]


def contrast(a, b):
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high+.05)/(low+.05)


def validate(preferences):
    """Unknown or damaged preferences fall back to defaults instead of failing startup."""
    if not isinstance(preferences, dict):
        return dict(DEFAULT)
    mode = preferences.get("mode")
    accent = preferences.get("accent")
    return {"mode": mode if mode in MODES else DEFAULT["mode"],
            "accent": accent if accent in ACCENTS else DEFAULT["accent"]}


def load(path):
    try:
        return validate(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return dict(DEFAULT)


def resolve(preferences, system_dark=True):
    preferences = validate(preferences)
    mode = preferences["mode"]
    if mode == "system":
        mode = "dark" if system_dark else "light"
    tokens = dict(BASE[mode])
    accent = ACCENTS[preferences["accent"]][1][mode]
    tokens["accent"] = accent
    # Text on accent fills picks whichever of near-black / white reads better.
    tokens["accent-text"] = max(("#06201c", "#ffffff"), key=lambda c: contrast(c, accent))
    tokens["accent-soft"] = accent+"26"
    tokens["warn-soft"] = tokens["warn"]+"1f"
    return {"mode": mode, "requested": preferences["mode"], "accent": preferences["accent"],
            "tokens": tokens, "curves": dict(CURVES[mode])}


ARROWS = {"down": "M1.5 1.5l4 4 4-4", "up": "M1.5 5.5l4-4 4 4"}


def icon_svgs(theme):
    """Arrow glyphs in the muted text colour; the shell writes them next to its settings."""
    color = theme["tokens"]["muted"]
    return {name: f'<svg xmlns="http://www.w3.org/2000/svg" width="11" height="7" viewBox="0 0 11 7">'
                  f'<path d="{path}" fill="none" stroke="{color}" stroke-width="1.6" stroke-linecap="round" '
                  f'stroke-linejoin="round"/></svg>' for name, path in ARROWS.items()}


def _arrows(icons):
    if not icons:
        return ""
    return f"""
QComboBox {{ padding-right: 24px; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 22px; border: 0; }}
QComboBox::down-arrow {{ image: url("{icons['down']}"); width: 11px; height: 7px; }}
QAbstractSpinBox {{ padding-right: 20px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ subcontrol-origin: border; width: 18px; border: 0;
    background: transparent; }}
QAbstractSpinBox::up-button {{ subcontrol-position: top right; }}
QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; }}
QAbstractSpinBox::up-arrow {{ image: url("{icons['up']}"); width: 9px; height: 6px; }}
QAbstractSpinBox::down-arrow {{ image: url("{icons['down']}"); width: 9px; height: 6px; }}
QToolButton::menu-indicator {{ image: url("{icons['down']}"); width: 9px; height: 6px;
    subcontrol-position: right center; subcontrol-origin: padding; }}
"""


def stylesheet(theme, icons=None):
    t = theme["tokens"]
    return _arrows(icons)+f"""
QMainWindow, QDialog {{ background: {t['bg']}; }}
QWidget {{ color: {t['text']}; font-size: 12px; }}
QWidget#rail, QWidget#railBody {{ background: {t['bg']}; }}
QToolTip {{ background: {t['surface']}; color: {t['text']}; border: 1px solid {t['border-strong']}; padding: 4px; }}
QMenuBar {{ background: {t['bg']}; border-bottom: 1px solid {t['border']}; }}
QMenuBar::item {{ padding: 5px 10px; background: transparent; }}
QMenuBar::item:selected, QMenu::item:selected {{ background: {t['accent-soft']}; color: {t['text']}; }}
QMenu {{ background: {t['surface']}; border: 1px solid {t['border-strong']}; padding: 4px; }}
QMenu::item {{ padding: 5px 24px 5px 22px; border-radius: 4px; }}
QMenu::item:disabled {{ color: {t['muted']}; }}
QMenu::separator {{ height: 1px; background: {t['border']}; margin: 4px 8px; }}
QToolBar {{ background: {t['bg']}; border: 0; border-bottom: 1px solid {t['border']}; spacing: 4px; padding: 4px 6px; }}
QToolBar::separator {{ width: 1px; background: {t['border']}; margin: 4px 6px; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 4px 9px; }}
QToolButton:hover {{ background: {t['surface-2']}; border-color: {t['border']}; }}
QToolButton:pressed, QToolButton:checked {{ background: {t['accent-soft']}; border-color: {t['accent']}; }}
QToolButton:disabled {{ color: {t['muted']}; background: transparent; border-color: transparent; }}
QToolButton::menu-button {{ border: 0; width: 14px; }}
QPushButton {{ background: {t['surface-2']}; border: 1px solid {t['border']}; border-radius: 6px; padding: 5px 12px; }}
QPushButton:hover {{ border-color: {t['accent']}; }}
QPushButton:pressed {{ background: {t['surface-3']}; }}
QPushButton:checked {{ background: {t['accent-soft']}; border-color: {t['accent']}; color: {t['text']}; }}
QPushButton:disabled {{ color: {t['muted']}; background: {t['bg']}; border-color: {t['border']}; }}
QPushButton[primary="true"] {{ background: {t['accent']}; border-color: {t['accent']}; color: {t['accent-text']}; font-weight: 600; }}
QPushButton[primary="true"]:disabled {{ background: {t['surface-2']}; border-color: {t['border']}; color: {t['muted']}; font-weight: 400; }}
QPushButton[flat="true"] {{ background: transparent; border-color: transparent; color: {t['muted']}; padding: 3px 6px; }}
QPushButton[flat="true"]:hover {{ color: {t['text']}; }}
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{ background: {t['surface']}; border: 1px solid {t['border']};
    border-radius: 5px; padding: 4px 6px; selection-background-color: {t['accent']}; selection-color: {t['accent-text']}; }}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {t['border-strong']}; }}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {t['accent']}; }}
QComboBox:disabled, QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {t['muted']}; background: {t['bg']}; }}
QComboBox QAbstractItemView {{ background: {t['surface']}; border: 1px solid {t['border-strong']};
    selection-background-color: {t['accent-soft']}; selection-color: {t['text']}; outline: 0; }}
QTableWidget, QListWidget {{ background: {t['surface']}; alternate-background-color: {t['surface-2']};
    gridline-color: {t['border']}; border: 1px solid {t['border']}; border-radius: 5px;
    selection-background-color: {t['accent-soft']}; selection-color: {t['text']}; }}
QHeaderView::section {{ background: {t['surface-2']}; color: {t['muted']}; padding: 4px 6px; border: 0;
    border-bottom: 1px solid {t['border']}; }}
QTableCornerButton::section {{ background: {t['surface-2']}; border: 0; }}
QFrame#card {{ background: {t['surface']}; border: 1px solid {t['border']}; border-radius: 8px; }}
QFrame#card[current="true"] {{ border-color: {t['accent']}; }}
QLabel[role="step"] {{ background: {t['surface-3']}; color: {t['muted']}; border-radius: 9px; font-weight: 600;
    min-width: 18px; max-width: 18px; min-height: 18px; max-height: 18px; qproperty-alignment: AlignCenter; }}
QLabel[role="step"][state="ok"] {{ background: {t['accent']}; color: {t['accent-text']}; }}
QLabel[role="cardTitle"] {{ font-weight: 600; font-size: 13px; }}
QLabel[role="muted"], QLabel[role="section"] {{ color: {t['muted']}; }}
QLabel[role="section"] {{ font-size: 11px; margin-top: 4px; }}
QLabel[state="warn"] {{ color: {t['warn']}; }}
QLabel[state="ok"] {{ color: {t['ok']}; }}
QLabel[role="chip"] {{ background: {t['surface-2']}; border: 1px solid {t['border']}; border-radius: 10px; padding: 2px 10px; }}
QLabel[role="chip"][state="ok"] {{ color: {t['text']}; border-color: {t['accent']}; }}
QLabel[role="chip"][state="warn"] {{ color: {t['warn']}; border-color: {t['warn']}; }}
QScrollArea {{ border: 0; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle {{ background: {t['border-strong']}; border-radius: 4px; min-height: 24px; min-width: 24px; }}
QScrollBar::handle:hover {{ background: {t['muted']}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: 0; width: 0; height: 0; }}
QSplitter::handle {{ background: {t['border']}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QDockWidget {{ titlebar-close-icon: none; }}
QDockWidget::title {{ background: {t['surface-2']}; padding: 5px 8px; border-bottom: 1px solid {t['border']}; }}
QStatusBar {{ background: {t['bg']}; color: {t['muted']}; border-top: 1px solid {t['border']}; }}
QStatusBar::item {{ border: 0; }}
QProgressBar {{ background: {t['surface-2']}; border: 0; border-radius: 3px; max-height: 6px; }}
QProgressBar::chunk {{ background: {t['accent']}; border-radius: 3px; }}
QCheckBox {{ spacing: 6px; }}
"""


def web(theme):
    return {"mode": theme["mode"], "accent": theme["accent"], "tokens": theme["tokens"]}
