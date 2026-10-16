"""TraceHub 程序入口。

界面结构、配色与交互按 UI PRD（TraceHub_UI_PRD.md）实现：
标题栏 + 侧栏（会话树 / 显示设置 / 自动保存 / 清空显示）+ 标签栏 + 日志视图。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from core.config import new_session
from core.serial_worker import SerialWorker
from gui import theme
from gui.log_view import LogView, detect_level
from gui.main_window import APP_TITLE, VERSION, MainWindow
from gui.session_dialog import SerialSessionDialog
from gui.session_view import SessionView
from utils.paths import app_dir, assets_dir

PROJECT_ROOT = app_dir()
ASSETS_DIR = assets_dir()
SERVER_NAME = f"{APP_TITLE}-single-instance"
SELFTEST_SERVER_NAME = f"{SERVER_NAME}-selftest"


def find_app_icon() -> Path | None:
    """assets 目录下的程序图标：优先 TraceHub.ico / icon.ico，否则取任意一个 .ico。"""
    for name in ("TraceHub.ico", "icon.ico"):
        preferred = ASSETS_DIR / name
        if preferred.is_file():
            return preferred
    candidates = sorted(ASSETS_DIR.glob("*.ico"))
    return candidates[0] if candidates else None


def notify_existing_instance(name: str = SERVER_NAME) -> bool:
    """已有实例在运行时通知它把窗口拉到前台，返回是否通知成功。"""
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(300):
        return False
    socket.write(b"activate")
    socket.waitForBytesWritten(300)
    socket.disconnectFromServer()
    return True


def create_instance_server(on_activate, name: str = SERVER_NAME) -> QLocalServer | None:
    """监听单实例管道，收到通知时调用 on_activate。"""
    QLocalServer.removeServer(name)
    server = QLocalServer()
    if not server.listen(name):
        return None

    def _handle() -> None:
        while server.hasPendingConnections():
            connection = server.nextPendingConnection()
            if connection is not None:
                connection.readAll()
                connection.disconnected.connect(connection.deleteLater)
        on_activate()

    server.newConnection.connect(_handle)
    return server


# ----------------------------------------------------------------------
# 自检（--self-test，不需要真实硬件）
# ----------------------------------------------------------------------
def _check_window_shell(window: MainWindow) -> list[str]:
    problems: list[str] = []
    if not window.windowTitle().startswith(APP_TITLE):
        problems.append(f"窗口标题异常：{window.windowTitle()!r}")
    if not (window.windowFlags() & Qt.FramelessWindowHint):
        problems.append("窗口不是无边框模式")
    if window.title_bar.height() != 38:
        problems.append(f"标题栏高度异常：{window.title_bar.height()}")
    if window.sidebar.width() != 248:
        problems.append(f"侧栏宽度异常：{window.sidebar.width()}")
    if window.stack.count() < 1:
        problems.append("主视图为空")
    return problems


def _check_level_detection() -> list[str]:
    cases = (
        ("I: nRF5 802154 radio initialized", "INFO"),
        ("[Prob][I]: probe init", "INFO"),
        ("[WAKEUP] MCU uart idle", ""),
        ("[ERROR] CRC 校验失败", "ERROR"),
        ("W: RSSI 偏低 -80 dBm", "WARN"),
        ("D: heap free=1234", "DEBUG"),
        ("sys heap usage: Free: 14684 bytes", ""),
        ("E: 写入 Flash 失败", "ERROR"),
        ("处理完成, no error", ""),
        ("error_count=0, 链路正常", ""),
        ("0 errors reported", ""),
        ("FAIL 打开设备", "ERROR"),
        ("connect: ok", ""),
    )
    problems: list[str] = []
    for text, expected in cases:
        got = detect_level(text)
        if got != expected:
            problems.append(f"级别识别错误：{text[:24]!r} -> {got!r}（应为 {expected!r}）")
    return problems


def _check_log_view_limits() -> list[str]:
    problems: list[str] = []
    view = LogView()
    view.set_max_lines(500)
    entries = [
        (
            f"[ERROR] 第 {index} 行 写入失败" if index % 7 == 0 else f"[INFO] 第 {index} 行 正常输出",
            "[2026-10-14 10:00:00.000]",
        )
        for index in range(5000)
    ]
    view.append_logs(entries)
    if view.document().blockCount() > 501:
        problems.append(f"日志行数未受限：{view.document().blockCount()}")
    if view.line_number() != 5000:
        problems.append(f"行号计数不对：{view.line_number()}")
    if view.gutter_width() <= 0:
        problems.append("行号槽宽度异常")
    view.select_block(view.document().blockCount() - 1)
    if len(view.extraSelections()) != 1:
        problems.append("点击行未产生选中高亮")
    view.clear_log()
    if view.line_number() != 0 or view.document().blockCount() != 1:
        problems.append("清空显示没有重置")
    view.deleteLater()

    blank = LogView()
    blank.set_timestamps(False)
    blank.append_log("")
    blank.append_log("第二行")
    if blank.document().blockCount() != 2:
        problems.append(f"空首行被吞掉：块数 {blank.document().blockCount()}")
    blank.deleteLater()
    return problems


def _check_dialog_defaults() -> list[str]:
    """空端口不能被自动填成第一个可用串口。"""
    problems: list[str] = []
    dialog = SerialSessionDialog(None)
    if dialog.port():
        problems.append(f"新建串口会话默认端口被填充：{dialog.port()!r}")
    dialog.set_port("COM9")
    if dialog.port() != "COM9":
        problems.append(f"自定义端口未保留：{dialog.port()!r}")
    dialog.refresh_ports()
    if dialog.port() != "COM9":
        problems.append(f"刷新端口后自定义端口丢失：{dialog.port()!r}")
    dialog.deleteLater()
    return problems


def _check_single_instance() -> list[str]:
    """第二次启动要能唤起已有窗口。"""
    problems: list[str] = []
    activated: list[bool] = []
    server = create_instance_server(
        lambda: activated.append(True), name=SELFTEST_SERVER_NAME
    )
    if server is None:
        return ["单实例服务端启动失败"]

    if not notify_existing_instance(name=SELFTEST_SERVER_NAME):
        problems.append("未能通知到已运行的实例")

    app = QApplication.instance()
    deadline = time.time() + 2
    while time.time() < deadline and not activated:
        app.processEvents()
        time.sleep(0.02)
    if not activated:
        problems.append("激活回调没有被触发")

    server.close()
    QLocalServer.removeServer(SELFTEST_SERVER_NAME)
    return problems


def _check_app_icon() -> list[str]:
    """程序图标必须存在，并且能被 Qt 加载出至少一个尺寸。"""
    path = find_app_icon()
    if path is None:
        return ["assets 目录里没有 .ico 图标"]
    icon = QIcon(str(path))
    if icon.isNull():
        return [f"图标无法加载：{path.name}"]
    if not icon.availableSizes():
        return [f"图标没有可用尺寸：{path.name}"]
    return []


def _check_logger_lines() -> list[str]:
    """分片到达的数据不能把一行拆成两行写进文件。"""
    import tempfile as temp

    from core.logger import SessionLogger

    problems: list[str] = []
    logger = SessionLogger("断行测试", temp.mkdtemp())
    logger.start()
    logger.write(b"he")
    logger.write(b"llo wo")
    logger.write(b"rld\nsecond line\npartial")
    logger.stop()

    lines = logger.file_path.read_text(encoding="utf-8").splitlines()
    if len(lines) != 3:
        problems.append(f"日志行数不符：{len(lines)} 行 -> {lines}")
    elif "hello world" not in lines[0]:
        problems.append(f"分片被拆成多行：{lines[0]!r}")
    elif "second line" not in lines[1]:
        problems.append(f"第二行内容不符：{lines[1]!r}")
    elif "partial" not in lines[2]:
        problems.append(f"结尾残行未写出：{lines[2]!r}")

    directory = temp.mkdtemp()
    ansi_logger = SessionLogger("ANSI过滤", directory)
    ansi_logger.start()
    ansi_logger.write(b"\x1b[0mhello\x1b[31mworld\x1b[0m\nbell\x07backspace\x08\n")
    ansi_logger.stop()
    ansi_text = ansi_logger.file_path.read_text(encoding="utf-8")
    if "\x1b" in ansi_text:
        problems.append("日志文件里仍包含 ESC/ANSI 转义字节")
    if "helloworld" not in ansi_text or "bell" not in ansi_text or "backspace" not in ansi_text:
        problems.append(f"ANSI/控制字符过滤损坏：{ansi_text!r}")

    directory = temp.mkdtemp()
    first = SessionLogger("同名会话", directory)
    second = SessionLogger("同名会话", directory)
    first.start()
    second.start()
    if first.file_path == second.file_path:
        problems.append(f"同名会话写进了同一个日志文件：{first.file_path.name}")
    first.stop()
    second.stop()
    return problems


def _check_config_atomic() -> list[str]:
    """配置保存不能留下临时文件，未知字段要保留。"""
    import json
    import tempfile as temp

    from core.config import ConfigStore

    problems: list[str] = []
    path = Path(temp.mkdtemp()) / "config.json"
    store = ConfigStore(path)
    store.add_session("serial", "原子写入")
    if not store.save():
        return ["配置保存失败"]
    if not path.exists():
        problems.append("配置未写出")
    leftovers = [item.name for item in path.parent.glob("*.tmp")]
    if leftovers:
        problems.append(f"残留临时文件：{leftovers}")

    compat_path = Path(temp.mkdtemp()) / "config.json"
    compat_path.write_text(
        json.dumps(
            {
                "sessions": [{"id": "x", "type": "serial", "name": "兼容", "future_field": 42}],
                "log_path": "C:/log",
                "extra_top": {"a": 1},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    compat_store = ConfigStore(compat_path)
    compat_store.save()
    saved = json.loads(compat_path.read_text(encoding="utf-8"))
    if saved.get("extra_top") != {"a": 1}:
        problems.append("顶层未知字段被丢弃")
    if saved["sessions"][0].get("future_field") != 42:
        problems.append("会话未知字段被丢弃")
    return problems


def _check_serial_roundtrip(app: QApplication) -> list[str]:
    """用 pyserial 的 loop:// 端口验证串口收发链路。"""
    problems: list[str] = []
    worker = SerialWorker("loop://", 115200)
    received: list[bytes] = []
    states: list[bool] = []
    worker.dataReceived.connect(received.append)
    worker.stateChanged.connect(lambda _text, connected: states.append(connected))
    worker.start_work()

    deadline = time.time() + 3
    while time.time() < deadline and not states:
        app.processEvents()
        time.sleep(0.02)
    if not states:
        problems.append("串口线程未进入已连接状态")

    if not worker.send(b"loopback-check\n"):
        problems.append("串口写入失败")

    deadline = time.time() + 3
    while time.time() < deadline and b"loopback-check" not in b"".join(received):
        app.processEvents()
        time.sleep(0.02)
    worker.stop_work()

    if b"loopback-check" not in b"".join(received):
        problems.append("串口回环数据未收到")
    if worker.isRunning():
        problems.append("串口线程未能停止")
    return problems


