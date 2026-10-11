import colorsys
import json
import re

import pytest

from heytap_eq import theme

CURVE_KEYS = {"original", "current", "estimated", "target", "planned", "correction", "dac1", "dac2", "preview"}


@pytest.mark.parametrize("mode", ["light", "dark"])
@pytest.mark.parametrize("accent", list(theme.ACCENTS))
def test_every_theme_keeps_text_curves_and_accent_readable(mode, accent):
    resolved = theme.resolve({"mode": mode, "accent": accent, "contrast": "standard"})
    t = resolved["tokens"]
    assert resolved["mode"] == mode and resolved["contrast"] == "standard" and resolved["line_scale"] == 1
    # WCAG AA for body text and secondary text on the window background.
    assert theme.contrast(t["text"], t["bg"]) >= 7
    assert theme.contrast(t["muted"], t["surface"]) >= 4.5
    assert theme.contrast(t["accent-text"], t["accent"]) >= 4.5
    # Curves must stay distinguishable from the plot background (non-text contrast 3:1).
    for name, color in resolved["curves"].items():
        assert theme.contrast(color, t["plot-bottom"]) >= 3, name
        assert theme.contrast(color, t["plot-top"]) >= 3, name
    assert set(resolved["curves"]) == CURVE_KEYS
    assert t["accent"] in theme.stylesheet(resolved)


@pytest.mark.parametrize("mode", ["light", "dark"])
@pytest.mark.parametrize("accent", list(theme.ACCENTS))
def test_high_contrast_meets_aaa_and_thickens_edges(mode, accent):
    resolved = theme.resolve({"mode": mode, "accent": accent, "contrast": "high"})
    t = resolved["tokens"]
    assert resolved["contrast"] == "high" and resolved["line_scale"] == 1.5
    for key in ("text", "muted"):
        assert theme.contrast(t[key], t["bg"]) >= 7 and theme.contrast(t[key], t["surface-3"]) >= 7, key
    assert theme.contrast(t["accent-text"], t["accent"]) >= 7
    # Edges, focus and selection indicators: non-text contrast 3:1 against the window.
    for key in ("border", "border-strong", "accent", "grid"):
        assert theme.contrast(t[key], t["bg"]) >= 3, key
    # Disabled text stays legible but is visibly different from enabled text.
    assert theme.contrast(t["disabled"], t["bg"]) >= 4.5 and theme.contrast(t["disabled"], t["text"]) >= 2
    for name, color in resolved["curves"].items():
        assert theme.contrast(color, t["plot-bottom"]) >= 4.5, name
    assert set(resolved["curves"]) == CURVE_KEYS
    sheet = theme.stylesheet(resolved)
    assert "border-width: 2px" in sheet and f"border: 2px solid {t['accent']}" in sheet
    assert "border-width: 2px" not in theme.stylesheet(theme.resolve({"mode": mode, "contrast": "standard"}))
    web = theme.web(resolved)
    assert web["contrast"] == "high" and web["lineScale"] == 1.5


def test_no_palette_uses_green_or_teal():
    def greenish(color):
        r, g, b = (int(color[i:i+2], 16)/255 for i in (1, 3, 5))
        hue, _, saturation = colorsys.rgb_to_hls(r, g, b)
        return 75 <= hue*360 <= 185 and saturation > .2

    for palette in theme.BASE:
        mode, high = palette.removeprefix("hc-"), palette.startswith("hc-")
        for accent in theme.ACCENTS:
            resolved = theme.resolve({"mode": mode, "accent": accent, "contrast": "high" if high else "standard"})
            colors = {**resolved["tokens"], **resolved["curves"]}
            assert not [k for k, v in colors.items() if greenish(v[:7])], (palette, accent)


