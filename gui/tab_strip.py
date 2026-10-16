"""主视图顶部的窄标签栏（24px）。"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from . import theme


class TabItem(QFrame):
    """单个标签：源点 + 名称 + 关闭。"""

    activated = Signal(str)
    closeRequested = Signal(str)
    contextRequested = Signal(str, QPoint)

    def __init__(self, session_id: str, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("tabItem")
        self.setProperty("active", "false")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(24)
        self.session_id = session_id

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 6, 0)
        layout.setSpacing(8)

        self.dot = QFrame()
        self.dot.setFixedSize(7, 7)
        self.dot.setStyleSheet(
            f"background:{theme.INFO}; border-radius:3px; border:0;"
        )

        self.title_label = QLabel(title)
        self.title_label.setObjectName("tabTitle")

        self.close_button = QPushButton("\u2715")
        self.close_button.setObjectName("tabClose")
        self.close_button.setFixedSize(16, 16)
        self.close_button.setCursor(Qt.PointingHandCursor)
        self.close_button.setFocusPolicy(Qt.NoFocus)
        self.close_button.setToolTip("关闭视图")
        self.close_button.clicked.connect(lambda: self.closeRequested.emit(self.session_id))

        layout.addWidget(self.dot)
        layout.addWidget(self.title_label)
        layout.addWidget(self.close_button)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.activated.emit(self.session_id)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        self.contextRequested.emit(self.session_id, event.globalPos())
        event.accept()

    def set_active(self, active: bool) -> None:
        self.setProperty("active", "true" if active else "false")
        self._repolish()

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)

    def set_dot(self, color: str) -> None:
        self.dot.setStyleSheet(f"background:{color}; border-radius:3px; border:0;")

    def _repolish(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)


class TabStrip(QWidget):
    """标签集合；关掉最后一个标签时主视图回到空状态页。"""

    activated = Signal(str)
    closed = Signal(str)
    contextRequested = Signal(str, QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("tabStrip")
        self.setFixedHeight(24)
        self._items: dict[str, TabItem] = {}

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._layout.addStretch(1)

    # ------------------------------------------------------------------
    def add_tab(self, session_id: str, title: str) -> TabItem:
        item = self._items.get(session_id)
        if item is not None:
            item.set_title(title)
            return item
        item = TabItem(session_id, title, self)
        item.activated.connect(self.activated.emit)
        item.closeRequested.connect(self._on_close_requested)
        item.contextRequested.connect(self.contextRequested.emit)
        self._layout.insertWidget(self._layout.count() - 1, item)
        self._items[session_id] = item
        return item

    def remove_tab(self, session_id: str) -> None:
        item = self._items.pop(session_id, None)
        if item is not None:
            self._layout.removeWidget(item)
            item.deleteLater()

    def set_active(self, session_id: str | None) -> None:
        for key, item in self._items.items():
            item.set_active(key == session_id)

    def set_title(self, session_id: str, title: str) -> None:
        item = self._items.get(session_id)
        if item is not None:
            item.set_title(title)

    def set_dot(self, session_id: str, color: str) -> None:
        item = self._items.get(session_id)
        if item is not None:
            item.set_dot(color)

    def count(self) -> int:
        return len(self._items)

    def ids(self) -> list[str]:
        return list(self._items)

    def _on_close_requested(self, session_id: str) -> None:
        self.closed.emit(session_id)
