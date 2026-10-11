"""Recognize both supported desktop packagers without changing Python globals."""

import sys


def packager():
    # Nuitka marks compiled modules; it deliberately does not set sys.frozen.
    if "__compiled__" in globals():
        return "nuitka"
    if getattr(sys, "frozen", False):
        return "pyinstaller"
    return "source"


def is_packaged():
    return packager() != "source"
