"""配置文件的读写与默认值。"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from utils.paths import config_path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = config_path()
DEFAULT_LOG_PATH = str(Path.home() / "tracehub-logs")

SESSION_TYPES = ("serial", "jlink")

BAUDRATES = [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600]

DEVICE_PRESETS = [
    "nRF52840_xxAA",
    "nRF52833_xxAA",
    "nRF52832_xxAA",
    "nRF52811_xxAA",
    "nRF52810_xxAA",
    "nRF51822_xxAA",
    "STM32F103C8",
    "STM32F407VG",
    "Cortex-M4",
    "Cortex-M0",
]

JLINK_INTERFACES = ["SWD", "JTAG"]


def default_serial_config() -> dict:
    return {"port": "", "baudrate": 115200}


def default_jlink_config() -> dict:
    return {
        "device_name": "nRF52840_xxAA",
        "interface": "SWD",
        "speed": 4000,
        "serial_number": "",
    }


def default_display() -> dict:
    return {"hex": False, "timestamp": True, "max_lines": 2000, "autosave": True}


def new_session(session_type: str = "serial", name: str = "") -> dict:
    """构造一个会话配置，字段与旧版工具保持一致。"""
    if session_type not in SESSION_TYPES:
        session_type = "serial"
    return {
        "id": str(uuid.uuid4()),
        "type": session_type,
        "name": name or ("串口" if session_type == "serial" else "J-Link"),
        "serial_config": default_serial_config(),
        "jlink_config": default_jlink_config(),
        "display": default_display(),
    }


def normalize_session(raw: dict) -> dict:
    """补全缺失字段，兼容旧版本写出的配置。

    未知字段会原样保留，避免读到新版本写出的配置后再保存时把字段丢掉。
    """
    session_type = raw.get("type")
    if session_type not in SESSION_TYPES:
        session_type = "serial"

    session = dict(raw)
    session.update(new_session(session_type, str(raw.get("name") or "")))
    session["id"] = str(raw.get("id") or session["id"])

    # 子字典也保留未知字段（例如 J-Link 的 rtt_address）
    serial_cfg = dict(default_serial_config())
    serial_cfg.update(raw.get("serial_config") or {})
    serial_cfg["port"] = str(serial_cfg.get("port", "") or "")
    serial_cfg["baudrate"] = int(serial_cfg.get("baudrate", 115200) or 115200)
    session["serial_config"] = serial_cfg

    jlink_cfg = dict(default_jlink_config())
    jlink_cfg.update(raw.get("jlink_config") or {})
    jlink_cfg["device_name"] = str(jlink_cfg.get("device_name") or "nRF52840_xxAA")
    jlink_cfg["interface"] = str(jlink_cfg.get("interface") or "SWD")
    jlink_cfg["speed"] = int(jlink_cfg.get("speed", 4000) or 4000)
    jlink_cfg["serial_number"] = str(jlink_cfg.get("serial_number", "") or "")
    session["jlink_config"] = jlink_cfg

    display = dict(default_display())
    display.update(raw.get("display") or {})
    display["hex"] = bool(display.get("hex", False))
    display["timestamp"] = bool(display.get("timestamp", True))
    display["max_lines"] = int(display.get("max_lines", 20000) or 20000)
    display["autosave"] = bool(display.get("autosave", True))
    session["display"] = display
    return session


class ConfigStore:
    """config.json 的读写入口。"""

    def __init__(self, path: Path | str = CONFIG_FILE):
        self.path = Path(path)
        self.sessions: list[dict] = []
        self.log_path: str = DEFAULT_LOG_PATH
        self._extra: dict = {}
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        raw_sessions = data.get("sessions")
        if isinstance(raw_sessions, list):
            self.sessions = [normalize_session(item) for item in raw_sessions if isinstance(item, dict)]
        self.log_path = str(data.get("log_path") or DEFAULT_LOG_PATH)
        self._extra = {
            key: value for key, value in data.items() if key not in ("sessions", "log_path")
        }

    def save(self) -> bool:
        payload = {"sessions": self.sessions, "log_path": self.log_path, **self._extra}
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # 先写临时文件再原子替换，避免断电或崩溃把配置写坏
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=4, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        except OSError:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            return False
        return True

    def add_session(self, session_type: str = "serial", name: str = "") -> dict:
        session = new_session(session_type, name)
        self.sessions.append(session)
        return session

    def remove_session(self, session_id: str) -> None:
        self.sessions = [item for item in self.sessions if item.get("id") != session_id]

    def find(self, session_id: str) -> dict | None:
        for item in self.sessions:
            if item.get("id") == session_id:
                return item
        return None
