"""单个会话的日志页：把采集线程的数据写进日志视图与文件。"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.jlink_worker import JLinkWorker
from core.logger import SessionLogger, decode_payload
from core.serial_worker import SerialWorker
from utils.timestamp import timestamp_text

from .log_view import LogView

# 一次涌入太多时先缓冲再渲染
MAX_BUFFERED_LINES = 5000
# 长时间没有换行的输出（例如二进制流）允许占用的最大字符数
MAX_PENDING_CHARS = 64 * 1024

# 停止超时的线程仍在运行，必须由 Python 侧保留引用，
# 否则 QThread 对象被回收会直接让进程崩溃
_ORPHANED_WORKERS: list = []


def _keep_alive(worker) -> None:
    if worker in _ORPHANED_WORKERS:
        return
    _ORPHANED_WORKERS.append(worker)

    def _release() -> None:
        if worker in _ORPHANED_WORKERS:
            _ORPHANED_WORKERS.remove(worker)

    worker.finished.connect(_release)


class SessionView(QWidget):
    """一个会话的日志页。"""

    statusChanged = Signal(str, str)  # session_id, idle / live / err
    configChanged = Signal()  # 需要落盘（例如自动定位到 RTT 控制块地址）

    def __init__(self, session: dict, log_path: str = "", parent=None):
        super().__init__(parent)
        self.session = session
        self.session_id = str(session.get("id", ""))
        self.log_path = log_path or ""
        self.worker = None
        self.logger = SessionLogger(
            session.get("name", "session"),
            self.log_path,
            bool(session.get("display", {}).get("timestamp", True)),
        )
        self._pending = ""
        self._line_buffer: list[tuple[str, str]] = []
        # 断线/连接失败后要保持错误态，不能被随后的“已断开”信号冲掉
        self._error_pending = False
        self._connected = False
        self._want_running = False

        self.view = LogView(self)
        self.command_bar = QWidget(self)
        self.command_bar.setObjectName("commandBar")
        command_layout = QHBoxLayout(self.command_bar)
        command_layout.setContentsMargins(10, 7, 10, 7)
        command_layout.setSpacing(8)

        command_label = QLabel("发送", self.command_bar)
        command_label.setObjectName("commandLabel")
        self.command_input = QLineEdit(self.command_bar)
        self.command_input.setObjectName("commandInput")
        self.command_input.setPlaceholderText("串口命令")
        self.command_input.setToolTip("Enter 发送，Esc 退出输入状态")
        self.command_input.returnPressed.connect(self.send_command)
        self.command_input.installEventFilter(self)
        self.command_close_button = QPushButton("\u2715", self.command_bar)
        self.command_close_button.setObjectName("commandClose")
        self.command_close_button.setFixedSize(22, 22)
        self.command_close_button.setCursor(Qt.PointingHandCursor)
        self.command_close_button.setToolTip("关闭输入条")
        self.command_close_button.clicked.connect(self.close_command_mode)
        command_layout.addWidget(command_label)
        command_layout.addWidget(self.command_input, 1)
        command_layout.addWidget(self.command_close_button)
        self.command_bar.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.view)
        layout.addWidget(self.command_bar)
        self.view.commandModeRequested.connect(self.enter_command_mode)

        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.timeout.connect(self._flush_pending)

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._flush_lines)

        self.apply_display(session.get("display", {}))
        self.apply_name(str(session.get("name", "")))

    # ------------------------------------------------------------------
    # 设置
    # ------------------------------------------------------------------
    def apply_display(self, display: dict) -> None:
        self.view.set_timestamps(bool(display.get("timestamp", True)))
        self.view.set_max_lines(int(display.get("max_lines", 20000) or 20000))
        self.logger.timestamps = bool(display.get("timestamp", True))
        self.command_input.setPlaceholderText(
            "十六进制字节" if bool(display.get("hex", False)) else "串口命令"
        )

    def apply_name(self, name: str) -> None:
        self.logger.session_name = name

    def set_log_path(self, path: str) -> None:
        self.log_path = path or self.log_path
        self.logger.log_path = self.log_path

    def hex_mode(self) -> bool:
        return bool(self.session.get("display", {}).get("hex", False))

    def is_running(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def current_state(self) -> str:
        """live / err / idle，供侧栏与标签栏恢复显示状态。"""
        if self._error_pending:
            return "err"
        return "live" if self._connected else "idle"

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------
    def connect_session(self) -> str:
        """打开连接，返回错误信息（空串表示已开始连接）。"""
        if self.is_running():
            return ""
        self._want_running = False

        session_type = self.session.get("type", "serial")
        if session_type == "serial":
            config = self.session.get("serial_config", {})
            port = str(config.get("port") or "")
            if not port:
                self.append_system("未选择串口，无法打开连接")
                self.statusChanged.emit(self.session_id, "err")
                return "未选择串口"
            self.worker = SerialWorker(port, int(config.get("baudrate", 115200) or 115200))
        else:
            config = self.session.get("jlink_config", {})
            device = str(config.get("device_name") or "")
            if not device:
                self.append_system("未选择设备，无法打开连接")
                self.statusChanged.emit(self.session_id, "err")
                return "未选择设备"
            self.worker = JLinkWorker(
                device,
                str(config.get("interface", "SWD") or "SWD"),
                int(config.get("speed", 4000) or 4000),
                str(config.get("serial_number", "") or ""),
                rtt_address=str(config.get("rtt_address", "") or ""),
                on_address_found=self._remember_rtt_address,
            )

        self.worker.dataReceived.connect(self._on_data)
        self.worker.stateChanged.connect(self._on_state_changed)
        self.worker.errorOccurred.connect(self._on_error)
        self.worker.message.connect(self.append_system)
        self.worker.gaveUp.connect(self._on_gave_up)

        # 日志只在真正连上之后才开始写（避免连接失败时每 2 秒建一个空文件）
        self._error_pending = False
        self.statusChanged.emit(self.session_id, "idle")
        self.worker.start_work()
        self._want_running = True
        return ""

    def disconnect_session(self, wait_ms: int = 3000) -> None:
        worker = self.worker
        self.worker = None
        self._connected = False
        if worker is not None and not worker.stop_work(wait_ms):
            _keep_alive(worker)
        # stop_work 返回时，读线程可能已经把数据通过队列信号发给界面，
        # 但事件还没被处理。先处理一轮，再关闭日志文件，避免丢尾部数据。
        app = QApplication.instance()
        if app is not None:
            app.processEvents()
        self._stop_logging()
        self._error_pending = False
        self._want_running = False
        self.statusChanged.emit(self.session_id, "idle")

    def shutdown(self) -> None:
        # 退出时给 J-Link 管道留出收尾时间；极端卡死才由 _keep_alive 兜住
        self._want_running = False
        self.disconnect_session(wait_ms=4500)

    def wants_running(self) -> bool:
        """用户是否希望这个会话保持采集（看门狗据此自动重启线程）。"""
        return self._want_running

    # ------------------------------------------------------------------
    # 采集数据
    # ------------------------------------------------------------------
    def _start_logging(self) -> None:
        self.logger.log_path = self.log_path or self.logger.log_path
        self.logger.tag = self._log_tag()
        self.append_system(self.logger.start())

    def _stop_logging(self) -> None:
        message = self.logger.stop()
        if message:
            self.append_system(message)

    def start_logging(self) -> None:
        """供侧栏「启用自动保存」开关调用。"""
        self._start_logging()

    def stop_logging(self) -> None:
        self._stop_logging()

    def _log_tag(self) -> str:
        """J-Link 会话在文件名里带上序列号，与旧版工具保持一致。"""
        if self.session.get("type") != "jlink":
            return ""
        serial_number = str(
            self.session.get("jlink_config", {}).get("serial_number", "") or ""
        ).strip()
        return f"_SN{serial_number}" if serial_number else "_SNAuto"

    def _on_data(self, data: bytes) -> None:
        if self.logger.active:
            self.logger.write(data)

        if self.hex_mode():
            self._append_line(" ".join(f"{byte:02X}" for byte in data))
            return

        self._pending += decode_payload(data)
        if "\n" in self._pending:
            parts = self._pending.split("\n")
            self._pending = parts.pop()
            for line in parts:
                self._append_line(line.rstrip("\r"))
        if len(self._pending) > MAX_PENDING_CHARS:
            self._append_line(self._pending)
            self._pending = ""
        elif self._pending:
            self._flush_timer.start(120)

    def _flush_pending(self) -> None:
        if self._pending:
            self._append_line(self._pending)
            self._pending = ""

    def _append_line(self, text: str) -> None:
        self._line_buffer.append((text, timestamp_text()))
        if len(self._line_buffer) >= MAX_BUFFERED_LINES:
            self._flush_lines()
        elif not self._render_timer.isActive():
            self._render_timer.start(50)

    def _flush_lines(self) -> None:
        if not self._line_buffer:
            return
        entries = self._line_buffer
        self._line_buffer = []
        # 批量写入：一个编辑块 + 一次滚动刷新
        self.view.append_logs(entries)

    def append_system(self, text: str) -> None:
        view = getattr(self, "view", None)
        if text and view is not None:
            view.append_system(text, timestamp=timestamp_text())

    def clear_log(self) -> None:
        self._pending = ""
        self._line_buffer.clear()
        self.view.clear_log()

    # ------------------------------------------------------------------
    def _on_state_changed(self, _text: str, connected: bool) -> None:
        self._connected = bool(connected)
        if connected:
            self._error_pending = False
            self.statusChanged.emit(self.session_id, "live")
            if self.session.get("display", {}).get("autosave", True) and not self.logger.active:
                self._start_logging()
        else:
            # J-Link 自动重连属于同一会话的瞬时断线：
            # 保持日志文件打开，重连后继续追加到原文件。
            # 只有手动断开/关闭会话时才由 disconnect_session() 停止记录。
            # 出错导致的断开保持红色，用户主动断开才是普通态
            self.statusChanged.emit(self.session_id, "err" if self._error_pending else "idle")

    def _on_error(self, text: str) -> None:
        self.view.append_system(text, timestamp=timestamp_text())
        self._error_pending = True
        self.statusChanged.emit(self.session_id, "err")

    def _on_gave_up(self) -> None:
        """串口按策略不再重连时，别让看门狗再把它拉起来。

        J-Link 的业务要求是持续无限重连；它的子进程可能会因连续连接失败退出，
        但父线程应该继续拉起，不能把“希望运行”标记清掉。
        """
        if self.session.get("type", "serial") == "jlink":
            return
        self._want_running = False
        self.statusChanged.emit(self.session_id, "err" if self._error_pending else "idle")

    def _remember_rtt_address(self, address: str) -> None:
        """把自动定位到的 RTT 控制块地址写回会话，下次连接直接用。"""
        config = self.session.setdefault("jlink_config", {})
        if str(config.get("rtt_address") or "") != address:
            config["rtt_address"] = address
            self.configChanged.emit()

    def send_data(self, payload: bytes) -> bool:
        if self.worker is None or not self.worker.isRunning():
            return False
        return bool(self.worker.send(payload))

    def enter_command_mode(self) -> None:
        """日志区按 Enter 后进入串口命令输入状态。"""
        if self.session.get("type", "serial") != "serial" or not self.is_running():
            self.append_system("串口未连接，无法进入输入状态")
            return
        self.command_bar.show()
        self.command_input.setFocus()
        self.command_input.selectAll()

    def send_command(self) -> None:
        if self.session.get("type", "serial") != "serial" or not self.is_running():
            self.command_bar.hide()
            return

        text = self.command_input.text()
        if self.hex_mode():
            compact = text.replace(",", " ").replace("0x", " ").replace("0X", " ")
            try:
                payload = bytes.fromhex("".join(compact.split()))
            except ValueError:
                self.append_system("十六进制格式不正确，未发送")
                return
        else:
            payload = text.encode("utf-8") + b"\r\n"

        if not self.send_data(payload):
            self.append_system("发送失败")
            return
        self.command_input.clear()

    def close_command_mode(self) -> None:
        self.command_input.clear()
        self.command_bar.hide()
        self.view.setFocus()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.command_input and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Escape:
                self.close_command_mode()
                return True
        return super().eventFilter(obj, event)
