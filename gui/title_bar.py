"""自定义标题栏：品牌区 + 菜单按钮 + 窗口控制。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from . import theme


class TitleBar(QWidget):
    """38px 标题栏，兼作窗口拖动区域。"""

    newJLinkRequested = Signal()
    newSerialRequested = Signal()
    minimizeRequested = Signal()
    maximizeRequested = Signal()
    closeRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("titleBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(38)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(14)

        self.logo = QLabel("T")
        self.logo.setObjectName("brandLogo")
        self.logo.setFixedSize(22, 22)
        self.logo.setAlignment(Qt.AlignCenter)

        self.brand = QLabel("TraceHub")
        self.brand.setObjectName("brandText")

        layout.addWidget(self.logo)
        layout.addWidget(self.brand)
        layout.addSpacing(6)

        self.jlink_button = QPushButton("新建J-Link 会话")
        self.jlink_button.setObjectName("menuAccent")
        self.jlink_button.setCursor(Qt.PointingHandCursor)
        self.jlink_button.clicked.connect(self.newJLinkRequested.emit)

        self.serial_button = QPushButton("新建串口会话")
        self.serial_button.setObjectName("menuAccent")
        self.serial_button.setCursor(Qt.PointingHandCursor)
        self.serial_button.clicked.connect(self.newSerialRequested.emit)

        layout.addWidget(self.jlink_button)
        layout.addSpacing(8)
        layout.addWidget(self.serial_button)
        layout.addStretch(1)

        self.minimize_button = self._control("\u2014", "最小化")
        self.maximize_button = self._control("\u25a2", "最大化")
        self.close_button = self._control("\u2715", "关闭", close=True)

        self.minimize_button.clicked.connect(self.minimizeRequested.emit)
        self.maximize_button.clicked.connect(self.maximizeRequested.emit)
        self.close_button.clicked.connect(self.closeRequested.emit)

        # 窗口控制按钮单独一组，间距按设计稿 4px
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(4)
        controls.addWidget(self.minimize_button)
        controls.addWidget(self.maximize_button)
        controls.addWidget(self.close_button)
        layout.addLayout(controls)

    # ------------------------------------------------------------------
    def _control(self, glyph: str, tip: str, close: bool = False) -> QPushButton:
        button = QPushButton(glyph, self)
        button.setObjectName("winControl")
        button.setProperty("close", "true" if close else "false")
        button.setToolTip(tip)
        button.setFixedSize(30, 24)
        button.setCursor(Qt.PointingHandCursor)
        button.setFocusPolicy(Qt.NoFocus)
        return button

    def set_maximized(self, maximized: bool) -> None:
        self.maximize_button.setText("\u2750" if maximized else "\u25a2")
        self.maximize_button.setToolTip("还原" if maximized else "最大化")

    # ------------------------------------------------------------------
    # 拖动与双击最大化
    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.maximizeRequested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
