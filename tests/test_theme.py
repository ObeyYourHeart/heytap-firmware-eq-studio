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
                                       "correction", "dac1", "dac2"}
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
