"""UI 响应性探针（开发用）。

起一个标题为 TraceHub-perf 的测试窗口，按模式加载负载，供外部进程
用 SendMessageTimeout(WM_NULL) 测量界面响应延迟。

用法： python tools/ui_perf_probe.py [idle|jlink|stream]
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import time  # noqa: E402

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.config import new_session  # noqa: E402
from gui import theme  # noqa: E402
from gui.main_window import MainWindow  # noqa: E402

LINE = b"[INFO] perf test line level=3 battery=87 temperature=26.5 status=ok\n"
TICK_MS = 20


class LoopMonitor:
    """量 GUI 线程事件循环的抖动：健康时接近 TICK_MS，被阻塞时会明显变大。"""

    def __init__(self, path: Path):
        self.path = path
        self.samples: list[float] = []
        self._last = time.perf_counter()
        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)
        self._timer.start(TICK_MS)
        self._stat_timer = QTimer()
        self._stat_timer.timeout.connect(self._report)
        self._stat_timer.start(5000)

    def _tick(self) -> None:
        now = time.perf_counter()
        self.samples.append((now - self._last) * 1000)
        self._last = now
        if len(self.samples) > 5000:
            del self.samples[:2500]

    def _report(self) -> None:
        if not self.samples:
            return
        data = sorted(self.samples)
        self.samples.clear()
        line = (
            f"[{time.strftime('%H:%M:%S')}] tick 间隔 中位 {data[len(data)//2]:6.1f} ms "
            f"| 95% {data[int(len(data)*0.95)]:6.1f} ms | 最大 {data[-1]:7.1f} ms "
            f"| 样本 {len(data)}\n"
        )
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(line)
        print(line.strip(), flush=True)


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "idle"
    serial = sys.argv[2] if len(sys.argv) > 2 else "59607166"
    app = QApplication([])
    app.setFont(theme.ui_font(13))
    app.setStyleSheet(theme.stylesheet())

    config = PROJECT_ROOT / "build" / "perf-config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    if config.exists():
        config.unlink()

    window = MainWindow(config)
    window.setWindowTitle("TraceHub-perf")
    window.showMaximized()
    monitor = LoopMonitor(PROJECT_ROOT / "build" / "perf-probe.log")

    if mode in ("jlink", "jlink-nospawn"):
        if mode == "jlink-nospawn":
            # 二分用：让"启动子进程"这一步直接失败，只保留父进程的重试循环
            from core.jlink_worker import JLinkWorker

            def boom(self) -> None:
                raise RuntimeError("simulated spawn failure")

            JLinkWorker._start_child = boom
        session = new_session("jlink", "perf-jlink")
        session["jlink_config"].update(
            {
                "device_name": "nRF52840_xxAA",
                "interface": "SWD",
                "speed": 4000,
                "serial_number": serial,
            }
        )
        session["display"]["autosave"] = False
        window.sessions.append(session)
        window._sync_sidebar()
        window.open_session(str(session["id"]))
        view = window.views[str(session["id"])]
        status = QTimer(window)

        def report_state() -> None:
            print(
                f"[{time.strftime('%H:%M:%S')}] 会话状态 {view.current_state()} "
                f"线程在跑={view.is_running()} 行数={view.view.line_number()}",
                flush=True,
            )

        status.timeout.connect(report_state)
        status.start(5000)

    elif mode == "stream":
        session = new_session("serial", "perf-stream")
        session["serial_config"]["port"] = "loop://"
        session["display"]["autosave"] = False
        window.sessions.append(session)
        window._sync_sidebar()
        window.open_session(str(session["id"]))
        view = window.views[str(session["id"])]

        def feed() -> None:
            for _ in range(20):  # 每 20ms 20 行 → 约 1000 行/秒
                view.send_data(LINE)

        timer = QTimer(window)
        timer.timeout.connect(feed)
        timer.start(20)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