def test_target_and_raw_nodes_no_longer_share_a_colour():
    for palette in theme.BASE:
        resolved = theme.resolve({"mode": palette.removeprefix("hc-"), "accent": "amber",
                                  "contrast": "high" if palette.startswith("hc-") else "standard"})
        assert resolved["curves"]["target"] != resolved["tokens"]["raw-node"]
        target, node = (bytes.fromhex(c[1:]) for c in (resolved["curves"]["target"], resolved["tokens"]["raw-node"]))
        # Similar lightness is fine here; the hues must be clearly apart (RGB distance).
        assert sum((a-b)**2 for a, b in zip(target, node))**.5 > 90


def test_system_mode_and_contrast_follow_environment_and_bad_preferences_fall_back(tmp_path):
    assert theme.resolve({"mode": "system"}, system_dark=True)["mode"] == "dark"
    assert theme.resolve({"mode": "system"}, system_dark=False)["mode"] == "light"
    assert theme.resolve({"mode": "light"}, system_dark=True)["mode"] == "light"
    assert theme.resolve({}, system_high_contrast=True)["contrast"] == "high"
    assert theme.resolve({"contrast": "standard"}, system_high_contrast=True)["contrast"] == "standard"
    assert theme.resolve({"contrast": "high"}, system_high_contrast=False)["contrast"] == "high"
    assert theme.validate({"mode": "neon", "accent": 3, "contrast": "max"}) == theme.DEFAULT
    assert theme.validate(None) == theme.DEFAULT
    # The removed teal accent and files written before contrast existed still load.
    assert theme.validate({"mode": "dark", "accent": "teal"}) == {"mode": "dark", "accent": "blue", "contrast": "system"}
    path = tmp_path/"appearance.json"
    assert theme.load(path) == theme.DEFAULT
    path.write_text("{broken", encoding="utf-8")
    assert theme.load(path) == theme.DEFAULT
    path.write_text(json.dumps({"mode": "dark", "accent": "violet"}), encoding="utf-8")
    assert theme.load(path) == {"mode": "dark", "accent": "violet", "contrast": "system"}
    web = theme.web(theme.resolve(theme.load(path)))
    assert web["mode"] == "dark" and web["tokens"]["accent"] == theme.ACCENTS["violet"][1]["dark"]


def test_windows_contrast_probe_never_raises():
    enabled, dark = theme.windows_high_contrast()
    assert isinstance(enabled, bool) and (dark is None or isinstance(dark, bool))
    assert enabled or dark is None


def test_arrow_icons_follow_the_theme_and_are_referenced_by_the_style_sheet():
    dark, light = (theme.resolve({"mode": m}) for m in ("dark", "light"))
    assert theme.icon_svgs(dark)["down"] != theme.icon_svgs(light)["down"]
    assert dark["tokens"]["muted"] in theme.icon_svgs(dark)["up"]
    sheet = theme.stylesheet(dark, {"down": "C:/with space/down.svg", "up": "C:/with space/up.svg"})
    assert 'url("C:/with space/down.svg")' in sheet and "QAbstractSpinBox::up-arrow" in sheet
    assert "::down-arrow" not in theme.stylesheet(dark)


@pytest.mark.parametrize("contrast, alpha", [("standard", 38), ("high", 64)])
def test_translucent_tokens_use_rgba_in_qt_style_sheets(contrast, alpha):
    for mode in ("light", "dark"):
        resolved = theme.resolve({"mode": mode, "accent": "blue", "contrast": contrast})
        sheet = theme.stylesheet(resolved)
        # Qt would read #RRGGBBAA as #AARRGGBB and tint checked buttons with the wrong colour.
        assert not re.search(r"#[0-9a-fA-F]{8}", sheet)
        accent = resolved["tokens"]["accent"]
        rgb = ", ".join(str(int(accent[i:i+2], 16)) for i in (1, 3, 5))
        assert f"rgba({rgb}, {alpha})" in sheet
    assert theme.qss_color("#5b9dff26") == "rgba(91, 157, 255, 38)"
    assert theme.qss_color("#5b9dff") == "#5b9dff"
