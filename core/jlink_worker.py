"""J-Link 会话的进程管理。

SEGGER 的 JLinkARM.dll 在一个进程里只能维持一条 USB 连接，所以每个 J-Link 会话
跑一个子进程（core/jlink_process.py），子进程内部自己做重连（固定 2 秒）。

这里刻意不用 QProcess：Qt 的 QProcess 放在没有事件循环的 QThread 里会干扰
主线程的事件分发（实测会让界面定时器从 20ms 拖到 100ms）。改成 subprocess.Popen
+ 纯 Python 读线程，工作线程里不再有任何 Qt 对象。
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

from .interfaces import BaseWorker

try:  # pylink-square
    import pylink
except ImportError:  # pragma: no cover - 缺少驱动时给出提示
    pylink = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHILD_SCRIPT = PROJECT_ROOT / "core" / "jlink_process.py"


def list_emulators() -> list[dict]:
    """扫描已连接的仿真器（只枚举，不建立连接）。"""
    if pylink is None:
        raise RuntimeError("请检查是否已安装 J-Link 驱动")
    jlink = pylink.JLink()
    result = []
    for emulator in jlink.connected_emulators():
        serial_number = getattr(emulator, "SerialNumber", None)
        if serial_number in (None, ""):
            serial_number = getattr(emulator, "serial_number", "")
        product = getattr(emulator, "acProduct", "") or getattr(emulator, "product_name", "")
        if isinstance(product, bytes):
            product = product.decode("utf-8", errors="replace")
        product = str(product).strip() or "J-Link"
        result.append({"serial_number": str(serial_number), "product_name": product})
    return result


def worker_command(args: list[str]) -> list[str]:
    """子进程命令行：源码模式跑脚本，打包模式用同一个 exe 加 --jlink-worker。"""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--jlink-worker", *args]
    return [sys.executable, str(CHILD_SCRIPT), *args]


class JLinkWorker(BaseWorker):
    """每个 J-Link 会话一个子进程，互不干扰（同进程只能有一条 J-Link 连接）。"""

    connect_failure_text = "连接失败"
    retry_seconds = 2  # J-Link 固定 2 秒重连（断开后由父进程重启子进程）
    use_backoff = False
    child_stuck_seconds = 15.0  # 已连接时，子进程心跳丢失多久就强制重启
    child_connect_stuck_seconds = 30.0  # 未连接时，子进程心跳丢失多久就强制重启
    reader_join_seconds = 2.0  # 停止子进程后等待管道收尾的时间

    def __init__(
        self,
        device_name: str,
        interface: str = "SWD",
        speed: int = 4000,
        serial_number: str = "",
        rtt_address: str = "",
        on_address_found=None,
        parent=None,
    ):
        super().__init__(parent)
        self.device_name = device_name
        self.interface = interface
        self.speed = int(speed)
        self.serial_number = str(serial_number or "")
        self.rtt_address = str(rtt_address or "")
        self.on_address_found = on_address_found
        self._process: subprocess.Popen | None = None
        self._reader_threads: list[threading.Thread] = []
        self._stderr_buffer = ""
        self._exit_message = ""
        self._connected = False
        self._data_seen = False
        self._no_data_deadline = 0.0
        self._no_data_hinted = False
        self._last_error = ""
        self._last_error_at = 0.0
        self._last_restart_at = 0.0
        self._last_child_alive = 0.0
        self._last_stuck_message_at = 0.0

    # ------------------------------------------------------------------
    def run(self) -> None:  # noqa: D401
        """监督 J-Link 采集子进程；断开后由父进程重启全新子进程。"""
        self._retry_delay = float(self.retry_seconds)
        while self._running:
            try:
                self._start_child()
            except Exception as exc:  # noqa: BLE001
                self.errorOccurred.emit(f"{self.connect_failure_text}：{exc}")
                if not self._retry_allowed():
                    break
                self._wait_before_retry()
                continue

            self._supervise()
            self._stop_child()

            if self._running:
                if self._connected:
                    self._connected = False
                    self.stateChanged.emit(self.disconnected_text, False)
                now = time.monotonic()
                if now - self._last_restart_at >= 30.0:
                    self._last_restart_at = now
                    self.message.emit(f"采集进程已退出，{self.retry_seconds} 秒后重启")
                self._sleep(float(self.retry_seconds))

        self.message.emit("线程已停止")

    # ------------------------------------------------------------------
    def _start_child(self) -> None:
        args = [
            self.device_name,
            self.interface,
            str(self.speed),
            self.serial_number,
            self.rtt_address,
        ]
        command = worker_command(args)
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        self._stderr_buffer = ""
        self._exit_message = ""
        self._reader_threads = []
        self._connected = False
        self._data_seen = False
        self._no_data_deadline = 0.0
        self._no_data_hinted = False
        self._last_child_alive = time.monotonic()
        self._last_stuck_message_at = 0.0

        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(PROJECT_ROOT) if not getattr(sys, "frozen", False) else None,
            creationflags=creationflags,
        )
        self._process = process

        stdout_thread = threading.Thread(
            target=self._read_pipe, args=(process.stdout, self.dataReceived.emit), daemon=True
        )
        stderr_thread = threading.Thread(
            target=self._read_stderr, args=(process.stderr,), daemon=True
        )
        stdout_thread.start()
        stderr_thread.start()
        self._reader_threads = [stdout_thread, stderr_thread]

    def _supervise(self) -> None:
        while self._running:
            process = self._process
            if process is None or process.poll() is not None:
                return
            reason = self._child_stuck_reason()
            if reason:
                self._report_child_stuck(reason)
                self._stop_child()
                return
            self._maybe_hint_no_data()
            self._drain_send_queue()
            time.sleep(0.05)

    def _child_stuck_reason(self, now: float | None = None) -> str:
        """根据子进程心跳判断它是否卡在 JLinkARM.dll 里。"""
        current = time.monotonic() if now is None else now
        if self._last_child_alive <= 0:
            return ""
        elapsed = current - self._last_child_alive
        if self._connected:
            if elapsed >= self.child_stuck_seconds:
                return f"已连接状态下 {elapsed:.0f} 秒无心跳"
        elif elapsed >= self.child_connect_stuck_seconds:
            return f"未连接状态下 {elapsed:.0f} 秒无心跳"
        return ""

    def _report_child_stuck(self, reason: str) -> None:
        now = time.monotonic()
        if now - self._last_stuck_message_at >= 30.0:
            self._last_stuck_message_at = now
            self.message.emit(f"J-Link 采集进程无响应（{reason}），正在重启")

    def _stop_child(self) -> None:
        process = self._process
        readers = self._reader_threads
        self._process = None
        self._reader_threads = []
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass
        # stdout/stderr 由读线程读到 EOF 后自己关闭。这里等它们收尾，
        # 避免子进程刚写入管道、父进程还没读完就被关闭而丢尾部日志。
        for thread in readers:
            if thread.is_alive():
                thread.join(timeout=self.reader_join_seconds)

    def _write(self, data: bytes) -> None:
        process = self._process
        if process is None or process.poll() is not None or process.stdin is None:
            raise RuntimeError("J-Link 未连接")
        process.stdin.write(b"WRITE " + data.hex().encode("ascii") + b"\n")
        process.stdin.flush()

    # ------------------------------------------------------------------
    def _read_pipe(self, pipe, emit) -> None:
        try:
            while True:
                # BufferedReader.read(n) 会等满 n 字节或 EOF；
                # RTT 数据常常不足 4096，必须用 read1 读当前可用数据。
                chunk = pipe.read1(4096)
                if not chunk:
                    return
                self._data_seen = True
                emit(bytes(chunk))
        except (OSError, ValueError):
            return
        finally:
            try:
                pipe.close()
            except (OSError, ValueError):
                pass

    def _read_stderr(self, pipe) -> None:
        try:
            while True:
                line = pipe.readline()
                if not line:
                    return
                self._handle_stderr(line.decode("utf-8", "replace"))
        except (OSError, ValueError):
            return
        finally:
            try:
                pipe.close()
            except (OSError, ValueError):
                pass

    def _handle_stderr(self, text: str) -> None:
        self._stderr_buffer += text
        while "\n" in self._stderr_buffer:
            line, self._stderr_buffer = self._stderr_buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            self._last_child_alive = time.monotonic()
            if line == "ALIVE":
                continue
            if line.startswith("STATE "):
                connected = line[6:].strip() == "connected"
                if connected != self._connected:
                    self._connected = connected
                    if connected:
                        self._no_data_deadline = time.monotonic() + 15.0
                    self.stateChanged.emit(
                        self.connected_text if connected else self.disconnected_text,
                        connected,
                    )
            elif line.startswith("MSG "):
                self.message.emit(line[4:])
            elif line.startswith("ADDR "):
                found = line[5:].strip()
                self.rtt_address = found
                if self.on_address_found is not None:
                    self.on_address_found(found)
            elif line.startswith("ERR "):
                payload = line[4:].strip()
                self._exit_message = payload
                now = time.monotonic()
                if payload != self._last_error or now - self._last_error_at >= 30.0:
                    self._last_error = payload
                    self._last_error_at = now
                    self.errorOccurred.emit(payload)
            else:
                self.message.emit(line)

    def _maybe_hint_no_data(self) -> None:
        """连接成功但目标长时间无 RTT 输出时，给出一次非故障提示。"""
        if (
            self._connected
            and not self._data_seen
            and not self._no_data_hinted
            and self._no_data_deadline > 0
            and time.monotonic() >= self._no_data_deadline
        ):
            self._no_data_hinted = True
            self.message.emit(
                "RTT 已连接但 15 秒未收到数据：目标可能当前没有新日志；"
                "启动日志通常需要复位或重新上电后才会输出"
            )