def _check_window_controls(window: MainWindow) -> list[str]:
    """窗口控制按钮信号连通。"""
    from PySide6.QtTest import QTest

    problems: list[str] = []

    was_maximized = window.isMaximized()
    QTest.mouseClick(window.title_bar.maximize_button, Qt.LeftButton)
    if window.isMaximized() == was_maximized:
        problems.append("最大化按钮点击无效（状态没有切换）")
    window.showNormal()

    QTest.mouseClick(window.title_bar.minimize_button, Qt.LeftButton)
    if not window.isMinimized():
        problems.append("最小化按钮点击无效")
    window.showNormal()
    QApplication.instance().processEvents()

    fired: list[bool] = []
    window.title_bar.closeRequested.connect(lambda: fired.append(True))
    window.title_bar.close_button.click()
    if not fired:
        problems.append("关闭按钮未发出关闭信号")
    return problems


def _check_retry_policy() -> list[str]:
    """重连策略：串口不自动重连；J-Link 固定 2 秒重连、不做退避。"""
    from core.jlink_worker import JLinkWorker

    problems: list[str] = []

    serial = SerialWorker("loop://", 115200)
    if serial.retry_enabled:
        problems.append("串口不应该自动重连")

    jlink = JLinkWorker("nRF52840_xxAA", "SWD", 4000, "")
    if not jlink.retry_enabled:
        problems.append("J-Link 应该自动重连")
    if jlink.retry_seconds != 2 or jlink.use_backoff:
        problems.append(
            f"J-Link 应为固定 2 秒重连（当前 {jlink.retry_seconds}s, 退避={jlink.use_backoff}）"
        )

    delays: list[float] = []
    jlink._sleep = lambda seconds: (delays.append(seconds), True)[1]
    for _ in range(4):
        jlink._wait_before_retry()
    if delays != [2, 2, 2, 2]:
        problems.append(f"J-Link 重试间隔不是固定 2 秒：{delays}")
    return problems


