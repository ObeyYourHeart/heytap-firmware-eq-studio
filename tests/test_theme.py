import json

import pytest

from heytap_eq import theme


@pytest.mark.parametrize("mode", ["light", "dark"])
@pytest.mark.parametrize("accent", list(theme.ACCENTS))
def test_every_theme_keeps_text_curves_and_accent_readable(mode, accent):
    resolved = theme.resolve({"mode": mode, "accent": accent})
    t = resolved["tokens"]
    assert resolved["mode"] == mode
    # WCAG AA for body text and secondary text on the window background.
    assert theme.contrast(t["text"], t["bg"]) >= 7
    assert theme.contrast(t["muted"], t["surface"]) >= 4.5
    assert theme.contrast(t["accent-text"], t["accent"]) >= 4.5
    # Curves must stay distinguishable from the plot background (non-text contrast 3:1).
    for name, color in resolved["curves"].items():
        assert theme.contrast(color, t["plot-bottom"]) >= 3, name
        assert theme.contrast(color, t["plot-top"]) >= 3, name
    assert set(resolved["curves"]) == {"original", "current", "estimated", "target", "planned",
                                       "correction", "dac1", "dac2", "preview"}
    assert t["accent"] in theme.stylesheet(resolved)


def test_target_and_raw_nodes_no_longer_share_a_colour():
    for mode in ("light", "dark"):
        resolved = theme.resolve({"mode": mode, "accent": "amber"})
        assert resolved["curves"]["target"] != resolved["tokens"]["raw-node"]
        target, node = (bytes.fromhex(c[1:]) for c in (resolved["curves"]["target"], resolved["tokens"]["raw-node"]))
        # Similar lightness is fine here; the hues must be clearly apart (RGB distance).
        assert sum((a-b)**2 for a, b in zip(target, node))**.5 > 90


def test_system_mode_follows_environment_and_bad_preferences_fall_back(tmp_path):
    assert theme.resolve({"mode": "system"}, system_dark=True)["mode"] == "dark"
    assert theme.resolve({"mode": "system"}, system_dark=False)["mode"] == "light"
    assert theme.resolve({"mode": "light"}, system_dark=True)["mode"] == "light"
    assert theme.validate({"mode": "neon", "accent": 3}) == theme.DEFAULT
    assert theme.validate(None) == theme.DEFAULT
    path = tmp_path/"appearance.json"
    assert theme.load(path) == theme.DEFAULT
    path.write_text("{broken", encoding="utf-8")
    assert theme.load(path) == theme.DEFAULT
    path.write_text(json.dumps({"mode": "dark", "accent": "violet"}), encoding="utf-8")
    assert theme.load(path) == {"mode": "dark", "accent": "violet"}
    web = theme.web(theme.resolve(theme.load(path)))
    assert web["mode"] == "dark" and web["tokens"]["accent"] == theme.ACCENTS["violet"][1]["dark"]


def test_arrow_icons_follow_the_theme_and_are_referenced_by_the_style_sheet():
    dark, light = (theme.resolve({"mode": m}) for m in ("dark", "light"))
    assert theme.icon_svgs(dark)["down"] != theme.icon_svgs(light)["down"]
    assert dark["tokens"]["muted"] in theme.icon_svgs(dark)["up"]
    sheet = theme.stylesheet(dark, {"down": "C:/with space/down.svg", "up": "C:/with space/up.svg"})
    assert 'url("C:/with space/down.svg")' in sheet and "QAbstractSpinBox::up-arrow" in sheet
    assert "::down-arrow" not in theme.stylesheet(dark)


def test_translucent_tokens_use_rgba_in_qt_style_sheets():
    import re

    for mode in ("light", "dark"):
        resolved = theme.resolve({"mode": mode, "accent": "teal"})
        sheet = theme.stylesheet(resolved)
        # Qt would read #RRGGBBAA as #AARRGGBB and tint checked buttons olive.
        assert not re.search(r"#[0-9a-fA-F]{8}", sheet)
        accent = resolved["tokens"]["accent"]
        rgb = ", ".join(str(int(accent[i:i+2], 16)) for i in (1, 3, 5))
        assert f"rgba({rgb}, 38)" in sheet
    assert theme.qss_color("#3ccfb826") == "rgba(60, 207, 184, 38)"
    assert theme.qss_color("#3ccfb8") == "#3ccfb8"
