"""数据接收工作线程基类，定义统一的信号接口。"""

from __future__ import annotations

import queue
import time

from PySide6.QtCore import QThread, Signal


class BaseWorker(QThread):
    """所有采集线程的基类，负责连接、读取与断线重连。"""

    dataReceived = Signal(bytes)
    stateChanged = Signal(str, bool)
    errorOccurred = Signal(str)
    message = Signal(str)
    gaveUp = Signal()  # 按策略不再重连，线程即将结束

    retry_seconds = 3
    max_retry_seconds = 60
    retry_enabled = True  # 是否自动重连
    use_backoff = True  # 是否指数退避（False = 固定间隔）
    heartbeat_interval = 2.0

    connected_text = "已连接"
    disconnected_text = "未连接"
    connect_failure_text = "连接失败"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._last_heartbeat = 0.0
        self._retry_delay = float(self.retry_seconds)
        self._send_queue: queue.Queue[bytes] = queue.Queue(maxsize=1000)

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def start_work(self) -> None:
        """开始工作。"""
        if self.isRunning():
            return
        self._running = True
        self.message.emit("工作线程启动")
        self.start()

    def stop_work(self, wait_ms: int = 3000) -> bool:
        """停止工作并等待线程退出，返回线程是否已经结束。"""
        if not self.isRunning():
            self._running = False
            return True
        self._running = False
        self.message.emit("正在清理资源")
        if not self.wait(wait_ms):
            self.message.emit("线程已停止（超时）")
            return False
        self.message.emit("线程已完全停止")
        return True

    def is_active(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # 线程执行主体
    # ------------------------------------------------------------------
    def run(self) -> None:  # noqa: D401
        self._retry_delay = float(self.retry_seconds)
        while self._running:
            try:
                self._connect()
            except Exception as exc:  # noqa: BLE001
                self.errorOccurred.emit(f"{self.connect_failure_text}：{exc}")
                self.stateChanged.emit(self.disconnected_text, False)
                if not self._retry_allowed():
                    break
                self._wait_before_retry()
                continue

            # 连上了就把退避重置回初始值
            self._retry_delay = float(self.retry_seconds)
            self.stateChanged.emit(self.connected_text, True)
            try:
                self._read_loop()
            except Exception as exc:  # noqa: BLE001
                if self._running:
                    self.errorOccurred.emit(f"检测到连接断开：{exc}")
            finally:
                try:
                    self._disconnect()
                except Exception as exc:  # noqa: BLE001
                    self.errorOccurred.emit(f"正在清理资源时出错：{exc}")
                if self._running:
                    self.stateChanged.emit(self.disconnected_text, False)

            if self._running:
                if not self._retry_allowed():
                    break
                self._wait_before_retry("检测到连接断开")

        self.message.emit("线程已停止")

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
    def _sleep(self, seconds: float) -> bool:
        """可被打断的等待，返回是否仍在运行。"""
        deadline = time.monotonic() + seconds
        while self._running and time.monotonic() < deadline:
            time.sleep(0.05)
        return self._running

    def _wait_before_retry(self, reason: str = "") -> None:
        """指数退避重试：3s → 6s → 12s … 最长 60s，连上后重新计数。"""
        delay = int(self._retry_delay)
        prefix = f"{reason}，" if reason else ""
        self.message.emit(f"{prefix}{delay} 秒后重试")
        if self._sleep(self._retry_delay) and self.use_backoff:
            self._retry_delay = min(
                self._retry_delay * 2, float(self.max_retry_seconds)
            )

    def _retry_allowed(self) -> bool:
        """不允许重连时给出提示并通知界面，不再拉起线程。"""
        if self.retry_enabled:
            return True
        self.message.emit("按设置不自动重连，请手动重新打开连接")
        self.gaveUp.emit()
        return False

    def heartbeat_due(self) -> bool:
        now = time.monotonic()
        if now - self._last_heartbeat >= self.heartbeat_interval:
            self._last_heartbeat = now
            return True
        return False

    # ------------------------------------------------------------------
    # 子类实现
    # ------------------------------------------------------------------
    def _connect(self) -> None:
        raise NotImplementedError

    def _read_loop(self) -> None:
        raise NotImplementedError

    def _disconnect(self) -> None:
        """释放资源，子类按需实现。"""

    def send(self, data: bytes) -> bool:
        """把数据排进发送队列，由工作线程实际写出。

        读写使用同一个句柄，必须由同一个线程操作，否则可能出现交叉写坏。
        """
        if not self._running:
            return False
        try:
            self._send_queue.put_nowait(bytes(data))
        except queue.Full:
            self.errorOccurred.emit("写入错误：发送队列已满")
            return False
        return True

    def _drain_send_queue(self) -> None:
        """在工作线程里把排队的数据发出去。"""
        while True:
            try:
                payload = self._send_queue.get_nowait()
            except queue.Empty:
                return
            try:
                self._write(payload)
            except Exception as exc:  # noqa: BLE001
                self.errorOccurred.emit(f"写入错误：{exc}")

    def _write(self, data: bytes) -> None:
        """实际写数据，子类按需实现。"""
