"""日志记录器，负责将接收到的数据写入文件。"""

from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path

from utils.timestamp import timestamp_text
from utils.ansi_helper import ESCAPE_RE

from .config import DEFAULT_LOG_PATH

_SAFE_NAME = re.compile(r"[^\w\u4e00-\u9fff.-]+")


def decode_payload(data: bytes) -> str:
    """尽力把设备输出解码成文本。"""
    for encoding in ("utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


class SessionLogger:
    """把接收到的数据自动落盘。"""

    def __init__(
        self,
        session_name: str,
        log_path: str,
        timestamps: bool = True,
        tag: str = "",
    ):
        self.session_name = session_name
        self.log_path = log_path or DEFAULT_LOG_PATH
        self.timestamps = timestamps
        self.tag = tag
        self.active = False
        self.file_path: Path | None = None
        self._handle = None
        self._last_flush = 0.0
        self._unflushed = 0
        self._pending_text = ""
        self.last_error = ""

    # ------------------------------------------------------------------
    def _target_dir(self) -> Path:
        directory = Path(self.log_path).expanduser()
        try:
            directory.mkdir(parents=True, exist_ok=True)
            return directory
        except OSError:
            pass

        fallback = Path(DEFAULT_LOG_PATH).expanduser()
        try:
            fallback.mkdir(parents=True, exist_ok=True)
            self.last_error = f"路径未找到，使用默认路径：{fallback}"
            return fallback
        except OSError as exc:
            self.last_error = f"无法创建日志目录：{exc}"
            return Path.cwd()

    def start(self) -> str:
        if self.active:
            # 重复启动时先关掉上一个文件，避免句柄泄漏
            self.stop()
        self.last_error = ""
        directory = self._target_dir()
        safe_name = _SAFE_NAME.sub("_", self.session_name).strip("_") or "session"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # 同名会话在同一秒启动时不要写进同一个文件
        self.file_path = directory / f"{safe_name}{self.tag}_{stamp}.log"
        index = 2
        while self.file_path.exists() and index <= 99:
            self.file_path = directory / f"{safe_name}{self.tag}_{stamp}_{index}.log"
            index += 1
        try:
            self._handle = open(self.file_path, "a", encoding="utf-8", errors="replace")
        except OSError as exc:
            self.active = False
            message = f"启动日志记录失败：{exc}"
            self.last_error = message
            return message

        self.active = True
        self._last_flush = time.monotonic()
        self._unflushed = 0
        self._pending_text = ""
        message = f"正在记录到 {self.file_path}"
        if self.last_error:
            message = f"{message}（{self.last_error}）"
        return message

    def write(self, data: bytes) -> None:
        if not self.active or self._handle is None:
            return
        # 数据是按分片到达的，先攒够整行再写，避免把一行拆成两行
        self._pending_text += self._clean_text(decode_payload(data))
        if "\n" in self._pending_text:
            parts = self._pending_text.split("\n")
            self._pending_text = parts.pop()
            self._write_lines(parts)
        if len(self._pending_text) > 64 * 1024:
            self._write_lines([self._pending_text])
            self._pending_text = ""

    def _write_lines(self, lines: list[str]) -> None:
        if self._handle is None or not lines:
            return
        try:
            if self.timestamps:
                for line in lines:
                    self._handle.write(f"{timestamp_text()} {line.rstrip(chr(13))}\n")
            else:
                for line in lines:
                    self._handle.write(f"{line.rstrip(chr(13))}\n")
            # 按块或按秒刷盘，避免高频写入阻塞界面线程
            self._unflushed += sum(len(line) + 1 for line in lines)
            now = time.monotonic()
            if self._unflushed >= 64 * 1024 or now - self._last_flush >= 1.0:
                self._handle.flush()
                self._unflushed = 0
                self._last_flush = now
        except OSError as exc:
            self.last_error = f"日志写入失败：{exc}"
            self.stop()

    @staticmethod
    def _clean_text(text: str) -> str:
        """落盘保存为可读纯文本：去掉 ANSI 序列与不可见控制字符。"""
        text = ESCAPE_RE.sub("", text)
        return "".join(
            char
            for char in text
            if char == "\n" or char == "\r" or char == "\t" or ord(char) >= 32
        )

    def stop(self) -> str:
        if self._handle is not None and self._pending_text:
            self._write_lines([self._pending_text])
            self._pending_text = ""
        if self._handle is not None:
            try:
                self._handle.flush()
                self._handle.close()
            except OSError:
                pass
            self._handle = None
        if self.active:
            self.active = False
            return "日志记录已停止"
        return ""