def _check_jlink_target_access_watch() -> list[str]:
    """J-Link 断线检测：安静时保持连接，持续不可访问且无数据才断开。"""
    from core.jlink_process import TARGET_LOSS_CONFIRM_SECONDS, TargetAccessWatch

    problems: list[str] = []

    class FakeJLink:
        def __init__(self, results: list[object]) -> None:
            self.results = list(results)

        def core_id(self):
            if not self.results:
                raise RuntimeError("target unavailable")
            result = self.results[0]
            if result is False:
                raise RuntimeError("target unavailable")
            return 0x2A0C6800

        def memory_read32(self, address: int, count: int):  # noqa: ARG002
            if not self.results:
                raise RuntimeError("target unavailable")
            result = self.results.pop(0)
            if result is False:
                raise RuntimeError("target unavailable")
            if result is True:
                return [0x47474553]
            return [int(result)]

    for healthy_results in ([True], [0]):
        watch = TargetAccessWatch(0x20018C50)
        try:
            watch.check(FakeJLink(healthy_results))
        except RuntimeError:
            problems.append(f"目标 RAM 返回 {healthy_results} 却被误判为断开")

    transient = TargetAccessWatch(0x20018C50)
    try:
        transient.check(FakeJLink([False, True]))
    except RuntimeError:
        problems.append("一次 RAM 读取失败被误判为断开")

    offline = TargetAccessWatch(0x20018C50)
    try:
        offline.check(FakeJLink([False, False]))
    except RuntimeError:
        problems.append("RAM 刚开始失败就被断开，没有保留确认窗口")

    offline.first_failure_at = (offline.first_failure_at or 0) - (TARGET_LOSS_CONFIRM_SECONDS + 0.1)
    try:
        offline.check(FakeJLink([False, False]))
    except RuntimeError:
        pass
    else:
        problems.append("RAM 持续不可访问超过确认窗口后仍未断开")

    wake = TargetAccessWatch(0x20018C50)
    wake.check(FakeJLink([False, False]))
    wake.first_failure_at = (wake.first_failure_at or 0) - (TARGET_LOSS_CONFIRM_SECONDS + 0.1)
    wake.mark_data_seen()
    if wake.first_failure_at is not None:
        problems.append("RTT 数据出现后没有撤销 RAM 失败状态")
    if not wake.data_seen_recently():
        problems.append("RTT 数据未被识别为最近收到")
    try:
        wake.check(FakeJLink([True]))
    except RuntimeError:
        problems.append("唤醒后 RAM 恢复可读却被误判为断开")

    wake.last_data_at = (wake.last_data_at or 0) - (TARGET_LOSS_CONFIRM_SECONDS + 0.1)
    if wake.data_seen_recently():
        problems.append("过期的 RTT 数据被当作当前存活证据")
    return problems


