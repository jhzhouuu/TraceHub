"""串口数据接收线程。"""

from __future__ import annotations

import serial
from serial.tools import list_ports

from .interfaces import BaseWorker


def available_ports() -> list[tuple[str, str]]:
    """扫描已连接的串口，返回 (端口, 描述) 列表。"""
    ports = []
    for info in list_ports.comports():
        description = info.description or ""
        ports.append((info.device, description))
    return sorted(ports, key=lambda item: item[0])


class SerialWorker(BaseWorker):
    """串口采集线程：打开端口、持续读取、断线后重连。"""

    connect_failure_text = "串口错误"
    retry_enabled = False  # 串口按需求不自动重连

    def __init__(self, port: str, baudrate: int = 115200, parent=None):
        super().__init__(parent)
        self.port = port
        self.baudrate = int(baudrate)
        self._serial: serial.Serial | None = None

    # ------------------------------------------------------------------
    def _connect(self) -> None:
        if not self.port:
            raise serial.SerialException("未选择串口")
        self.message.emit(f"尝试打开指定端口 {self.port}")
        # serial_for_url 对普通串口与 loop:// 之类的测试端口都适用
        self._serial = serial.serial_for_url(
            self.port,
            baudrate=self.baudrate,
            timeout=0.05,
            write_timeout=1.0,
        )
        self._last_heartbeat = 0.0
        self.message.emit(f"已连接到 {self.port} @ {self.baudrate}")

    def _read_loop(self) -> None:
        self.message.emit("进入数据读取主循环")
        while self._running:
            if self._serial is None:
                raise serial.SerialException("串口不可用")
            self._drain_send_queue()
            try:
                pending = self._serial.in_waiting
                data = self._serial.read(pending or 1)
            except (OSError, serial.SerialException) as exc:
                raise serial.SerialException(f"检测到连接断开：{exc}") from exc

            if data:
                self.dataReceived.emit(bytes(data))

            if self.heartbeat_due() and not self._serial.is_open:
                raise serial.SerialException("检测到连接断开")

    def _disconnect(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except (OSError, serial.SerialException):
                pass
            self._serial = None
            self.message.emit("串口已关闭")

    def _write(self, data: bytes) -> None:
        if self._serial is None or not self._serial.is_open:
            raise serial.SerialException("串口未打开")
        self._serial.write(data)
        self._serial.flush()
