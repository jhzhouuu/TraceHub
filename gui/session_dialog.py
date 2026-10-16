"""会话弹窗：新建 / 编辑 J-Link 与串口会话，以及删除确认。

弹窗按设计稿做成无边框卡片：自定义标题栏、遮罩点击、Esc、✕、取消 都能关闭。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.config import BAUDRATES, DEVICE_PRESETS, JLINK_INTERFACES
from core.jlink_worker import list_emulators
from core.serial_worker import available_ports

from . import theme

SPEED_PRESETS = [4000, 2000, 1000, 500]

# 扫描期间保持引用：线程没跑完就被回收会直接崩进程
_ACTIVE_SCANS: set = set()


class ScanWorker(QThread):
    """后台执行设备扫描，避免 J-Link DLL 阻塞界面线程。"""

    finishedWith = Signal(object)

    def __init__(self, func, parent=None):
        super().__init__(parent)
        self._func = func

    def run(self) -> None:  # noqa: D401
        try:
            result = self._func()
        except Exception as exc:  # noqa: BLE001 - 缺驱动等情况要回传给界面
            result = exc
        self.finishedWith.emit(result)


class ModalBackdrop(QWidget):
    """盖住主窗口的半透明遮罩，点击空白处关闭弹窗。"""

    clicked = Signal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, False)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(4, 6, 9, 158))
        painter.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.clicked.emit()
        event.accept()


def run_modal(dialog: QDialog, host: QWidget) -> int:
    """显示遮罩并把弹窗居中，返回 QDialog 的结果。"""
    backdrop = ModalBackdrop(host)
    backdrop.setGeometry(host.rect())
    backdrop.clicked.connect(dialog.reject)
    backdrop.show()
    backdrop.raise_()

    dialog.adjustSize()
    center = host.mapToGlobal(host.rect().center())
    dialog.move(
        center.x() - dialog.width() // 2,
        center.y() - dialog.height() // 2,
    )
    try:
        return dialog.exec()
    finally:
        backdrop.hide()
        backdrop.setParent(None)
        backdrop.deleteLater()


class SessionDialog(QDialog):
    """无边框弹窗外壳：标题 + 内容 + 底部按钮。"""

    def __init__(self, title: str, ok_text: str, width: int = 400, parent=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setModal(True)
        self.setFixedWidth(width)
        self._scan: ScanWorker | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QWidget()
        head.setObjectName("dialogHead")
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(14, 11, 10, 11)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("dialogTitle")
        self.close_button = QPushButton("\u2715")
        self.close_button.setObjectName("dialogClose")
        self.close_button.setFixedSize(22, 22)
        self.close_button.setCursor(Qt.PointingHandCursor)
        self.close_button.clicked.connect(self.reject)
        head_layout.addWidget(self.title_label, 1)
        head_layout.addWidget(self.close_button)
        outer.addWidget(head)

        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(14, 14, 14, 14)
        self.body_layout.setSpacing(12)
        outer.addWidget(self.body)

        foot = QWidget()
        foot.setObjectName("dialogFoot")
        foot_layout = QHBoxLayout(foot)
        foot_layout.setContentsMargins(14, 12, 14, 12)
        foot_layout.setSpacing(8)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("dialogButton")
        self.cancel_button.setCursor(Qt.PointingHandCursor)
        self.cancel_button.clicked.connect(self.reject)
        self.ok_button = QPushButton(ok_text)
        self.ok_button.setObjectName("primaryButton")
        self.ok_button.setCursor(Qt.PointingHandCursor)
        self.ok_button.setDefault(True)
        self.ok_button.clicked.connect(self._on_accept)
        foot_layout.addStretch(1)
        foot_layout.addWidget(self.cancel_button)
        foot_layout.addWidget(self.ok_button)
        outer.addWidget(foot)

        self.hint_label = QLabel("")
        self.hint_label.setObjectName("hintLabel")
        self.hint_label.setWordWrap(True)

    # ------------------------------------------------------------------
    def add_field(self, label: str, widget: QWidget) -> None:
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.setSpacing(6)
        text = QLabel(label)
        text.setObjectName("fieldLabel")
        holder_layout.addWidget(text)
        holder_layout.addWidget(widget)
        self.body_layout.addWidget(holder)

    def add_row_field(self, label: str, widget: QWidget) -> QPushButton:
        """一行：输入控件 + 刷新按钮。"""
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.setSpacing(6)
        text = QLabel(label)
        text.setObjectName("fieldLabel")
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        refresh = QPushButton("刷新")
        refresh.setObjectName("panelButton")
        refresh.setCursor(Qt.PointingHandCursor)
        row_layout.addWidget(widget, 1)
        row_layout.addWidget(refresh)
        holder_layout.addWidget(text)
        holder_layout.addWidget(row)
        self.body_layout.addWidget(holder)
        return refresh

    def finish(self) -> None:
        self.body_layout.addWidget(self.hint_label)

    def _on_accept(self) -> None:
        problem = self.validate()
        if problem:
            self.hint_label.setText(problem)
            self.hint_label.setStyleSheet(f"color:{theme.ERROR};")
            return
        self.accept()

    def validate(self) -> str:
        return ""

    # ------------------------------------------------------------------
    def start_scan(self, button: QPushButton, func, on_done) -> None:
        """后台跑一次扫描；期间按钮置灰显示「刷新中…」。"""
        if self._scan is not None:
            return
        text = button.text()
        button.setEnabled(False)
        button.setText("刷新中…")

        worker = ScanWorker(func)  # 不挂 parent：避免弹窗销毁时连带销毁运行中的线程
        _ACTIVE_SCANS.add(worker)
        self._scan = worker

        def _finish(result):
            _ACTIVE_SCANS.discard(worker)
            if self._scan is worker:
                self._scan = None
            worker.deleteLater()
            try:
                button.setEnabled(True)
                button.setText(text)
            except RuntimeError:  # 弹窗已经销毁
                return
            on_done(result)

        worker.finishedWith.connect(_finish)
        worker.start()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._scan is not None and self._scan.isRunning():
            self._scan.wait(3000)
        super().closeEvent(event)


class JLinkSessionDialog(SessionDialog):
    """新建 / 编辑 J-Link 会话。"""

    def __init__(self, session: dict | None = None, parent=None):
        editing = session is not None
        super().__init__(
            "编辑 J-Link 会话" if editing else "新建 J-Link 会话",
            "保存" if editing else "确定",
            parent=parent,
        )
        self._session = session or {}
        config = dict(self._session.get("jlink_config", {}))

        self.name_edit = QLineEdit(str(self._session.get("name", "")))
        self.name_edit.setPlaceholderText("例如 52840_蓝牙")

        self.serial_combo = QComboBox()
        self.serial_combo.setEditable(True)
        self.serial_combo.setMinimumWidth(160)
        serial_number = str(config.get("serial_number", "") or "")
        if serial_number:
            self.serial_combo.addItem(serial_number, serial_number)

        self.device_combo = QComboBox()
        self.device_combo.setEditable(True)
        self.device_combo.addItems(DEVICE_PRESETS)
        device_name = str(config.get("device_name", "") or DEVICE_PRESETS[0])
        index = self.device_combo.findText(device_name)
        if index >= 0:
            self.device_combo.setCurrentIndex(index)
        else:
            self.device_combo.setEditText(device_name)

        self.interface_combo = QComboBox()
        self.interface_combo.addItems(JLINK_INTERFACES)
        interface = str(config.get("interface", "SWD")).upper()
        index = self.interface_combo.findText(interface)
        if index >= 0:
            self.interface_combo.setCurrentIndex(index)

        self.speed_combo = QComboBox()
        self.speed_combo.setEditable(True)
        self.speed_combo.addItems([str(item) for item in SPEED_PRESETS])
        self.speed_combo.setEditText(str(config.get("speed", SPEED_PRESETS[0]) or SPEED_PRESETS[0]))

        self.add_field("会话名称:", self.name_edit)
        self.refresh_button = self.add_row_field("JLink 序列号:", self.serial_combo)
        self.refresh_button.clicked.connect(self.refresh_emulators)
        self.add_field("设备类型 (e.g. STM32F407VE):", self.device_combo)
        self.add_field("接口:", self.interface_combo)
        self.add_field("速率 (kHz):", self.speed_combo)
        self.finish()

    # ------------------------------------------------------------------
    def refresh_emulators(self) -> None:
        self.start_scan(self.refresh_button, list_emulators, self._apply_emulators)

    def _apply_emulators(self, result) -> None:
        current = self.serial_number()
        if isinstance(result, Exception):
            emulators: list[dict] = []
            self.hint_label.setStyleSheet(f"color:{theme.ERROR};")
            self.hint_label.setText(str(result))
        else:
            emulators = list(result)
            self.hint_label.setStyleSheet(f"color:{theme.TEXT_DIM};")
            self.hint_label.setText(f"扫描到 {len(emulators)} 个仿真器")
        self.serial_combo.clear()
        for emulator in emulators:
            self.serial_combo.addItem(emulator["serial_number"], emulator["serial_number"])
        if current:
            index = self.serial_combo.findData(current)
            if index >= 0:
                self.serial_combo.setCurrentIndex(index)
            else:
                self.serial_combo.setEditText(current)

    def serial_number(self) -> str:
        data = self.serial_combo.currentData()
        if data:
            return str(data)
        text = self.serial_combo.currentText().strip()
        return text.split()[0] if text else ""

    def device_name(self) -> str:
        return self.device_combo.currentText().strip()

    def interface(self) -> str:
        return self.interface_combo.currentText().strip() or "SWD"

    def speed(self) -> int:
        try:
            return int(float(self.speed_combo.currentText().strip()))
        except ValueError:
            return 4000

    def validate(self) -> str:
        if not self.name_edit.text().strip():
            return "请填写会话名称"
        if not self.device_name():
            return "请填写设备类型"
        return ""

    def values(self) -> dict:
        return {
            "name": self.name_edit.text().strip(),
            "jlink_config": {
                "device_name": self.device_name(),
                "interface": self.interface(),
                "speed": self.speed(),
                "serial_number": self.serial_number(),
            },
        }


class SerialSessionDialog(SessionDialog):
    """新建 / 编辑串口会话。"""

    def __init__(self, session: dict | None = None, parent=None):
        editing = session is not None
        super().__init__(
            "编辑串口会话" if editing else "新建串口会话",
            "保存" if editing else "确定",
            parent=parent,
        )
        self._session = session or {}
        config = dict(self._session.get("serial_config", {}))

        self.name_edit = QLineEdit(str(self._session.get("name", "")))
        self.name_edit.setPlaceholderText("例如 主控_NSS32")

        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.setMinimumWidth(160)
        self._populate_ports(str(config.get("port", "") or ""))

        self.baud_combo = QComboBox()
        self.baud_combo.setEditable(True)
        self.baud_combo.addItems([str(rate) for rate in BAUDRATES])
        baudrate = str(config.get("baudrate", 115200) or 115200)
        index = self.baud_combo.findText(baudrate)
        if index >= 0:
            self.baud_combo.setCurrentIndex(index)
        else:
            self.baud_combo.setEditText(baudrate)

        self.add_field("会话名称:", self.name_edit)
        self.refresh_button = self.add_row_field("端口:", self.port_combo)
        self.refresh_button.clicked.connect(self.refresh_ports)
        self.add_field("波特率:", self.baud_combo)
        self.finish()

    # ------------------------------------------------------------------
    def _populate_ports(self, keep: str, ports=None) -> None:
        self.port_combo.clear()
        for device, description in (available_ports() if ports is None else ports):
            label = f"{device} - {description}".strip(" -")
            self.port_combo.addItem(label, device)
        self.set_port(keep)

    def refresh_ports(self) -> None:
        self.start_scan(self.refresh_button, available_ports, self._apply_ports)

    def _apply_ports(self, result) -> None:
        current = self.port()
        if isinstance(result, Exception):
            self.hint_label.setStyleSheet(f"color:{theme.ERROR};")
            self.hint_label.setText(str(result))
            return
        self._populate_ports(current, result)
        self.hint_label.setStyleSheet(f"color:{theme.TEXT_DIM};")
        self.hint_label.setText(f"扫描到 {self.port_combo.count()} 个串口")

    def set_port(self, port: str) -> None:
        if not port:
            self.port_combo.setCurrentIndex(-1)
            self.port_combo.setEditText("")
            return
        index = self.port_combo.findData(port)
        if index >= 0:
            self.port_combo.setCurrentIndex(index)
        else:
            self.port_combo.setCurrentIndex(-1)
            self.port_combo.setEditText(port)

    def port(self) -> str:
        data = self.port_combo.currentData()
        if data:
            return str(data)
        text = self.port_combo.currentText().strip()
        return text.split(" ")[0] if text else ""

    def baudrate(self) -> int:
        try:
            return int(float(self.baud_combo.currentText().strip()))
        except ValueError:
            return 115200

    def validate(self) -> str:
        if not self.name_edit.text().strip():
            return "请填写会话名称"
        if not self.port():
            return "请选择串口"
        return ""

    def values(self) -> dict:
        return {
            "name": self.name_edit.text().strip(),
            "serial_config": {"port": self.port(), "baudrate": self.baudrate()},
        }


class DeleteSessionDialog(SessionDialog):
    """删除确认。"""

    def __init__(self, name: str, parent=None):
        super().__init__("删除会话", "确认删除", width=340, parent=parent)
        self.ok_button.setObjectName("dangerButton")
        message = QLabel(f"是否确认删除会话「{name}」？")
        message.setObjectName("fieldLabel")
        message.setWordWrap(True)
        self.body_layout.addWidget(message)
        self.finish()
        self.hint_label.hide()
