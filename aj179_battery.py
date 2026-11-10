"""AJAZZ 无线鼠标电量读取（纯 HID）。

协议（本机实测 + 厂商逆向文档，AJ179 APEX / SONiX 0x3151 方案）：
  1. 厂商私有控制集合 usage_page=0xFFFF, usage=0x02 上 SET_FEATURE 一个
     “状态轮询”帧：report id 0x00，opcode 0xF7，其余全 0，无载荷无校验。
     这就是厂商驱动每秒发一次的心跳，作用是拉起 2.4G 状态回传链路；
     不发它，状态页永远是全零。
  2. 等约 50ms，GET_FEATURE report 0x05：
     有效帧 = report id 后、电量字节前的字节必须为 0；
     Windows 下电量在 byte3（0x64 = 100%）。

设备形态（插拔实测）：
  无线 2.4G 接收器 -> 3151:5007 "AJAZZ 2.4G 8K"，可读电量
  插线 / 充电       -> 3151:4026 "AJAZZ AJ179 APEX"，改走有线通道，
                       优先尝试读电量，读不到则只判定“充电中”，不编造数字。

底座 OLED 时钟（opcode 0x28 SET_OLEDCLOCK）：
  同一条控制集合，64 字节无校验帧：
    帧[0]=0x00 report id, 帧[1]=0x28 命令, 帧[2..7]=0,
    帧[8]=0xD7 必需标记（缺了固件静默丢弃整包）,
    帧[9..10]=年（大端）, 帧[11]=月, 帧[12]=日, 帧[13]=时, 帧[14]=分, 帧[15]=秒。
  固件只在启动约 2 秒后接受该帧，且会在几分钟内把时钟重置回 00:00，
  所以必须定时重发（业界实现均取 30 秒）。

除上面两个无载荷/定时命令外，不发送任何写命令，不触碰 DPI / 灯光 / 配对。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import hid

VENDOR_ID = 0x3151
VENDOR_USAGE_PAGE = 0xFFFF
VENDOR_USAGE = 0x02
STATUS_REPORT_ID = 0x05
POLL_REPORT_ID = 0x00
POLL_OPCODE = 0xF7
REPORT_LEN = 65  # report id + 64 bytes payload
SETTLE_SECONDS = 0.05

CLOCK_COMMAND = 0x28
CLOCK_MARKER = 0xD7

# 同一控制集合上的读写必须串行，避免电量和时钟两路命令交错
_IO_LOCK = threading.Lock()

WIRELESS_PIDS = {0x5007, 0x4027, 0x5C2F}
WIRED_PIDS = {0x4026, 0x4006, 0x5008}

STATE_WIRELESS = "wireless"
STATE_CHARGING = "charging"
STATE_SLEEPING = "sleeping"
STATE_ABSENT = "absent"


@dataclass
class Status:
    state: str
    percent: int | None = None
    raw: bytes | None = None
    product: str | None = None
    path: str | None = None
    timestamp: float = field(default_factory=time.time)

    @property
    def charging(self) -> bool:
        return self.state == STATE_CHARGING

    @property
    def updated_at(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.timestamp))


def _path_str(path) -> str:
    return path.decode(errors="replace") if isinstance(path, bytes) else str(path)


def _group_devices() -> dict[int, dict]:
    """按 PID 归并当前在线的 AJAZZ HID 设备，并挑出可用的厂商控制集合。"""
    devices: dict[int, dict] = {}
    for d in hid.enumerate(VENDOR_ID, 0):
        pid = d["product_id"]
        entry = devices.setdefault(
            pid, {"product": d.get("product_string") or "", "vendor": None}
        )
        if d.get("usage_page") != VENDOR_USAGE_PAGE:
            continue
        # 优先 usage=0x02 的控制集合：只有它能读出状态帧
        if d.get("usage") == VENDOR_USAGE:
            entry["vendor"] = d
        elif entry["vendor"] is None or entry["vendor"].get("usage") != VENDOR_USAGE:
            entry["vendor"] = d
    return devices


def _poll_status_link(handle) -> bool:
    """发送厂商状态轮询（无载荷），拉起 2.4G 电量回传链路。"""
    frame = bytearray(REPORT_LEN)
    frame[0] = POLL_REPORT_ID
    frame[1] = POLL_OPCODE
    try:
        handle.send_feature_report(bytes(frame))
        return True
    except OSError:
        return False


def _parse_frame(data: bytes) -> int | None:
    """解析 0x05 帧，返回电量；帧无效或链路未就绪返回 None。"""
    if len(data) < 4:
        return None
    charge = 3 if data[0] == STATUS_REPORT_ID else 2
    # 有效帧：report id 与电量之间必须全 0（重连瞬间会返回脏数据）
    if any(data[1:charge]):
        return None
    percent = data[charge]
    if percent == 0 or percent > 100:
        return None  # 0 = 链路尚未就绪，不能当作 0%
    return percent


def _read_once(collection: dict) -> tuple[int, bytes] | None:
    with _IO_LOCK:
        handle = hid.device()
        try:
            handle.open_path(collection["path"])
        except OSError:
            return None
        try:
            _poll_status_link(handle)
            time.sleep(SETTLE_SECONDS)
            data = bytes(handle.get_feature_report(STATUS_REPORT_ID, REPORT_LEN))
        except OSError:
            return None
        finally:
            try:
                handle.close()
            except Exception:
                pass

    percent = _parse_frame(data)
    return None if percent is None else (percent, data)


# ---------- 底座 OLED 时钟 ----------


def build_clock_frame(when: time.struct_time | None = None) -> bytes:
    """构造 SET_OLEDCLOCK 帧（65 字节：report id + 64）。"""
    t = when or time.localtime()
    frame = bytearray(REPORT_LEN)
    frame[0] = 0x00  # report id（该接口不声明 report id，必须为 0）
    frame[1] = CLOCK_COMMAND  # payload[0] = 0x28
    frame[8] = CLOCK_MARKER  # payload[7] = 0xD7，缺了固件静默丢弃
    frame[9] = (t.tm_year >> 8) & 0xFF  # 年，大端
    frame[10] = t.tm_year & 0xFF
    frame[11] = t.tm_mon
    frame[12] = t.tm_mday
    frame[13] = t.tm_hour
    frame[14] = t.tm_min
    frame[15] = t.tm_sec
    return bytes(frame)


def _find_dock_collection() -> dict | None:
    """底座 = 2.4G 接收器；只它有 OLED 时钟。"""
    for pid, info in _group_devices().items():
        if info["vendor"] is not None and (
            pid in WIRELESS_PIDS or "2.4G" in info["product"].upper()
        ):
            return info["vendor"]
    return None


def set_dock_clock(when: time.struct_time | None = None) -> bool:
    """把当前时间推到底座 OLED。设备不在线或发送失败返回 False。"""
    collection = _find_dock_collection()
    if collection is None:
        return False

    with _IO_LOCK:
        handle = hid.device()
        try:
            handle.open_path(collection["path"])
        except OSError:
            return False
        try:
            # 固件上电后约 2 秒才开始接受该帧，太早发会被静默丢弃
            time.sleep(0.05)
            handle.send_feature_report(build_clock_frame(when))
            return True
        except OSError:
            return False
        finally:
            try:
                handle.close()
            except Exception:
                pass


def read_status(attempts: int = 2) -> Status:
    devices = _group_devices()
    if not devices:
        return Status(STATE_ABSENT)

    # 1) 无线接收器：可读真实百分比
    for pid, info in devices.items():
        if pid in WIRELESS_PIDS or "2.4G" in info["product"].upper():
            if info["vendor"] is None:
                continue
            for _ in range(attempts):
                result = _read_once(info["vendor"])
                if result:
                    percent, raw = result
                    return Status(
                        STATE_WIRELESS,
                        percent=percent,
                        raw=raw,
                        product=info["product"],
                        path=_path_str(info["vendor"]["path"]),
                    )
                time.sleep(0.1)
            return Status(STATE_SLEEPING, product=info["product"])

    # 2) 有线 / 插线充电：优先读电量，读不到就只报充电状态
    for pid, info in devices.items():
        if pid in WIRED_PIDS:
            percent = raw = None
            if info["vendor"] is not None:
                result = _read_once(info["vendor"])
                if result:
                    percent, raw = result
            return Status(
                STATE_CHARGING,
                percent=percent,
                raw=raw,
                product=info["product"],
            )

    # 3) 设备在但读不到
    for info in devices.values():
        if info["vendor"] is not None:
            return Status(STATE_SLEEPING, product=info["product"])
    return Status(STATE_SLEEPING)


if __name__ == "__main__":
    status = read_status()
    print(f"状态: {status.state}")
    if status.percent is not None:
        print(f"电量: {status.percent}%")
    if status.product:
        print(f"设备: {status.product}")
    if status.raw:
        print(f"原始帧: {status.raw.hex(' ')}")
