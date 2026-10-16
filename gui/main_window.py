"""TraceHub 主窗口：标题栏 + 侧栏 + 标签栏 + 日志视图。"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.config import ConfigStore, new_session, normalize_session
from utils.timestamp import timestamp_text

from . import theme
from .find_dialog import FindDialog
from .session_dialog import (
    DeleteSessionDialog,
    JLinkSessionDialog,
    SerialSessionDialog,
    SessionDialog,
    run_modal,
)
from .session_tree import Sidebar
from .session_view import SessionView
from .tab_strip import TabStrip
from .title_bar import TitleBar

APP_TITLE = "TraceHub"
VERSION = "1.0.0"

SIDEBAR_BREAKPOINT = 920
DOT_IDLE = theme.INFO
DOT_LIVE = theme.SUCCESS
DOT_ERROR = theme.ERROR


class SendDataDialog(SessionDialog):
    """往当前会话发送数据。"""

    def __init__(self, session_name: str, hex_mode: bool, parent=None):
        super().__init__("发送数据", "发送", width=400, parent=parent)
        self._hex_mode = hex_mode

        title = QLabel(f"会话：{session_name}")
        title.setObjectName("hintLabel")
        self.body_layout.addWidget(title)

        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("01 02 0A" if hex_mode else "要发送的文本")
        self.add_field("发送内容:", self.input_edit)

        hint = QLabel("按十六进制解析" if hex_mode else "按 UTF-8 文本发送")
        hint.setObjectName("hintLabel")
        self.body_layout.addWidget(hint)
        self.finish()

    def validate(self) -> str:
        if not self.input_edit.text():
            return "请输入要发送的内容"
        if self._hex_mode and self.payload() is None:
            return "十六进制格式不正确"
        return ""

    def payload(self) -> bytes | None:
        text = self.input_edit.text()
        if not self._hex_mode:
            return text.encode("utf-8")
        compact = text.replace(",", " ").replace("0x", " ").replace("0X", " ")
        tokens = compact.split()
        if len(tokens) == 1 and len(tokens[0]) % 2 == 0 and len(tokens[0]) > 1:
            tokens = [tokens[0][i : i + 2] for i in range(0, len(tokens[0]), 2)]
        try:
            return bytes(int(token, 16) for token in tokens)
        except ValueError:
            return None


class MainWindow(QMainWindow):
    """主窗口。"""

    def __init__(self, config_path=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_TITLE} v{VERSION}")
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setMinimumSize(900, 560)
        self.store = ConfigStore() if config_path is None else ConfigStore(config_path)

        self.sessions: list[dict] = [normalize_session(item) for item in self.store.sessions]
        self.views: dict[str, SessionView] = {}
        self.tab_order: list[str] = []
        self.active_id: str | None = None
        self.find_dialogs: dict[str, FindDialog] = {}

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self.save_config)

        # 长时间运行兜底：采集线程异常退出时自动重启
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(30 * 1000)
        self._watchdog.timeout.connect(self._check_sessions_alive)
        self._watchdog.start()

        # 运行健康日志：两周连续运行时便于事后核对内存与各会话状态
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(10 * 60 * 1000)
        self._health_timer.timeout.connect(self.write_health_log)
        self._health_timer.start()

        root = QWidget()
        root.setObjectName("appRoot")
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.title_bar = TitleBar()
        root_layout.addWidget(self.title_bar)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        root_layout.addWidget(body, 1)

        self.sidebar = Sidebar()
        body_layout.addWidget(self.sidebar)

        main = QWidget()
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        body_layout.addWidget(main, 1)

        self.tab_strip = TabStrip()
        main_layout.addWidget(self.tab_strip)

        self.stack = QStackedWidget()
        main_layout.addWidget(self.stack, 1)

        self.empty_page = QWidget()
        empty_layout = QVBoxLayout(self.empty_page)
        empty_label = QLabel("从左侧「已存会话」里选择一个追踪源")
        empty_label.setObjectName("hintLabel")
        empty_layout.addStretch(1)
        empty_layout.addWidget(empty_label, 0, Qt.AlignHCenter)
        empty_layout.addStretch(2)
        self.stack.addWidget(self.empty_page)

        self._connect_signals()
        self._sync_sidebar()
        self.sidebar.set_log_path(self.store.log_path)
        self.sidebar.set_display(None)
        self.resize(1180, 760)

    # ------------------------------------------------------------------
    # 信号连接
    # ------------------------------------------------------------------
    def _connect_signals(self) -> None:
        self.title_bar.newJLinkRequested.connect(lambda: self.create_session("jlink"))
        self.title_bar.newSerialRequested.connect(lambda: self.create_session("serial"))
        self.title_bar.minimizeRequested.connect(self.showMinimized)
        self.title_bar.maximizeRequested.connect(self.toggle_maximized)
        self.title_bar.closeRequested.connect(self.close)

        self.sidebar.openRequested.connect(self.open_session)
        self.sidebar.editRequested.connect(self.edit_session)
        self.sidebar.deleteRequested.connect(self.delete_session)
        self.sidebar.displayChanged.connect(self._on_display_changed)
        self.sidebar.autosaveChanged.connect(self._on_autosave_changed)
        self.sidebar.logPathRequested.connect(self.choose_log_path)
        self.sidebar.clearRequested.connect(self.clear_active_log)

        self.tab_strip.activated.connect(self.activate_session)
        self.tab_strip.closed.connect(self.close_tab)
        self.tab_strip.contextRequested.connect(self._on_tab_menu)

        find_action = QAction("查找", self)
        find_action.setShortcut(QKeySequence.Find)
        find_action.triggered.connect(self.show_find)
        self.addAction(find_action)

        reconnect_action = QAction("重连串口", self)
        reconnect_action.setShortcut(QKeySequence("Ctrl+R"))
        reconnect_action.setShortcutContext(Qt.ApplicationShortcut)
        reconnect_action.triggered.connect(self.reconnect_serial_session)
        self.addAction(reconnect_action)

    # ------------------------------------------------------------------
    # 会话管理
    # ------------------------------------------------------------------
    def create_session(self, session_type: str) -> None:
        dialog = (
            JLinkSessionDialog(None, self)
            if session_type == "jlink"
            else SerialSessionDialog(None, self)
        )
        if run_modal(dialog, self) != QDialog.Accepted:
            return
        values = dialog.values()
        session = new_session(session_type, values["name"])
        session.update(values)
        self.sessions.append(session)
        self._sync_sidebar()
        self.schedule_save()
        self.open_session(str(session["id"]))

    def edit_session(self, session_id: str) -> None:
        session = self.find_session(session_id)
        if session is None:
            return
        dialog = (
            JLinkSessionDialog(session, self)
            if session.get("type", "serial") == "jlink"
            else SerialSessionDialog(session, self)
        )
        if run_modal(dialog, self) != QDialog.Accepted:
            return

        values = dialog.values()
        session["name"] = values["name"]
        session.setdefault("serial_config", {}).update(values.get("serial_config", {}))
        session.setdefault("jlink_config", {}).update(values.get("jlink_config", {}))

        view = self.views.get(session_id)
        if view is not None:
            view.session = session
            view.apply_name(session["name"])
            self.tab_strip.set_title(session_id, session["name"])
            if view.is_running():
                view.append_system("配置已更新，正在重新连接")
                view.disconnect_session()
                view.connect_session()

        self._sync_sidebar()
        self._update_display_panel()
        self.schedule_save()

    def delete_session(self, session_id: str) -> None:
        session = self.find_session(session_id)
        if session is None:
            return
        dialog = DeleteSessionDialog(str(session.get("name", "会话")), self)
        if run_modal(dialog, self) != QDialog.Accepted:
            return
        self.close_tab(session_id)
        self.sessions = [
            item for item in self.sessions if str(item.get("id", "")) != session_id
        ]
        self._sync_sidebar()
        self._update_display_panel()
        self.schedule_save()

    def find_session(self, session_id: str) -> dict | None:
        for session in self.sessions:
            if str(session.get("id", "")) == session_id:
                return session
        return None

    # ------------------------------------------------------------------
    # 标签页
    # ------------------------------------------------------------------
    def open_session(self, session_id: str) -> None:
        session = self.find_session(session_id)
        if session is None:
            return

        view = self.views.get(session_id)
        if view is None:
            view = SessionView(session, self.store.log_path, self)
            view.statusChanged.connect(self._on_session_status)
            view.configChanged.connect(self.schedule_save)
            self.views[session_id] = view
            self.tab_order.append(session_id)
            self.stack.addWidget(view)
            self.tab_strip.add_tab(session_id, str(session.get("name", "会话")))

        self.activate_session(session_id)
        if not view.is_running():
            error = view.connect_session()
            if error:
                self._on_session_status(session_id, "err")

    def activate_session(self, session_id: str) -> None:
        view = self.views.get(session_id)
        if view is None:
            return
        self.active_id = session_id
        self.stack.setCurrentWidget(view)
        self.tab_strip.set_active(session_id)
        self.sidebar.set_active(session_id)
        self._update_display_panel()

    def close_tab(self, session_id: str) -> None:
        view = self.views.pop(session_id, None)
        if view is None:
            return
        view.shutdown()
        self.stack.removeWidget(view)
        view.deleteLater()
        self.tab_strip.remove_tab(session_id)
        if session_id in self.tab_order:
            self.tab_order.remove(session_id)
        dialog = self.find_dialogs.pop(session_id, None)
        if dialog is not None:
            dialog.close()

        if self.active_id == session_id:
            self.active_id = self.tab_order[0] if self.tab_order else None
        if self.active_id:
            self.activate_session(self.active_id)
        else:
            self.stack.setCurrentWidget(self.empty_page)
            self.tab_strip.set_active(None)
            self.sidebar.set_active(None)
            self._update_display_panel()

    def _on_tab_menu(self, session_id: str, position: QPoint) -> None:
        from PySide6.QtWidgets import QMenu

        session = self.find_session(session_id)
        view = self.views.get(session_id)
        if session is None or view is None:
            return

        menu = QMenu(self)
        send_action = menu.addAction("发送数据…")
        connect_action = menu.addAction("断开连接" if view.is_running() else "打开连接")
        close_action = menu.addAction("关闭标签")
        chosen = menu.exec(position)

        if chosen is send_action:
            self._send_data(view, session)
        elif chosen is connect_action:
            if view.is_running():
                view.disconnect_session()
            else:
                view.connect_session()
        elif chosen is close_action:
            self.close_tab(session_id)

    def _send_data(self, view: SessionView, session: dict) -> None:
        if not view.is_running():
            view.append_system("未连接，无法发送数据")
            return
        dialog = SendDataDialog(str(session.get("name", "会话")), view.hex_mode(), self)
        if run_modal(dialog, self) != QDialog.Accepted:
            return
        payload = dialog.payload()
        if payload is None:
            return
        if view.send_data(payload):
            preview = dialog.input_edit.text()
            view.append_system(f"已发送 {len(payload)} 字节：{preview}")
        else:
            view.append_system("发送失败")

    # ------------------------------------------------------------------
    # 侧栏 / 显示设置
    # ------------------------------------------------------------------
    def _sync_sidebar(self) -> None:
        self.sidebar.set_sessions([dict(item) for item in self.sessions])
        self.sidebar.set_active(self.active_id)
        # 节点是重建的，按当前采集状态把徽章颜色补回来
        for session_id, view in self.views.items():
            self._on_session_status(session_id, view.current_state())

    def _active_view(self) -> SessionView | None:
        return self.views.get(self.active_id) if self.active_id else None

    def _active_session(self) -> dict | None:
        return self.find_session(self.active_id) if self.active_id else None

    def _update_display_panel(self) -> None:
        session = self._active_session()
        self.sidebar.set_display(None if session is None else session.get("display", {}))

    def _on_display_changed(self, display: dict) -> None:
        session = self._active_session()
        if session is None:
            return
        session.setdefault("display", {}).update(display)
        view = self._active_view()
        if view is not None:
            view.apply_display(session["display"])
        self.schedule_save()

    def _on_autosave_changed(self, enabled: bool) -> None:
        session = self._active_session()
        if session is None:
            return
        session.setdefault("display", {})["autosave"] = bool(enabled)
        view = self._active_view()
        if view is not None and view.is_running():
            if enabled:
                view.start_logging()
            else:
                view.stop_logging()
        self.schedule_save()

    def choose_log_path(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "选择保存目录", self.store.log_path or ""
        )
        if not directory:
            return
        self.store.log_path = directory
        self.sidebar.set_log_path(directory)
        for view in self.views.values():
            view.set_log_path(directory)
        self.schedule_save()

    def clear_active_log(self) -> None:
        view = self._active_view()
        if view is not None:
            view.clear_log()

    def _on_session_status(self, session_id: str, state: str) -> None:
        color = {"live": DOT_LIVE, "err": DOT_ERROR}.get(state, DOT_IDLE)
        self.tab_strip.set_dot(session_id, color)
        self.sidebar.set_status(session_id, state)

    # ------------------------------------------------------------------
    # 保存与查找
    # ------------------------------------------------------------------
    def schedule_save(self) -> None:
        self._save_timer.start(400)

    def save_config(self) -> bool:
        self.store.sessions = [dict(item) for item in self.sessions]
        return self.store.save()

    def show_find(self) -> None:
        view = self._active_view()
        if view is None or self.active_id is None:
            return
        dialog = self.find_dialogs.get(self.active_id)
        if dialog is None:
            dialog = FindDialog(view.view, self)
            self.find_dialogs[self.active_id] = dialog
        cursor = view.view.textCursor()
        selection = cursor.selectedText() if cursor.hasSelection() else ""
        dialog.show_for(selection)

    def reconnect_serial_session(self) -> None:
        """Ctrl+R：只重连当前激活的串口会话，不影响 J-Link 自动重连。"""
        view = self._active_view()
        if view is None or self.active_id is None:
            return
        session = self.find_session(self.active_id)
        if session is None or session.get("type", "serial") != "serial":
            return
        if view.is_running():
            view.append_system("串口连接仍在运行，无需重连")
            return

        view.append_system("Ctrl+R：尝试重新连接串口")
        view.connect_session()

    # ------------------------------------------------------------------
    # 窗口行为
    # ------------------------------------------------------------------
    def toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self.title_bar.set_maximized(self.isMaximized())

    def activate_from_shortcut(self) -> None:
        # 窗口被关掉/隐藏而未退出的情况下，第二次启动要能把它重新显示出来
        if not self.isVisible():
            self.show()
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def changeEvent(self, event) -> None:  # noqa: N802
        if event.type() == event.Type.WindowStateChange:
            self.title_bar.set_maximized(self.isMaximized())
        super().changeEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        # 窄窗口隐藏侧栏（设计稿：≤920px）
        self.sidebar.setVisible(self.width() > SIDEBAR_BREAKPOINT)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.shutdown_sessions()
        for dialog in self.find_dialogs.values():
            dialog.close()
        if not self.save_config():
            QMessageBox.warning(self, "配置", "配置保存失败")
        super().closeEvent(event)

    def shutdown_sessions(self) -> None:
        """停掉所有采集线程（关窗、退出、aboutToQuit 都会走到这里）。"""
        for view in list(self.views.values()):
            view.shutdown()

    # ------------------------------------------------------------------
    # 长时间运行的自愈与体检
    # ------------------------------------------------------------------
    def _check_sessions_alive(self) -> None:
        """看门狗：用户没主动断开、但线程已经不在了，就把它拉起来。"""
        for _session_id, view in list(self.views.items()):
            if view.wants_running() and not view.is_running():
                view.append_system("检测到采集线程已停止，正在重启")
                view.connect_session()

    def write_health_log(self) -> None:
        """把内存占用与各会话状态写一行到 <log_path>/tracehub_health.log。"""
        directory = Path(self.store.log_path or ".").expanduser()
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            return

        path = directory / "tracehub_health.log"
        try:
            if path.exists() and path.stat().st_size > 2 * 1024 * 1024:
                path.replace(path.with_suffix(".log.1"))
        except OSError:
            pass

        rss = ""
        try:
            import psutil

            rss = f" rss={psutil.Process().memory_info().rss / 1024 / 1024:.0f}MB"
        except Exception:  # noqa: BLE001 - 没有 psutil 也不影响主功能
            rss = ""

        details = " ".join(
            f"{session_id[:6]}={view.current_state()}" for session_id, view in self.views.items()
        )
        line = (
            f"{timestamp_text()} sessions={len(self.sessions)} views={len(self.views)}{rss}"
            f"{' ' + details if details else ''}\n"
        )
        try:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError:
            pass
