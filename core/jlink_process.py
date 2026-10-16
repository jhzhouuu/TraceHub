"""J-Link 采集子进程。

SEGGER 的 JLinkARM.dll 在一个进程里只维护一个 USB 连接上下文（打开一块探针后
再选另一块会返回 -1），而且它的 API 是「当前连接」全局状态。所以每个 J-Link 会话
单独起一个进程，各持有自己的连接，互不干扰。

对外协议：
  stdout  原始 RTT 字节流
  stderr  行文本控制消息：MSG / ADDR / ERR / STATE / ALIVE
  stdin   命令：WRITE <hex> 表示往 RTT 写数据
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time

RTT_SIGNATURE = b"SEGGER RTT"
RTT_SCAN_START = 0x20000000
RTT_SCAN_END = 0x20080000  # 512KB，覆盖常见 Cortex-M 的 SRAM
RTT_SCAN_STEP = 0x1000

READ_INTERVAL = 0.02
HEARTBEAT_INTERVAL = 0.5
TARGET_ACCESS_RETRY_DELAY = 0.1
TARGET_LOSS_CONFIRM_SECONDS = 1.0
RAM_FALLBACK_ADDRESS = RTT_SCAN_START
WRITE_BUFFER = 0
RETRY_SECONDS = 2.0
_PROTOCOL_ENABLED = False


def enable_protocol() -> None:
    """只有真正的采集子进程才向父进程输出协议消息。"""
    global _PROTOCOL_ENABLED
    _PROTOCOL_ENABLED = True


# ----------------------------------------------------------------------
# 与父进程通信（打包成窗口程序后 sys.stdout 可能是 None，所以直接写 fd）
# ----------------------------------------------------------------------
def _write(fd: int, payload: bytes) -> None:
    try:
        os.write(fd, payload)
    except OSError:
        pass


def _write_protocol(fd: int, payload: bytes) -> None:
    if _PROTOCOL_ENABLED:
        _write(fd, payload)


def message(text: str) -> None:
    _write_protocol(2, f"MSG {text}\n".encode("utf-8", "replace"))


def address_found(text: str) -> None:
    _write_protocol(2, f"ADDR {text}\n".encode("utf-8", "replace"))


def error(text: str) -> None:
    _write_protocol(2, f"ERR {text}\n".encode("utf-8", "replace"))


def state(text: str) -> None:
    """告诉父进程连接状态（connected / disconnected）。"""
    _write_protocol(2, f"STATE {text}\n".encode("utf-8", "replace"))


def alive() -> None:
    """告诉父进程采集循环仍在执行（不显示给用户）。"""
    _write_protocol(2, b"ALIVE\n")


# ----------------------------------------------------------------------
# RTT
# ----------------------------------------------------------------------
def find_rtt_control_block(jlink) -> int | None:
    """自动搜索失败时，按签名扫一遍 SRAM 找控制块地址。"""
    for base in range(RTT_SCAN_START, RTT_SCAN_END, RTT_SCAN_STEP):
        alive()
        try:
            chunk = bytes(jlink.memory_read8(base, RTT_SCAN_STEP))
        except Exception:  # noqa: BLE001 - 该段不可读就跳过
            continue
        position = chunk.find(RTT_SIGNATURE)
        if position >= 0:
            return base + position
    return None


def wait_rtt_ready(jlink, timeout: float = 0.8) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        alive()
        try:
            jlink.rtt_get_num_up_buffers()
            return True
        except Exception:  # noqa: BLE001 - 没找到时 DLL 会抛异常
            time.sleep(0.05)
    return False


def start_rtt(jlink, rtt_address: str) -> int | None:
    """启动 RTT，返回控制块地址（用于后续主动检查目标访问）。"""
    if rtt_address:
        try:
            jlink.rtt_start(block_address=int(rtt_address, 0))
            if wait_rtt_ready(jlink):
                message(f"RTT 已启动（指定地址 {rtt_address}）")
                return int(rtt_address, 0)
            message(f"指定地址 {rtt_address} 未找到控制块，改为自动搜索")
        except Exception as exc:  # noqa: BLE001
            message(f"按指定地址启动 RTT 失败：{exc}")

    try:
        jlink.rtt_start()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"RTT 启动失败：{exc}") from exc
    if wait_rtt_ready(jlink):
        # DLL 能自动找到 RTT，但不暴露控制块地址；
        # 这里再按签名扫一次，用于后续主动检查目标访问。
        address = find_rtt_control_block(jlink)
        if address is not None:
            address_found(hex(address))
            return address
        return None

    address = find_rtt_control_block(jlink)
    if address is None:
        raise RuntimeError(
            "目标里没有找到 RTT 控制块：请确认固件启用了 SEGGER RTT"
            "（调用 SEGGER_RTT_Init 或 SEGGER_RTT_printf）"
        )
    try:
        jlink.rtt_start(block_address=address)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"用扫描到的地址启动 RTT 失败：{exc}") from exc

    found = hex(address)
    message(f"自动定位到 RTT 控制块：{found}")
    address_found(found)
    if not wait_rtt_ready(jlink):
        message("RTT 控制块已定位，缓冲区尚未就绪")
    return address


# ----------------------------------------------------------------------
# 命令通道（父进程往 RTT 写数据用）
# ----------------------------------------------------------------------
class StopFlag:
    """父进程消失时置位，让主循环退出（避免留下占着探针的孤儿进程）。"""

    def __init__(self) -> None:
        self.value = False


def _start_command_reader(commands: "queue.Queue[bytes]", stop: StopFlag) -> None:
    """后台读 stdin 的命令行；所有 DLL 调用仍留在主循环里做。"""

    def _run() -> None:
        buffer = b""
        while True:
            try:
                chunk = os.read(0, 4096)
            except OSError:
                stop.value = True
                return
            if not chunk:
                # stdin 关闭 = 父进程已退出
                stop.value = True
                return
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                commands.put(line.strip())

    threading.Thread(target=_run, daemon=True).start()


def _sleep(seconds: float, stop: StopFlag) -> None:
    deadline = time.monotonic() + seconds
    while not stop.value and time.monotonic() < deadline:
        time.sleep(0.05)


def _drain_commands(jlink, commands: "queue.Queue[bytes]") -> None:
    while True:
        try:
            line = commands.get_nowait()
        except queue.Empty:
            return
        if not line or not line.startswith(b"WRITE "):
            continue
        try:
            payload = bytes.fromhex(line[6:].decode("ascii"))
        except ValueError:
            message("发送数据格式不正确")
            continue
        try:
            jlink.rtt_write(WRITE_BUFFER, list(payload))
        except Exception as exc:  # noqa: BLE001
            message(f"写入错误：{exc}")


# ----------------------------------------------------------------------
class TargetAccessWatch:
    """用 CPU ID、目标 RAM 和 RTT 数据共同判断连接是否真实存活。

    RTT 空读不能区分「设备安静」和「目标断电」，所以平时不依赖日志输出。
    设备从低功耗唤醒时，SWD 可能短暂不可读但 RTT 随后有数据；这类数据
    会撤销失败状态。只有 CPU ID 和 RAM 都持续不可读且没有任何 RTT 数据，
    才判定断电。
    """

    def __init__(self, block_address: int | None) -> None:
        self.address = int(block_address) if block_address is not None else RAM_FALLBACK_ADDRESS
        self.first_failure_at: float | None = None
        self.last_data_at: float | None = None

    def mark_data_seen(self) -> None:
        """RTT 有数据是目标存活的直接证据，优先于健康检查失败。"""
        self.last_data_at = time.monotonic()
        self.first_failure_at = None

    def data_seen_recently(self, window: float = TARGET_LOSS_CONFIRM_SECONDS) -> bool:
        if self.last_data_at is None:
            return False
        return time.monotonic() - self.last_data_at < window

    def read_once(self, jlink) -> bool:
        try:
            jlink.core_id()
        except Exception:  # noqa: BLE001 - 目标断电时 DLL 会抛异常
            pass
        else:
            return True

        try:
            values = jlink.memory_read32(self.address, 1)
        except Exception:  # noqa: BLE001 - 目标不可访问时 DLL 会抛异常
            return False
        return len(values) == 1

    def check(self, jlink) -> None:
        if self.read_once(jlink):
            self.first_failure_at = None
            return

        now = time.monotonic()
        if self.first_failure_at is None:
            self.first_failure_at = now

        time.sleep(TARGET_ACCESS_RETRY_DELAY)
        if self.read_once(jlink):
            self.first_failure_at = None
            return

        if time.monotonic() - self.first_failure_at >= TARGET_LOSS_CONFIRM_SECONDS:
            raise RuntimeError("目标无响应，已断开")


def _connect_once(pylink, device: str, interface: str, speed, serial, rtt_address: str):
    """连接目标并启动 RTT，返回 (JLink, 控制块地址或 None, 健康检查器)。"""
    jlink = pylink.JLink()
    serial_no = int(serial) if str(serial).strip().isdigit() else None
    alive()
    jlink.open(serial_no=serial_no)
    interfaces = {
        "SWD": pylink.enums.JLinkInterfaces.SWD,
        "JTAG": pylink.enums.JLinkInterfaces.JTAG,
    }
    jlink.set_tif(interfaces.get(str(interface).upper(), pylink.enums.JLinkInterfaces.SWD))
    message(f"连接目标 {device}")
    alive()
    jlink.connect(device, speed=int(speed))
    # 目标可能已经在跑，启动日志只会在复位后出现；
    # 连接成功后主动复位一次，避免用户必须拔插 J-Link 才能看到日志。
    alive()
    jlink.reset(0, halt=False)
    alive()
    block = start_rtt(jlink, rtt_address)
    return jlink, block, TargetAccessWatch(block)


def _stream(
    jlink,
    commands: "queue.Queue[bytes]",
    stop: StopFlag,
    block_address: int | None = None,
    health_watch: TargetAccessWatch | None = None,
) -> None:
    last_heartbeat = time.monotonic()
    while not stop.value:
        _drain_commands(jlink, commands)
        chunk = jlink.rtt_read(WRITE_BUFFER, 4096)
        if chunk:
            _write_protocol(1, bytes(chunk))
            if health_watch is not None:
                health_watch.mark_data_seen()
        if time.monotonic() - last_heartbeat >= HEARTBEAT_INTERVAL:
            last_heartbeat = time.monotonic()
            if not jlink.target_connected() and not (
                health_watch is not None and health_watch.data_seen_recently()
            ):
                raise RuntimeError("目标已断开")
            alive()
            if health_watch is not None:
                health_watch.check(jlink)
        time.sleep(READ_INTERVAL)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 5:
        error("子进程参数不正确")
        return 2
    device, interface, speed, serial, rtt_address = args

    try:
        import pylink
    except ImportError:
        error("请检查是否已安装 J-Link 驱动")
        return 3

    commands: "queue.Queue[bytes]" = queue.Queue()
    stop = StopFlag()
    _start_command_reader(commands, stop)

    if str(serial).strip().isdigit():
        message(f"尝试启动 J-Link（SN {serial}）")
    else:
        message("尝试启动 J-Link")

    attempt = 0
    last_error = ""
    consecutive_failures = 0
    while not stop.value:
        attempt += 1
        alive()
        try:
            jlink, block_address, health_watch = _connect_once(
                pylink, device, interface, speed, serial, rtt_address
            )
        except Exception as exc:  # noqa: BLE001
            text = str(exc)
            # 同样的错误不要每 2 秒刷一次；变化时或每 30 秒提醒一次
            if text != last_error or attempt % 15 == 1:
                error(text)
                last_error = text
            state("disconnected")
            consecutive_failures += 1
            # 连续失败时退出，父进程会拉起全新子进程，
            # 避免 JLinkARM.dll 卡在 USB 掉线后的坏状态里。
            # 父线程会每 2 秒无限重启子进程，因此这里不会停止整体重连。
            if consecutive_failures >= 3:
                break
            _sleep(RETRY_SECONDS, stop)
            alive()
            continue

        last_error = ""
        consecutive_failures = 0
        message(f"已启动 RTT：{device} @ {speed} kHz")
        state("connected")
        try:
            _stream(jlink, commands, stop, block_address, health_watch)
        except Exception as exc:  # noqa: BLE001
            error(f"检测到连接断开：{exc}")
        finally:
            for cleanup in (jlink.rtt_stop, jlink.close):
                try:
                    cleanup()
                except Exception:  # noqa: BLE001
                    pass
        state("disconnected")
        if stop.value:
            break
        # 断开后不在同一个进程里继续用 JLinkARM.dll 重试；
        # DLL 在 USB/目标掉线后容易留下坏状态，
        # 子进程退出、父进程拉起全新进程，重连才可靠。
        break

    return 0


if __name__ == "__main__":
    enable_protocol()
    raise SystemExit(main())