def _check_log_single_file() -> list[str]:
    """按需求日志不轮转：始终写同一个文件，不额外切分。"""
    import tempfile as temp

    from core.logger import SessionLogger

    problems: list[str] = []
    directory = Path(temp.mkdtemp())
    logger = SessionLogger("单文件测试", str(directory))
    logger.start()
    for index in range(2000):
        logger.write(f"第 {index} 行 数据内容占位\n".encode())
    logger.stop()

    files = sorted(directory.glob("*.log"))
    if len(files) != 1:
        problems.append(f"日志被切分成了 {len(files)} 个文件（应保持单文件）")
    elif files[0].stat().st_size < 20000:
        problems.append(f"单文件内容偏少：{files[0].stat().st_size} 字节")
    return problems


def _check_watchdog(window: MainWindow) -> list[str]:
    """采集线程异常退出时，看门狗要把它拉起来。"""
    app = QApplication.instance()
    problems: list[str] = []

    session = new_session("serial", "看门狗")
    session["serial_config"]["port"] = "loop://"
    session["display"]["autosave"] = False
    session_id = str(session["id"])
    window.sessions.append(session)
    window._sync_sidebar()
    window.open_session(session_id)
    view = window.views[session_id]

    deadline = time.time() + 3
    while time.time() < deadline and not view.is_running():
        app.processEvents()
        time.sleep(0.02)
    if not view.is_running():
        window.close_tab(session_id)
        return ["看门狗用例：会话没能启动"]

    view.worker.stop_work()
    if view.is_running():
        problems.append("模拟线程退出失败")
    window._check_sessions_alive()

    deadline = time.time() + 3
    while time.time() < deadline and not view.is_running():
        app.processEvents()
        time.sleep(0.02)
    if not view.is_running():
        problems.append("看门狗没有重启采集线程")

    window.close_tab(session_id)
    window.sessions = [item for item in window.sessions if str(item.get("id")) != session_id]
    window._sync_sidebar()
    return problems


def _check_jlink_gave_up() -> list[str]:
    """J-Link 子进程放弃后，看门狗语义上仍要保持"希望运行"。"""
    problems: list[str] = []

    for session_type in ("serial", "jlink"):
        session = new_session(session_type, f"放弃测试-{session_type}")
        view = SessionView(session, "")
        view._want_running = True
        view._error_pending = True
        view._on_gave_up()
        view.deleteLater()
        wants = view.wants_running()
        if session_type == "jlink" and not wants:
            problems.append("J-Link gaveUp 后看门狗不会再拉起采集")
        if session_type == "serial" and wants:
            problems.append("串口 gaveUp 后仍会被看门狗反复拉起")
    return problems


def _check_jlink_stuck_child() -> list[str]:
    """J-Link 子进程卡在 DLL 里时，父线程要能识别并强制重启。"""
    from core.jlink_worker import JLinkWorker

    problems: list[str] = []
    worker = JLinkWorker("nRF52840_xxAA", "SWD", 4000, "")
    now = time.monotonic()
    worker._running = True
    worker._connected = True
    worker._last_child_alive = now

    if worker._child_stuck_reason(now):
        problems.append("正常心跳被误判为 J-Link 子进程无响应")

    messages: list[str] = []
    worker.message.connect(messages.append)
    worker._last_child_alive = 0.0
    worker._handle_stderr("ALIVE\n")
    if worker._last_child_alive <= 0.0:
        problems.append("ALIVE 心跳没有刷新子进程存活时间")
    if messages:
        problems.append("ALIVE 心跳不应该显示给用户")

    stuck_now = time.monotonic()
    worker._last_child_alive = stuck_now - worker.child_stuck_seconds - 1
    if not worker._child_stuck_reason(stuck_now):
        problems.append("已连接且无心跳的 J-Link 子进程没有被判为无响应")

    worker._connected = False
    worker._last_child_alive = stuck_now - worker.child_connect_stuck_seconds - 1
    if not worker._child_stuck_reason(stuck_now):
        problems.append("未连接且无心跳的 J-Link 子进程没有被判为无响应")

    class _FakePipe:
        def close(self) -> None:
            return None

    class _FakeProcess:
        def __init__(self) -> None:
            self.stdin = _FakePipe()
            self.stdout = _FakePipe()
            self.stderr = _FakePipe()
            self.terminated = False
            self.killed = False

        def poll(self):
            return None

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

        def wait(self, timeout=None):
            return 0

    process = _FakeProcess()
    worker._connected = True
    worker._process = process
    worker._last_child_alive = time.monotonic() - worker.child_stuck_seconds - 1
    worker._supervise()
    if not (process.terminated or process.killed):
        problems.append("无响应的 J-Link 子进程没有被强制停止")
    if worker._process is not None:
        problems.append("强制停止后 J-Link 子进程句柄没有清空")

    worker.deleteLater()
    return problems


