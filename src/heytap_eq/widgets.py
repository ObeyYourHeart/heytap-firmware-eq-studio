"""Qt building blocks for the workflow rail; colours come from theme.stylesheet()."""

from PySide6 import QtCore, QtGui, QtWidgets


def repolish(widget):
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


def mark(widget, role=None, state=None):
    """Set the QSS role/state properties; restyle only when they actually change."""
    before = (widget.property("role"), widget.property("state"))
    if role is not None:
        widget.setProperty("role", role)
    widget.setProperty("state", state or "")
    if (widget.property("role"), widget.property("state")) != before:
        repolish(widget)
    return widget


def label(text="", role=None, wrap=True, selectable=False):
    result = QtWidgets.QLabel(text)
    result.setWordWrap(wrap)
    if selectable:
        result.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
    if role:
        result.setProperty("role", role)
    return result


def button(text, slot=None, primary=False, checkable=False, tooltip=None, flat=False):
    result = QtWidgets.QPushButton(text)
    if primary:
        result.setProperty("primary", True)
    if flat:
        result.setProperty("flat", True)
    result.setCheckable(checkable)
    if slot:
        result.clicked.connect(slot)
    if tooltip:
        result.setToolTip(tooltip)
    return result


def row(*widgets, spacing=6):
    body = QtWidgets.QWidget()
    layout = QtWidgets.QHBoxLayout(body)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    for widget in widgets:
        if widget is None:
            layout.addStretch()
        else:
            layout.addWidget(widget, 1)
    return body


class StepCard(QtWidgets.QFrame):
    """One step of the workflow: number, title, one-line summary and collapsible body."""

    def __init__(self, number, title, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.done = False
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 10)
        outer.setSpacing(6)
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(8)
        self.step = label(str(number), "step", wrap=False)
        self.title = label(title, "cardTitle", wrap=False)
        self.toggle = QtWidgets.QToolButton()
        self.toggle.setAutoRaise(True)
        self.toggle.setArrowType(QtCore.Qt.ArrowType.DownArrow)
        self.toggle.setToolTip("收起／展开")
        self.toggle.clicked.connect(lambda: self.set_expanded(not self.body.isVisible()))
        header.addWidget(self.step)
        header.addWidget(self.title)
        header.addStretch()
        header.addWidget(self.toggle)
        outer.addLayout(header)
        self.summary = label("", "muted")
        outer.addWidget(self.summary)
        self.body = QtWidgets.QWidget()
        self.layout_ = QtWidgets.QVBoxLayout(self.body)
        self.layout_.setContentsMargins(0, 2, 0, 0)
        self.layout_.setSpacing(6)
        outer.addWidget(self.body)

    def add(self, widget):
        self.layout_.addWidget(widget)
        return widget

    def set_summary(self, text, done=False, warn=False):
        self.done = done
        self.summary.setText(text)
        self.summary.setVisible(bool(text))
        mark(self.summary, "muted", "warn" if warn else None)
        mark(self.step, "step", "ok" if done else None)

    def set_expanded(self, expanded):
        self.body.setVisible(expanded)
        self.toggle.setArrowType(QtCore.Qt.ArrowType.DownArrow if expanded else QtCore.Qt.ArrowType.RightArrow)


def write_icons(theme, folder):
    """Materialise the theme's arrow glyphs; style sheets can only load images from files."""
    from heytap_eq.theme import icon_svgs

    folder.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, svg in icon_svgs(theme).items():
        path = folder/f"{name}-{theme['tokens']['muted'][1:]}.svg"
        if not path.exists() or path.read_text(encoding="utf-8") != svg:
            path.write_text(svg, encoding="utf-8")
        paths[name] = path.resolve().as_posix()
    return paths


def apply_palette(theme):
    """Palette for native-drawn parts (check marks, spin arrows, native-style dialogs)."""
    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    t = theme["tokens"]
    color = QtGui.QColor
    palette = QtGui.QPalette()
    Role, Group = QtGui.QPalette.ColorRole, QtGui.QPalette.ColorGroup
    for role, value in ((Role.Window, t["bg"]), (Role.WindowText, t["text"]), (Role.Base, t["surface"]),
                        (Role.AlternateBase, t["surface-2"]), (Role.Text, t["text"]),
                        (Role.Button, t["surface-2"]), (Role.ButtonText, t["text"]),
                        (Role.Highlight, t["accent"]), (Role.HighlightedText, t["accent-text"]),
                        (Role.ToolTipBase, t["surface"]), (Role.ToolTipText, t["text"]),
                        (Role.PlaceholderText, t["muted"]), (Role.Link, t["accent"]),
                        (Role.Mid, t["border"]), (Role.Dark, t["border-strong"]), (Role.Light, t["surface-3"])):
        palette.setColor(role, color(value))
    for role in (Role.WindowText, Role.Text, Role.ButtonText):
        palette.setColor(Group.Disabled, role, color(t["muted"]))
    app.setPalette(palette)
