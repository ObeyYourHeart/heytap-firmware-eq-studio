"""Appearance tokens shared by the Qt shell and the embedded graph; no Qt imports.

The same resolved theme produces the Qt style sheet and the CSS variables of the web
editor, so switching mode or accent restyles both halves of the window together.
"""

import json
import sys
from pathlib import Path

MODES = {"system": "跟随系统", "light": "浅色", "dark": "深色"}
CONTRASTS = {"system": "跟随系统", "standard": "标准", "high": "高对比度"}
# Each accent has a value for the four palettes: light / dark and their high-contrast variants.
ACCENTS = {
    "blue": ("蓝色", {"dark": "#5b9dff", "light": "#2563d4", "hc-dark": "#7cc0ff", "hc-light": "#0047ab"}),
    "violet": ("紫色", {"dark": "#a98bf5", "light": "#6d44c9", "hc-dark": "#d0b0ff", "hc-light": "#4b1fa3"}),
    "amber": ("琥珀", {"dark": "#e9b04f", "light": "#935c00", "hc-dark": "#ffd24d", "hc-light": "#6b4400"}),
}
DEFAULT = {"mode": "system", "accent": "blue", "contrast": "system"}

BASE = {
    "dark": {
        "bg": "#0f1520", "surface": "#151d29", "surface-2": "#1c2635", "surface-3": "#253145",
        "border": "#2a3647", "border-strong": "#3a4a60", "text": "#dbe4ef", "muted": "#8fa2b8",
        "disabled": "#8fa2b8", "warn": "#e8b04a", "danger": "#ff8f8f", "plot-top": "#131e2d",
        "plot-bottom": "#0c131d", "plot-edge": "#2a3647", "grid": "#253246", "tick": "#a5b5c8",
        "band": "#b29ad4", "raw-node": "#8fa2b8", "crosshair": "#a8b9cf", "shadow": "#00000099",
    },
    "light": {
        "bg": "#f3f5f8", "surface": "#ffffff", "surface-2": "#edf1f6", "surface-3": "#e1e7ef",
        "border": "#d3dbe5", "border-strong": "#b7c3d1", "text": "#1b2532", "muted": "#5d6d80",
        "disabled": "#5d6d80", "warn": "#9a6400", "danger": "#c0392b", "plot-top": "#ffffff",
        "plot-bottom": "#f2f5f9", "plot-edge": "#d3dbe5", "grid": "#e0e6ee", "tick": "#526276",
        "band": "#7b5fb0", "raw-node": "#77889b", "crosshair": "#5d6d80", "shadow": "#1b253233",
    },
    # High contrast: solid black/white surfaces, borders that read as edges (>= 3:1), secondary
    # text at AAA (>= 7:1), and a disabled colour that stays legible but is clearly not enabled.
    "hc-dark": {
        "bg": "#000000", "surface": "#000000", "surface-2": "#121212", "surface-3": "#262626",
        "border": "#a0a0a0", "border-strong": "#ffffff", "text": "#ffffff", "muted": "#e0e0e0",
        "disabled": "#9a9a9a", "warn": "#ffd400", "danger": "#ff7070", "plot-top": "#000000",
        "plot-bottom": "#000000", "plot-edge": "#ffffff", "grid": "#5e5e5e", "tick": "#ffffff",
        "band": "#d0b0ff", "raw-node": "#b3b3b3", "crosshair": "#ffffff", "shadow": "#000000cc",
    },
    "hc-light": {
        "bg": "#ffffff", "surface": "#ffffff", "surface-2": "#f0f0f0", "surface-3": "#dcdcdc",
        "border": "#595959", "border-strong": "#000000", "text": "#000000", "muted": "#262626",
        "disabled": "#6b6b6b", "warn": "#7a4100", "danger": "#a80000", "plot-top": "#ffffff",
        "plot-bottom": "#ffffff", "plot-edge": "#000000", "grid": "#8f8f8f", "tick": "#000000",
        "band": "#4b1fa3", "raw-node": "#595959", "crosshair": "#000000", "shadow": "#00000040",
    },
}
# Curve families keep one meaning everywhere: blue = measured, neutral bold = edited estimate,
# amber dotted = target, orange dashed = pending export, red dashed = generated preview,
# violet/pink = the two digital outputs. No green or teal hues.
CURVES = {
    "dark": {"original": "#6fa8dc", "current": "#a9c9ef", "estimated": "#f3f5f9", "target": "#f2c14e",
             "planned": "#ff8a5c", "correction": "#c8a27a", "dac1": "#b48cf2", "dac2": "#f07ab0",
             "preview": "#ff5d73"},
    "light": {"original": "#2f6fb0", "current": "#5b8ccc", "estimated": "#141b26", "target": "#a86f00",
              "planned": "#d9541e", "correction": "#8a6a4a", "dac1": "#7b4fd6", "dac2": "#c4407f",
              "preview": "#e0244a"},
    "hc-dark": {"original": "#5cb2ff", "current": "#a6d4ff", "estimated": "#ffffff", "target": "#ffd400",
                "planned": "#ffa040", "correction": "#f0c090", "dac1": "#d4a6ff", "dac2": "#ff7ac8",
                "preview": "#ff6680"},
    "hc-light": {"original": "#0050b3", "current": "#2f6fd6", "estimated": "#000000", "target": "#7a4f00",
                 "planned": "#b33c00", "correction": "#5c4033", "dac1": "#5b21b6", "dac2": "#a3125f",
                 "preview": "#d00030"},
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
    choices = {"mode": MODES, "accent": ACCENTS, "contrast": CONTRASTS}
    return {key: preferences.get(key) if preferences.get(key) in options else DEFAULT[key]
            for key, options in choices.items()}


def load(path):
    try:
        return validate(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return dict(DEFAULT)


def resolve(preferences, system_dark=True, system_high_contrast=False):
    preferences = validate(preferences)
    mode = preferences["mode"]
    if mode == "system":
        mode = "dark" if system_dark else "light"
    level = preferences["contrast"]
    high = system_high_contrast if level == "system" else level == "high"
    palette = ("hc-" if high else "")+mode
    tokens = dict(BASE[palette])
    accent = ACCENTS[preferences["accent"]][1][palette]
    tokens["accent"] = accent
    # Text on accent fills picks whichever of near-black / white reads better.
    tokens["accent-text"] = max(("#000000" if high else "#0b1220", "#ffffff"), key=lambda c: contrast(c, accent))
    tokens["accent-soft"] = accent+("40" if high else "26")
    tokens["warn-soft"] = tokens["warn"]+"1f"
    return {"mode": mode, "requested": preferences["mode"], "accent": preferences["accent"],
            "contrast": "high" if high else "standard", "requested_contrast": level,
            "line_scale": 1.5 if high else 1, "tokens": tokens, "curves": dict(CURVES[palette])}


def windows_high_contrast():
    """(enabled, dark) for the Windows contrast-theme setting; (False, None) where unavailable.

    Qt 6.8 does not report it. Dark/light follows the theme's window colour, so "Aquatic" and
    "Night sky" resolve dark and "Desert" light.
    """
    if sys.platform != "win32":
        return False, None
    try:
        import ctypes
        from ctypes import wintypes

        class HighContrast(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwFlags", wintypes.DWORD),
                        ("lpszDefaultScheme", wintypes.LPWSTR)]

        info = HighContrast(ctypes.sizeof(HighContrast), 0, None)
        user32 = ctypes.windll.user32
        # SPI_GETHIGHCONTRAST; HCF_HIGHCONTRASTON = 1.
        if not user32.SystemParametersInfoW(0x0042, ctypes.sizeof(info), ctypes.byref(info), 0) or not info.dwFlags & 1:
            return False, None
        window = user32.GetSysColor(5)  # COLOR_WINDOW as 0x00BBGGRR
        color = f"#{window & 0xff:02x}{window >> 8 & 0xff:02x}{window >> 16 & 0xff:02x}"
        return True, _luminance(color) < .5
    except (OSError, AttributeError, ValueError):
        return False, None


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


def qss_color(value):
    """CSS reads #RRGGBBAA but Qt style sheets read 8-digit hex as #AARRGGBB; use rgba() for Qt."""
    if len(value) == 9:
        r, g, b, a = (int(value[i:i+2], 16) for i in (1, 3, 5, 7))
        return f"rgba({r}, {g}, {b}, {a})"
    return value


def stylesheet(theme, icons=None):
    t = {key: qss_color(value) for key, value in theme["tokens"].items()}
    return _arrows(icons)+_base(t)+(_high_contrast(t) if theme.get("contrast") == "high" else "")


def _high_contrast(t):
    """Structure, not just colour: 2 px edges and focus rings, solid selections, outlined tools."""
    return f"""
QPushButton, QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTableWidget, QListWidget,
QFrame#card {{ border-width: 2px; }}
QToolBar QToolButton {{ border: 2px solid {t['border']}; }}
QToolBar QToolButton:disabled {{ border-color: transparent; }}
QPushButton:focus, QToolButton:focus, QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QPlainTextEdit:focus, QTableWidget:focus, QListWidget:focus {{ border: 2px solid {t['accent']}; }}
QMenuBar::item:selected, QMenu::item:selected {{ background: {t['accent']}; color: {t['accent-text']}; }}
QComboBox QAbstractItemView, QTableWidget, QListWidget {{ selection-background-color: {t['accent']};
    selection-color: {t['accent-text']}; }}
QMenu, QToolTip {{ border: 2px solid {t['border-strong']}; }}
QScrollBar::handle {{ background: {t['border']}; }}
QSplitter::handle:horizontal {{ width: 2px; }}
QSplitter::handle {{ background: {t['border-strong']}; }}
QHeaderView::section {{ color: {t['text']}; border-bottom: 2px solid {t['border']}; }}
"""


def _base(t):
    return f"""
QMainWindow, QDialog {{ background: {t['bg']}; }}
QWidget {{ color: {t['text']}; font-size: 12px; }}
QWidget#rail, QWidget#railBody {{ background: {t['bg']}; }}
QToolTip {{ background: {t['surface']}; color: {t['text']}; border: 1px solid {t['border-strong']}; padding: 4px; }}
QMenuBar {{ background: {t['bg']}; border-bottom: 1px solid {t['border']}; }}
QMenuBar::item {{ padding: 5px 10px; background: transparent; }}
QMenuBar::item:selected, QMenu::item:selected {{ background: {t['accent-soft']}; color: {t['text']}; }}
QMenu {{ background: {t['surface']}; border: 1px solid {t['border-strong']}; padding: 4px; }}
QMenu::item {{ padding: 5px 24px 5px 22px; border-radius: 4px; }}
QMenu::item:disabled {{ color: {t['disabled']}; }}
QMenu::separator {{ height: 1px; background: {t['border']}; margin: 4px 8px; }}
QToolBar {{ background: {t['bg']}; border: 0; border-bottom: 1px solid {t['border']}; spacing: 4px; padding: 4px 6px; }}
QToolBar::separator {{ width: 1px; background: {t['border']}; margin: 4px 6px; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 4px 9px; }}
QToolButton:hover {{ background: {t['surface-2']}; border-color: {t['border']}; }}
QToolButton:pressed, QToolButton:checked {{ background: {t['accent-soft']}; border-color: {t['accent']}; }}
QToolButton:disabled {{ color: {t['disabled']}; background: transparent; border-color: transparent; }}
QToolButton::menu-button {{ border: 0; width: 14px; }}
QPushButton {{ background: {t['surface-2']}; border: 1px solid {t['border']}; border-radius: 6px; padding: 5px 12px; }}
QPushButton:hover {{ border-color: {t['accent']}; }}
QPushButton:pressed {{ background: {t['surface-3']}; }}
QPushButton:checked {{ background: {t['accent-soft']}; border-color: {t['accent']}; color: {t['text']}; }}
QPushButton:disabled {{ color: {t['disabled']}; background: {t['bg']}; border-color: {t['border']}; }}
QPushButton[primary="true"] {{ background: {t['accent']}; border-color: {t['accent']}; color: {t['accent-text']}; font-weight: 600; }}
QPushButton[primary="true"]:disabled {{ background: {t['surface-2']}; border-color: {t['border']}; color: {t['disabled']}; font-weight: 400; }}
QPushButton[flat="true"] {{ background: transparent; border-color: transparent; color: {t['muted']}; padding: 3px 6px; }}
QPushButton[flat="true"]:hover {{ color: {t['text']}; }}
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{ background: {t['surface']}; border: 1px solid {t['border']};
    border-radius: 5px; padding: 4px 6px; selection-background-color: {t['accent']}; selection-color: {t['accent-text']}; }}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {t['border-strong']}; }}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {t['accent']}; }}
QComboBox:disabled, QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {t['disabled']}; background: {t['bg']}; }}
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
QWidget#railStrip {{ background: {t['bg']}; }}
QToolButton[role="stepButton"] {{ background: {t['surface-3']}; color: {t['muted']}; font-weight: 600; padding: 0;
    border-radius: 13px; min-width: 26px; max-width: 26px; min-height: 26px; max-height: 26px; }}
QToolButton[role="stepButton"][state="ok"] {{ background: {t['accent']}; color: {t['accent-text']}; }}
QToolButton[role="stepButton"]:hover {{ border: 1px solid {t['accent']}; }}
QLabel[role="muted"], QLabel[role="section"] {{ color: {t['muted']}; }}
QLabel[role="section"] {{ font-size: 11px; margin-top: 4px; }}
QLabel[state="warn"] {{ color: {t['warn']}; }}
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
    return {"mode": theme["mode"], "accent": theme["accent"], "contrast": theme["contrast"],
            "lineScale": theme["line_scale"], "tokens": theme["tokens"]}