def _check_jlink_pipe_drain() -> list[str]:
    """停止 J-Link 子进程时，管道里的尾部数据要被读完并关闭。"""
    import threading

    from core.jlink_worker import JLinkWorker

    problems: list[str] = []

    class _FakePipe:
        def __init__(self, chunks: list[bytes]) -> None:
            self._chunks = chunks
            self.closed = False

        def read1(self, size: int):  # noqa: ARG002
            return self._chunks.pop(0) if self._chunks else b""

        def readline(self):
            return self.read1(4096)

        def close(self) -> None:
            self.closed = True

    class _FakeProcess:
        def __init__(self) -> None:
            self.stdin = _FakePipe([])

        def poll(self):
            return 0

    worker = JLinkWorker("nRF52840_xxAA", "SWD", 4000, "")
    stdout = _FakePipe([b"tail-log\n"])
    received: list[bytes] = []
    stdout_thread = threading.Thread(target=worker._read_pipe, args=(stdout, received.append))
    stdout_thread.start()
    worker._process = _FakeProcess()
    worker._reader_threads = [stdout_thread]
    worker._stop_child()
    if stdout_thread.is_alive():
        problems.append("J-Link stdout 读线程没有退出")
    if not stdout.closed:
        problems.append("J-Link stdout 管道没有被读线程关闭")
    if b"tail-log\n" not in received:
        problems.append("J-Link stdout 尾部数据没有读到")

    stderr = _FakePipe([b"ALIVE\n"])
    stderr_thread = threading.Thread(target=worker._read_stderr, args=(stderr,))
    stderr_thread.start()
    worker._process = _FakeProcess()
    worker._reader_threads = [stderr_thread]
    worker._stop_child()
    if stderr_thread.is_alive():
        problems.append("J-Link stderr 读线程没有退出")
    if not stderr.closed:
        problems.append("J-Link stderr 管道没有被读线程关闭")
    if worker._last_child_alive <= 0:
        problems.append("J-Link stderr 心跳没有被父进程接收")

    worker.deleteLater()
    return problems


def _check_error_path(window: MainWindow) -> list[str]:
    """端口打不开时：要有提示、状态灯变红、并且仍然能正常关掉。"""
    app = QApplication.instance()
    problems: list[str] = []

    session = new_session("serial", "错误路径")
    session["serial_config"]["port"] = "COM99"
    session["display"]["autosave"] = False
    session_id = str(session["id"])
    window.sessions.append(session)
    window._sync_sidebar()
    window.open_session(session_id)
    view = window.views[session_id]

    item = window.tab_strip._items.get(session_id)
    deadline = time.time() + 5
    while time.time() < deadline:
        app.processEvents()
        if item is not None and theme.ERROR in item.dot.styleSheet():
            break
        time.sleep(0.02)

    if view.view.line_number() == 0:
        problems.append("端口打不开时没有任何提示")
    if item is None or theme.ERROR not in item.dot.styleSheet():
        problems.append("错误状态没有反映到标签圆点")

    deadline = time.time() + 3
    while time.time() < deadline and view.is_running():
        app.processEvents()
        time.sleep(0.02)
    if view.is_running():
        problems.append("串口打不开时仍在重试（按设置不应重连）")
    if view.wants_running():
        problems.append("串口失败后仍标记为需要运行（看门狗会反复拉起）")

    window.close_tab(session_id)
    if view.worker is not None:
        problems.append("错误路径下关闭标签没有停掉线程")

    window.sessions = [item for item in window.sessions if str(item.get("id")) != session_id]
    window._sync_sidebar()
    return problems


def _check_tab_close_rule(window: MainWindow) -> list[str]:
    """关掉最后一个标签要回到空状态页，同时随时能断开连接。"""
    problems: list[str] = []
    sessions: list[dict] = []
    for name in ("标签A", "标签B"):
        session = new_session("serial", name)
        session["serial_config"]["port"] = "loop://"
        session["display"]["autosave"] = False
        window.sessions.append(session)
        sessions.append(session)
    window._sync_sidebar()

    first, second = sessions
    window.open_session(str(first["id"]))
    app = QApplication.instance()
    deadline = time.time() + 3
    while time.time() < deadline and not window.views[str(first["id"])].is_running():
        app.processEvents()
        time.sleep(0.02)

    view = window.views[str(first["id"])]
    if not view.is_running():
        problems.append("会话未进入运行状态")
    view.disconnect_session()
    if view.is_running():
        problems.append("断开连接没有停掉采集")

    tab_item = window.tab_strip._items[str(first["id"])]
    if not tab_item.close_button.isEnabled():
        problems.append("关闭按钮不可用")

    window.tab_strip._on_close_requested(str(first["id"]))
    if window.tab_strip.count() != 0:
        problems.append(f"最后一个标签没有关闭：{window.tab_strip.count()}")
    if window.stack.currentWidget() is not window.empty_page:
        problems.append("关闭最后一个标签后没有回到空状态页")
    if window.active_id is not None:
        problems.append("关闭最后一个标签后仍存在活动会话")
    if window.sidebar.hex_check.isEnabled():
        problems.append("关闭最后一个标签后显示设置未禁用")

    window.open_session(str(second["id"]))
    app.processEvents()
    if window.tab_strip.count() != 1:
        problems.append("重新打开会话失败")
    window.close_tab(str(second["id"]))
    if window.tab_strip.count() != 0:
        problems.append("再次关闭标签失败")
    if window.stack.currentWidget() is not window.empty_page:
        problems.append("空状态页没有保持")

    keep_ids = {str(first["id"]), str(second["id"])}
    window.sessions = [item for item in window.sessions if str(item.get("id")) not in keep_ids]
    window._sync_sidebar()
    return problems


