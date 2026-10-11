import pytest

from heytap_eq import runtime


@pytest.mark.parametrize("expected", ["source", "pyinstaller", "nuitka"])
def test_runtime_identifies_compiled_and_frozen_modules(monkeypatch, expected):
    monkeypatch.delattr(runtime, "__compiled__", raising=False)
    monkeypatch.setattr(runtime.sys, "frozen", expected == "pyinstaller", raising=False)
    if expected == "nuitka":
        monkeypatch.setattr(runtime, "__compiled__", object(), raising=False)
    assert runtime.packager() == expected
    assert runtime.is_packaged() == (expected != "source")
