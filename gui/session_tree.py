"""左侧栏：追踪源标题、已存会话树、显示设置、自动保存、清空显示。"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import theme


def device_icon(color: str = theme.TEXT_DIM) -> QIcon:
    """会话节点图标：一个带内容线的设备框。"""
    pixmap = QPixmap(15, 15)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(1.4)
    painter.setPen(pen)
    painter.drawRoundedRect(1, 3, 13, 9, 2, 2)
    painter.drawLine(4, 7, 11, 7)
    painter.drawLine(4, 10, 8, 10)
    painter.end()
    return QIcon(pixmap)


class SessionNode(QFrame):
    """一个已存会话节点。"""

    activated = Signal(str)  # 双击打开会话
    contextRequested = Signal(str, QPoint)

    def __init__(self, session: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("sessionNode")
        self.setProperty("active", "false")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("双击打开会话")
        self.session_id = str(session.get("id", ""))

        badge_text = self._badge_text_for(session)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 5, 8, 5)
        layout.setSpacing(8)

        icon = QLabel()
        icon.setPixmap(device_icon().pixmap(15, 15))
        icon.setFixedSize(15, 15)

        self.name_label = QLabel(str(session.get("name", "会话")))
        self.name_label.setObjectName("nodeName")

        self.badge = QLabel(badge_text)
        self.badge.setObjectName("nodeBadge")
        self.badge.setProperty("state", "idle")

        layout.addWidget(icon)
        layout.addWidget(self.name_label, 1)
        layout.addWidget(self.badge)

    @staticmethod
    def _badge_text_for(session: dict) -> str:
        if session.get("type", "serial") == "serial":
            return str(session.get("serial_config", {}).get("port") or "未设置")
        return "J-Link"

    # ------------------------------------------------------------------
    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.activated.emit(self.session_id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        self.contextRequested.emit(self.session_id, event.globalPos())
        event.accept()

    def set_name(self, name: str) -> None:
        self.name_label.setText(name)

    def set_badge(self, text: str, state: str = "idle") -> None:
        self.badge.setText(text)
        self.badge.setProperty("state", state)
        self._repolish(self.badge)

    def set_active(self, active: bool) -> None:
        self.setProperty("active", "true" if active else "false")
        self._repolish(self)

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)


class Sidebar(QWidget):
    """会话侧栏：会话树 + 三个设置面板。"""

    openRequested = Signal(str)
    editRequested = Signal(str)
    deleteRequested = Signal(str)
    displayChanged = Signal(dict)
    autosaveChanged = Signal(bool)
    logPathRequested = Signal()
    clearRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(248)

        self._nodes: dict[str, SessionNode] = {}
        self._sessions: list[dict] = []
        self._loading = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QWidget()
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(12, 10, 12, 8)
        self.head_label = QLabel("追踪源")
        self.head_label.setObjectName("sideHead")
        self.head_dot = QLabel("\u25cf")
        self.head_dot.setStyleSheet(f"color:{theme.ACCENT}; font-size:10px;")
        head_layout.addWidget(self.head_label, 1)
        head_layout.addWidget(self.head_dot)
        outer.addWidget(head)

        scroll = QScrollArea()
        scroll.setObjectName("sessionScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        outer.addWidget(scroll, 1)

        content = QWidget()
        content.setAutoFillBackground(False)
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(6, 0, 6, 12)
        layout.setSpacing(0)

        self.group_button = QToolButton()
        self.group_button.setObjectName("groupTitle")
        self.group_button.setCursor(Qt.PointingHandCursor)
        self.group_button.setText("\u25be 已存会话")
        self.group_button.clicked.connect(self._toggle_group)
        layout.addWidget(self.group_button)

        self.nodes_holder = QWidget()
        self.nodes_layout = QVBoxLayout(self.nodes_holder)
        self.nodes_layout.setContentsMargins(0, 0, 0, 6)
        self.nodes_layout.setSpacing(2)
        layout.addWidget(self.nodes_holder)

        layout.addWidget(self._build_display_panel())
        layout.addWidget(self._build_autosave_panel())
        layout.addWidget(self._build_clear_panel())
        layout.addStretch(1)

    # ------------------------------------------------------------------
    # 面板
    # ------------------------------------------------------------------
    @staticmethod
    def _panel(title: str, body: QWidget, top_margin: int = 0) -> QWidget:
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(8, top_margin, 8, 0)
        holder_layout.setSpacing(6)
        label = QLabel(title)
        label.setObjectName("panelTitle")
        holder_layout.addWidget(label)
        holder_layout.addWidget(body)
        return holder

    def _build_display_panel(self) -> QWidget:
        body = QFrame()
        body.setObjectName("panelBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(10, 10, 10, 10)
        body_layout.setSpacing(10)

        self.hex_check = QCheckBox("Hex 显示")
        self.timestamp_check = QCheckBox("显示时间戳")
        self.timestamp_check.setChecked(True)

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        self.max_lines_spin = QSpinBox()
        self.max_lines_spin.setRange(100, 1000000)
        self.max_lines_spin.setSingleStep(1000)
        self.max_lines_spin.setValue(20000)
        self.max_lines_spin.setFixedWidth(116)
        row_layout.addWidget(QLabel("最大行数:"))
        row_layout.addStretch(1)
        row_layout.addWidget(self.max_lines_spin)

        body_layout.addWidget(self.hex_check)
        body_layout.addWidget(self.timestamp_check)
        body_layout.addWidget(row)

        self.hex_check.toggled.connect(self._emit_display)
        self.timestamp_check.toggled.connect(self._emit_display)
        self.max_lines_spin.valueChanged.connect(self._emit_display)

        return self._panel("显示设置", body, top_margin=34)

    def _build_autosave_panel(self) -> QWidget:
        body = QFrame()
        body.setObjectName("panelBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(10, 10, 10, 10)
        body_layout.setSpacing(10)

        self.autosave_check = QCheckBox("启用自动保存")
        self.autosave_check.setChecked(True)
        self.path_button = QPushButton("设置保存路径")
        self.path_button.setObjectName("panelButton")
        self.path_button.setCursor(Qt.PointingHandCursor)
        self.path_label = QLabel("")
        self.path_label.setObjectName("hintLabel")

        body_layout.addWidget(self.autosave_check)
        body_layout.addWidget(self.path_button)
        body_layout.addWidget(self.path_label)

        self.autosave_check.toggled.connect(self._on_autosave_toggled)
        self.path_button.clicked.connect(self.logPathRequested.emit)

        return self._panel("自动保存", body, top_margin=16)

    def _build_clear_panel(self) -> QWidget:
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(8, 16, 8, 0)
        self.clear_button = QPushButton("清空显示")
        self.clear_button.setObjectName("panelButton")
        self.clear_button.setCursor(Qt.PointingHandCursor)
        self.clear_button.setFixedHeight(32)
        self.clear_button.clicked.connect(self.clearRequested.emit)
        holder_layout.addWidget(self.clear_button)
        return holder

    # ------------------------------------------------------------------
    # 会话节点
    # ------------------------------------------------------------------
    def set_sessions(self, sessions: list[dict]) -> None:
        self._sessions = list(sessions)
        while self.nodes_layout.count():
            item = self.nodes_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._nodes.clear()

        for session in self._sessions:
            node = SessionNode(session, self.nodes_holder)
            node.activated.connect(self.openRequested.emit)
            node.contextRequested.connect(self._on_context)
            self.nodes_layout.addWidget(node)
            self._nodes[str(session.get("id", ""))] = node
        self.nodes_layout.addStretch(1)

    def update_session(self, session: dict) -> None:
        node = self._nodes.get(str(session.get("id", "")))
        if node is None:
            return
        node.set_name(str(session.get("name", "会话")))
        node.set_badge(SessionNode._badge_text_for(session), str(node.badge.property("state")))

    def set_status(self, session_id: str, state: str) -> None:
        """state: idle / live / err，用于节点徽章着色。"""
        node = self._nodes.get(session_id)
        if node is not None:
            node.set_badge(node.badge.text(), state)

    def set_active(self, session_id: str | None) -> None:
        for key, node in self._nodes.items():
            node.set_active(key == session_id)

    def _on_context(self, session_id: str, position: QPoint) -> None:
        menu = QMenu(self)
        edit_action = menu.addAction("编辑会话")
        delete_action = menu.addAction("删除会话")
        chosen = menu.exec(position)
        if chosen is edit_action:
            self.editRequested.emit(session_id)
        elif chosen is delete_action:
            self.deleteRequested.emit(session_id)

    def _toggle_group(self) -> None:
        visible = self.nodes_holder.isVisible()
        self.nodes_holder.setVisible(not visible)
        self.group_button.setText("\u25b8 已存会话" if visible else "\u25be 已存会话")

    # ------------------------------------------------------------------
    # 显示设置
    # ------------------------------------------------------------------
    def _emit_display(self, *_args) -> None:
        if self._loading:
            return
        self.displayChanged.emit(self.display_settings())

    def _on_autosave_toggled(self, checked: bool) -> None:
        if not self._loading:
            self.autosaveChanged.emit(bool(checked))

    def display_settings(self) -> dict:
        return {
            "hex": self.hex_check.isChecked(),
            "timestamp": self.timestamp_check.isChecked(),
            "max_lines": int(self.max_lines_spin.value()),
        }

    def set_display(self, display: dict | None) -> None:
        """载入当前会话的显示设置；None 表示没有活动会话。"""
        self._loading = True
        try:
            enabled = display is not None
            for widget in (
                self.hex_check,
                self.timestamp_check,
                self.max_lines_spin,
                self.autosave_check,
                self.path_button,
                self.clear_button,
            ):
                widget.setEnabled(enabled)
            if not enabled:
                return
            self.hex_check.setChecked(bool(display.get("hex", False)))
            self.timestamp_check.setChecked(bool(display.get("timestamp", True)))
            self.max_lines_spin.setValue(int(display.get("max_lines", 20000) or 20000))
            self.autosave_check.setChecked(bool(display.get("autosave", True)))
        finally:
            self._loading = False

    def set_log_path(self, path: str) -> None:
        self.path_label.setText(path or "未设置")
        self.path_label.setToolTip(path)