def _check_rtt_address_support() -> list[str]:
    """RTT 控制块：自动搜索失败时要能自己扫到，地址也要能存进会话配置。"""
    from core.config import normalize_session
    from core.jlink_process import RTT_SIGNATURE, find_rtt_control_block

    problems: list[str] = []
    target = 0x20003010

    class FakeJLink:
        def memory_read8(self, addr, size):
            data = bytearray(size)
            if addr <= target < addr + size:
                offset = target - addr
                data[offset : offset + len(RTT_SIGNATURE)] = RTT_SIGNATURE
            return list(data)

    found = find_rtt_control_block(FakeJLink())
    if found != target:
        problems.append(f"RTT 控制块扫描结果不对：{found!r}（应为 {hex(target)}）")

    session = normalize_session(
        {
            "id": "x",
            "type": "jlink",
            "name": "n",
            "jlink_config": {"device_name": "nRF52840_xxAA", "rtt_address": "0x20018bb8"},
        }
    )
    if session["jlink_config"].get("rtt_address") != "0x20018bb8":
        problems.append("会话配置里的 rtt_address 没有被保留")
    return problems


def _check_double_click_open(window: MainWindow) -> list[str]:
    """侧栏会话：单击不开，双击才打开。"""
    from PySide6.QtTest import QTest

    app = QApplication.instance()
    problems: list[str] = []

    session = new_session("serial", "双击测试")
    session["serial_config"]["port"] = "loop://"
    session["display"]["autosave"] = False
    session_id = str(session["id"])
    window.sessions.append(session)
    window._sync_sidebar()

    node = window.sidebar._nodes.get(session_id)
    if node is None:
        return ["侧栏节点缺失"]

    QTest.mouseClick(node, Qt.LeftButton)
    app.processEvents()
    if window.tab_strip.count() != 0:
        problems.append("单击就打开了会话（应该双击才打开）")

    QTest.mouseDClick(node, Qt.LeftButton)
    app.processEvents()
    if window.tab_strip.count() != 1:
        problems.append("双击没有打开会话")

    window.close_tab(session_id)
    window.sessions = [item for item in window.sessions if str(item.get("id")) != session_id]
    window._sync_sidebar()
    return problems


def _check_find_dialog(window: MainWindow) -> list[str]:
    """查找弹窗：回车查找、右上角匹配计数、找不到时的提示。"""
    app = QApplication.instance()
    problems: list[str] = []

    session = new_session("serial", "查找测试")
    session["serial_config"]["port"] = "loop://"
    session["display"]["autosave"] = False
    session_id = str(session["id"])
    window.sessions.append(session)
    window._sync_sidebar()
    window.open_session(session_id)
    view = window.views[session_id]

    deadline = time.time() + 3
    while time.time() < deadline and view.current_state() != "live":
        app.processEvents()
        time.sleep(0.02)

    for index in range(3):
        view.view.append_log(f"[INFO] find-token 第 {index} 行", timestamp="[T]")
    view.view.append_log("[INFO] 另一行", timestamp="[T]")

    window.show_find()
    dialog = window.find_dialogs.get(session_id)
    if dialog is None:
        window.close_tab(session_id)
        return problems + ["查找对话框未创建"]

    dialog.find_edit.setText("find-token")
    dialog.recount()
    if not dialog.counter_label.text().endswith("/3"):
        problems.append(f"匹配总数不对：{dialog.counter_label.text()!r}（应为 x/3）")

    dialog.find_next()
    if dialog.editor.textCursor().selectedText() != "find-token":
        problems.append("回车查找没有选中目标")
    if dialog.counter_label.text() != "1/3":
        problems.append(f"匹配计数不对：{dialog.counter_label.text()!r}（应为 '1/3'）")
    highlighted = sum(
        1
        for selection in dialog.editor.extraSelections()
        if selection.cursor.selectedText() == "find-token"
    )
    if highlighted != 3:
        problems.append(f"匹配项没有全部高亮（当前 {highlighted} 个，应为 3 个）")

    dialog.find_next()
    if dialog.counter_label.text() != "2/3":
        problems.append(f"下一个之后计数不对：{dialog.counter_label.text()!r}")

    dialog.first_button.click()
    if dialog.counter_label.text() != "1/3":
        problems.append(f"顶部跳转后计数不对：{dialog.counter_label.text()!r}（应为 '1/3'）")
    dialog.last_button.click()
    if dialog.counter_label.text() != "3/3":
        problems.append(f"底部跳转后计数不对：{dialog.counter_label.text()!r}（应为 '3/3'）")

    dialog._count_timer.stop()
    view.view.append_log("[INFO] find-token 新增行", timestamp="[T]")
    QApplication.processEvents()
    if dialog.counter_label.text() != "3/4":
        problems.append(f"新增日志没有立即刷新计数：{dialog.counter_label.text()!r}（应为 '3/4'）")
    highlighted = sum(
        1
        for selection in dialog.editor.extraSelections()
        if selection.cursor.selectedText() == "find-token"
    )
    if highlighted != 4:
        problems.append(f"新增匹配没有自动高亮（当前 {highlighted} 个，应为 4 个）")

    dialog.find_edit.setText("不存在的关键字")
    dialog.find_next()
    if "未找到" not in dialog.status_label.text():
        problems.append("找不到匹配项时没有提示")
    if dialog.counter_label.text() != "0/0":
        problems.append(f"无匹配时计数不对：{dialog.counter_label.text()!r}")
    highlighted = sum(
        1
        for selection in dialog.editor.extraSelections()
        if selection.cursor.selectedText() == "不存在的关键字"
    )
    if highlighted:
        problems.append("无匹配时仍保留了查找高亮")

    dialog.find_edit.setText("find-token")
    QApplication.processEvents()
    time.sleep(0.25)
    QApplication.processEvents()
    dialog.close()
    highlighted = sum(
        1
        for selection in dialog.editor.extraSelections()
        if selection.cursor.selectedText() == "find-token"
    )
    if highlighted:
        problems.append("关闭查找弹窗后没有清理高亮")
    window.close_tab(session_id)
    window.sessions = [item for item in window.sessions if str(item.get("id")) != session_id]
    window._sync_sidebar()
    return problems


