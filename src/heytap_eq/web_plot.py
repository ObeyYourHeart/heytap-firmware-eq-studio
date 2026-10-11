"""Bundled DSSSP editor inside Qt; DSP and edits remain in Python.

Wire format (schema 2): Python publishes one JSON payload per refresh (curves, editable
filters/RAW, legend toggles, notice, theme tokens, one-shot reset/fit view requests); the
page submits gesture events {op: begin|add|filter|raw|delete|view|command}. Python
validates every event before it touches the session.
"""

import json
from pathlib import Path

import numpy as np
from PySide6 import QtCore, QtGui, QtWebChannel, QtWebEngineCore, QtWebEngineWidgets, QtWidgets

SCHEMA = 2


class PlotBridge(QtCore.QObject):
    publish = QtCore.Signal(str)

    def __init__(self, plot):
        super().__init__(plot)
        self.plot = plot

    @QtCore.Slot()
    def ready(self):
        self.plot.is_ready = True
        self.plot.commit()

    @QtCore.Slot(str)
    def submit(self, value):
        try:
            data = json.loads(value)
            if not isinstance(data, dict):
                raise ValueError("Invalid editor event")
            self.plot.editRequested.emit(data)
        except (ValueError, TypeError, KeyError) as exc:
            self.plot.error.emit(str(exc))


class ResponsePlot(QtWebEngineWidgets.QWebEngineView):
    editRequested = QtCore.Signal(object)
    error = QtCore.Signal(str)

    def __init__(self, label):
        super().__init__()
        self.axis_label = label
        self.is_ready = False
        self.curves = []
        self.low, self.high = -24., 24.
        self.reset_requested = True
        self.fit_requested = False
        self.theme = None
        self.editor = {}
        self.bridge = PlotBridge(self)
        self.channel = QtWebChannel.QWebChannel(self.page())
        self.channel.registerObject("bridge", self.bridge)
        self.page().setWebChannel(self.channel)
        self.page().setBackgroundColor(QtGui.QColor("#101722"))
        self.settings().setAttribute(
            QtWebEngineCore.QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        self.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.NoContextMenu)
        self.setMinimumSize(300, 360)
        assets = Path(__file__).with_name("web")/"index.html"
        if not assets.exists():
            self.setHtml("<body style='background:#101722;color:white'>"
                         "图形资源未构建，请使用 Actions 安装包或先运行 frontend 的构建。 </body>")
        else:
            self.load(QtCore.QUrl.fromLocalFile(str(assets.resolve())))

    def reset_range(self, low=-24., high=24., step=6):
        self.low, self.high = float(low), float(high)
        self.reset_requested = True

    def request_fit(self):
        """Fit the dB axis to the curves of the next published payload."""
        self.fit_requested = True

    def set_theme(self, theme):
        self.theme = theme
        if theme:
            self.page().setBackgroundColor(QtGui.QColor(theme["tokens"]["bg"]))
        self.commit()

    def setLabel(self, axis, text, **_):
        if axis == "left":
            self.axis_label = text

    def clear_curves(self):
        self.curves = []

    def zero_line(self):
        # The SVG grid already draws the zero reference.
        pass

    def plot(self, frequency, values, color="#efb55a", name="", dashed=False, width=None):
        points = np.column_stack((frequency, values))
        if len(points) < 2:
            return
        if not np.all(np.isfinite(points)):
            raise ValueError("Non-finite plot points")
        curve = {"name": name, "points": points.tolist(), "color": color, "dashed": bool(dashed)}
        if width:
            curve["width"] = float(width)
        self.curves.append(curve)

    def listDataItems(self):
        return list(self.curves)

    def set_editor(self, **data):
        self.editor = data
        self.commit()

    def commit(self):
        if not self.is_ready:
            return
        payload = {"schema": SCHEMA, "curves": self.curves, "low": self.low, "high": self.high,
                   "reset": self.reset_requested, "fit": self.fit_requested,
                   "axisLabel": self.axis_label, "theme": self.theme, "legend": [],
                   "filters": [], "raw": [], **self.editor}
        self.bridge.publish.emit(json.dumps(payload, ensure_ascii=False, allow_nan=False))
        self.reset_requested = self.fit_requested = False

    def inspect(self, callback):
        # PySide/Qt versions differ in conversion of JS objects to QVariant.
        # Explicit JSON keeps source and frozen probes on the same wire format.
        self.page().runJavaScript(
            "JSON.stringify(window.studioInspect ? window.studioInspect() : null)",
            lambda value: callback(json.loads(value) if value else None))


def initialize_web_engine():
    """Call before constructing QApplication, including GUI tests."""
    if QtWidgets.QApplication.instance() is None:
        QtCore.QCoreApplication.setAttribute(QtCore.Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
