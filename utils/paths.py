"""路径解析：区分「程序目录」与「打包内资源目录」。

源码运行时两者都是项目根目录；PyInstaller 打包后：
- 程序目录 = exe 所在目录（config.json、日志放这里，方便用户改和备份）
- 资源目录 = 解包目录 _MEIPASS（图标等只读资源）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """可写目录：配置、日志等放在这里。"""
    if is_frozen():
        exe_dir = Path(sys.executable).resolve().parent
        if _is_writable(exe_dir):
            return exe_dir
        return _local_app_dir()
    return Path(__file__).resolve().parent.parent


_WRITABLE_CACHE: dict[Path, bool] = {}


def _is_writable(path: Path) -> bool:
    cached = _WRITABLE_CACHE.get(path)
    if cached is not None:
        return cached
    probe = path / ".tracehub-write-probe"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"")
        probe.unlink(missing_ok=True)
        result = True
    except OSError:
        result = False
    _WRITABLE_CACHE[path] = result
    return result


def _local_app_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base).expanduser() if base else Path.home() / ".tracehub"
    directory = root / "TraceHub"
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return directory


def resource_dir() -> Path:
    """只读资源目录。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base)
    return Path(__file__).resolve().parent.parent


def assets_dir() -> Path:
    return resource_dir() / "assets"


def config_path() -> Path:
    return app_dir() / "config.json"