def _check_modal_flow(window: MainWindow) -> list[str]:
    """弹窗遮罩的建立与清理。"""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialog

    from gui.session_dialog import JLinkSessionDialog, ModalBackdrop, run_modal

    problems: list[str] = []
    dialog = JLinkSessionDialog(None, window)
    QTimer.singleShot(150, dialog.reject)
    if run_modal(dialog, window) != QDialog.Rejected:
        problems.append("弹窗取消流程异常")
    from PySide6.QtCore import QEvent

    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    QApplication.instance().processEvents()
    if window.findChildren(ModalBackdrop):
        problems.append("弹窗遮罩未清理")
    dialog.deleteLater()
    return problems


def _check_session_flow(window: MainWindow) -> list[str]:
    """会话从侧栏打开到关闭的完整流程。"""
    app = QApplication.instance()
    problems: list[str] = []
    window.showMaximized()
    app.processEvents()

    session = new_session("serial", "自检会话")
    session["serial_config"]["port"] = "loop://"
    session["display"]["autosave"] = False
    session_id = str(session["id"])
    window.sessions.append(session)
    window._sync_sidebar()

    if session_id not in window.sidebar._nodes:
        problems.append("侧栏没有生成会话节点")

    window.open_session(session_id)
    view = window.views.get(session_id)
    if view is None:
        return problems + ["会话视图未创建"]

    deadline = time.time() + 3
    while time.time() < deadline and not view.is_running():
        app.processEvents()
        time.sleep(0.02)
    if not view.is_running():
        problems.append("会话未进入运行状态")
    if window.tab_strip.count() != 1:
        problems.append(f"标签栏数量异常：{window.tab_strip.count()}")

    view.disconnect_session(wait_ms=1000)
    deadline = time.time() + 3
    while time.time() < deadline and view.is_running():
        app.processEvents()
        time.sleep(0.02)
    if view.is_running():
        problems.append("串口断开后线程仍在运行")

    reconnect_actions = [
        action
        for action in window.actions()
        if action.shortcut().toString().lower() == "ctrl+r"
    ]
    if not reconnect_actions:
        problems.append("没有注册 Ctrl+R 重连串口快捷键")
    else:
        reconnect_actions[0].trigger()
    deadline = time.time() + 3
    while time.time() < deadline and view.current_state() != "live":
        app.processEvents()
        time.sleep(0.02)
    if view.current_state() != "live":
        problems.append("Ctrl+R 没有重连串口会话")

    deadline = time.time() + 3
    while time.time() < deadline and view.current_state() != "live":
        app.processEvents()
        time.sleep(0.02)
    window._sync_sidebar()
    node = window.sidebar._nodes.get(session_id)
    if node is None or str(node.badge.property("state")) != "live":
        problems.append("侧栏刷新后没有恢复运行状态")

    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent

    enter_event = QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier)
    window.raise_()
    window.activateWindow()
    view.view.setFocus()
    app.processEvents()
    QApplication.sendEvent(view.view, enter_event)
    if not view.command_bar.isVisible():
        problems.append("按 Enter 没有进入串口输入状态")

    view.command_input.setText("command-check")
    view.send_command()
    deadline = time.time() + 3
    while time.time() < deadline and "command-check" not in view.view.toPlainText():
        app.processEvents()
        time.sleep(0.02)
    if "command-check" not in view.view.toPlainText():
        problems.append("串口输入状态没有发送命令")
    if view.command_input.text():
        problems.append("命令发送后输入框没有清空")

    view.command_input.setText("not-sent")
    view.command_close_button.click()
    if view.command_bar.isVisible():
        problems.append("关闭按钮没有退出串口输入状态")
    if view.command_input.text():
        problems.append("关闭按钮没有清空输入框")

    QApplication.sendEvent(view.view, enter_event)
    if not view.command_bar.isVisible():
        problems.append("关闭后不能重新进入串口输入状态")
    escape_event = QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
    QApplication.sendEvent(view.command_input, escape_event)
    if view.command_bar.isVisible():
        problems.append("按 Esc 没有退出串口输入状态")

    if view.send_data(b"tracehub-check\n"):
        deadline = time.time() + 3
        while time.time() < deadline and "tracehub-check" not in view.view.toPlainText():
            app.processEvents()
            time.sleep(0.02)
    view._flush_pending()
    view._flush_lines()
    if "tracehub-check" not in view.view.toPlainText():
        problems.append("日志视图未显示回环数据")
    if view.view.line_number() <= 0:
        problems.append("日志行号没有累加")

    node = window.sidebar._nodes.get(session_id)
    if node is None:
        problems.append("侧栏节点缺失")
    else:
        node.activated.emit(session_id)
        if window.active_id != session_id:
            problems.append("双击侧栏节点没有激活会话")

    window.sidebar.hex_check.setChecked(True)
    app.processEvents()
    if not session["display"].get("hex"):
        problems.append("显示设置没有写回会话")
    window.sidebar.hex_check.setChecked(False)
    window.sidebar.timestamp_check.setChecked(False)
    app.processEvents()
    if view.view.timestamps():
        problems.append("关闭时间戳后视图仍在显示时间戳")
    window.sidebar.timestamp_check.setChecked(True)

    window.close_tab(session_id)
    if window.tab_strip.count() != 0:
        problems.append("关闭标签后未清理")
    if view.worker is not None:
        problems.append("关闭标签后线程仍在运行")

    window.sessions = [item for item in window.sessions if str(item.get("id")) != session_id]
    window._sync_sidebar()
    return problems


def _self_test(window: MainWindow, screenshot: str = "") -> int:
    """无界面自检：界面骨架、日志视图、会话流程与配置。"""
    app = QApplication.instance()
    problems: list[str] = []

    problems.extend(_check_window_shell(window))
    problems.extend(_check_window_controls(window))
    problems.extend(_check_level_detection())
    problems.extend(_check_log_view_limits())
    problems.extend(_check_dialog_defaults())
    problems.extend(_check_app_icon())
    problems.extend(_check_single_instance())
    problems.extend(_check_logger_lines())
    problems.extend(_check_log_single_file())
    problems.extend(_check_jlink_target_access_watch())
    problems.extend(_check_retry_policy())
    problems.extend(_check_config_atomic())
    problems.extend(_check_serial_roundtrip(app))
    problems.extend(_check_modal_flow(window))
    problems.extend(_check_find_dialog(window))
    problems.extend(_check_double_click_open(window))
    problems.extend(_check_rtt_address_support())
    problems.extend(_check_tab_close_rule(window))
    problems.extend(_check_error_path(window))
    problems.extend(_check_watchdog(window))
    problems.extend(_check_jlink_gave_up())
    problems.extend(_check_jlink_stuck_child())
    problems.extend(_check_jlink_pipe_drain())
    problems.extend(_check_session_flow(window))

    if screenshot:
        app.processEvents()
        if not window.grab().save(screenshot):
            problems.append(f"截图保存失败：{screenshot}")

    lines = ["SELF-TEST FAILED" if problems else "SELF-TEST OK"]
    lines.extend(f" - {item}" for item in problems)
    if not problems:
        lines.append(f"应用名：{app.applicationName()}")
        lines.append(f"会话数量：{len(window.sessions)}")
        lines.append(f"配置文件：{window.store.path}")
    report = "\n".join(lines) + "\n"

    try:
        print(report, end="")
    except Exception:  # noqa: BLE001
        pass
    if getattr(sys, "frozen", False):
        try:
            (app_dir() / "tracehub-self-test.txt").write_text(report, encoding="utf-8")
        except OSError:
            pass
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)

    if "--jlink-worker" in argv:
        from core.jlink_process import enable_protocol, main as worker_main

        enable_protocol()
        return worker_main(argv[argv.index("--jlink-worker") + 1 :])

    self_test = "--self-test" in argv
    screenshot = ""
    if "--screenshot" in argv:
        index = argv.index("--screenshot")
        if index + 1 < len(argv):
            screenshot = argv[index + 1]

    if not self_test:
        os.environ.pop("QT_QPA_PLATFORM", None)

    app = QApplication(argv)
    app.setApplicationName(APP_TITLE)
    app.setFont(theme.ui_font(13))
    app.setStyleSheet(theme.stylesheet())

    icon_path = find_app_icon()
    if icon_path is not None:
        icon = QIcon(str(icon_path))
        if not icon.isNull():
            app.setWindowIcon(icon)

    if not self_test and notify_existing_instance():
        return 0

    config_path = None
    if self_test:
        config_path = Path(tempfile.mkdtemp(prefix="tracehub-selftest-")) / "config.json"

    window = MainWindow(config_path)
    window.showMaximized()
    window.title_bar.set_maximized(True)

    app.aboutToQuit.connect(window.shutdown_sessions)

    server = None if self_test else create_instance_server(window.activate_from_shortcut)

    if self_test:
        return _self_test(window, screenshot)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
