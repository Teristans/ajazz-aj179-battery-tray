"""AJ179 APEX 电量托盘 — Windows 系统托盘电量显示 + 充电底座 OLED 时钟对时。

用法：
    pythonw aj179_tray.py        # 托盘常驻（无控制台窗口）
    python  aj179_tray.py --once # 打印一次 JSON（供脚本或排查用）

除厂商的状态轮询与时钟帧外不写设备，不改 DPI / 灯光 / 配对，不联网。
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
import time
import winreg
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import aj179_battery as core

APP_NAME = "AJ179 电量"
REFRESH_SECONDS = 60
CLOCK_SYNC_SECONDS = 30  # 底座会在几分钟内把时钟重置回 00:00，必须定时重发
SCRIPT = Path(__file__).resolve()
CONFIG_FILE = SCRIPT.parent / "config.json"
QUIT_FLAG = SCRIPT.parent / "quit.flag"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "AJ179BatteryTray"

DIGIT_FONTS = [r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\seguisb.ttf"]
CJK_FONTS = [r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc"]

COLOR_OK = (36, 168, 84)
COLOR_MID = (232, 146, 16)
COLOR_LOW = (218, 48, 48)
COLOR_CHARGE = (0, 118, 240)
COLOR_UNKNOWN = (140, 140, 140)


def _font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont:
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _level_color(percent: int) -> tuple[int, int, int]:
    if percent <= 20:
        return COLOR_LOW
    if percent <= 50:
        return COLOR_MID
    return COLOR_OK


def make_icon(percent: int | None, charging: bool = False) -> Image.Image:
    size = 32
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if charging and percent is not None:
        text, color = str(percent), COLOR_CHARGE
        font = _font(DIGIT_FONTS, 19 if percent >= 100 else 20)
    elif charging:
        text, color, font = "充", COLOR_CHARGE, _font(CJK_FONTS, 22)
    elif percent is None:
        text, color, font = "--", COLOR_UNKNOWN, _font(DIGIT_FONTS, 15)
    else:
        text = str(percent)
        color = _level_color(percent)
        font = _font(DIGIT_FONTS, 19 if percent >= 100 else 20)

    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    draw.text(
        ((size - (right - left)) / 2 - left, (size - (bottom - top)) / 2 - top),
        text,
        font=font,
        fill=color,
    )
    return img


def _detail_text(
    status: core.Status, last: core.Status | None, clock_line: str | None = None
) -> str:
    if status.state == core.STATE_WIRELESS:
        head = f"电量：{status.percent}%（放电中）"
    elif status.state == core.STATE_CHARGING:
        head = (
            f"电量：{status.percent}%（充电中）"
            if status.percent is not None
            else "电量：充电中（已接入 USB，该通道未提供百分比）"
        )
    elif status.state == core.STATE_SLEEPING:
        head = "电量：鼠标休眠或待机中，暂时读不到"
    else:
        head = "电量：未检测到鼠标（接收器未插或鼠标断电）"

    lines = [head]
    if status.product:
        lines.append(f"设备：{status.product}")
    lines.append(f"更新时间：{status.updated_at}")
    if clock_line:
        lines.append(clock_line)
    if status.raw:
        lines.append("")
        lines.append(f"原始帧：\n{status.raw.hex(' ')}")
    if last and last.percent is not None and status.percent is None:
        lines.append("")
        lines.append(f"上次读数：{last.percent}%（{last.updated_at}）")
    return "\n".join(lines)


def show_message(title: str, text: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)


def _startup_command() -> str:
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    return f'"{exe}" "{SCRIPT}"'


def load_config() -> dict:
    defaults = {"clock_sync": True}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            defaults.update(data)
    except (OSError, ValueError):
        pass
    return defaults


def save_config(config: dict) -> None:
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
            json.dump(config, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def startup_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE)
        return bool(value)
    except OSError:
        return False


def set_startup(enabled: bool) -> None:
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, _startup_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass


class Tray:
    def __init__(self) -> None:
        import pystray

        self.current: core.Status | None = None
        self.last: core.Status | None = None
        self._low_notified = False
        self._stop = threading.Event()

        self.config = load_config()
        self._clock_ok = False
        self._clock_at: float | None = None

        self.icon = pystray.Icon(
            "aj179_battery",
            make_icon(None),
            APP_NAME,
            menu=pystray.Menu(
                pystray.MenuItem("立即刷新", self.refresh, default=True),
                pystray.MenuItem("电量详情", self.show_detail),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    "底座显示时间",
                    self.toggle_clock,
                    checked=lambda item: bool(self.config.get("clock_sync")),
                ),
                pystray.MenuItem("立即同步时钟", self.sync_clock_now),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    "开机自动启动",
                    self.toggle_startup,
                    checked=lambda item: startup_enabled(),
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", self.quit),
            ),
        )

    # ---------- 数据 ----------

    def refresh(self, icon=None, item=None) -> None:
        status = core.read_status()
        previous_state = self.current.state if self.current else None
        self.current = status

        # 接收器刚回到在线（例如拔掉充电线）时立刻补一次对时，不等 30 秒定时器
        if status.state == core.STATE_WIRELESS and previous_state not in (None, core.STATE_WIRELESS):
            threading.Thread(target=self._sync_clock, daemon=True).start()

        if status.percent is not None:
            self.last = status
            if status.percent <= 20 and not self._low_notified:
                self._low_notified = True
                try:
                    self.icon.notify(f"鼠标电量仅剩 {status.percent}%，该充电了", APP_NAME)
                except Exception:
                    pass
            elif status.percent > 20:
                self._low_notified = False

        self._apply(status)

    def _apply(self, status: core.Status) -> None:
        if status.state == core.STATE_WIRELESS:
            tooltip = f"{APP_NAME}\n电量 {status.percent}%\n更新于 {status.updated_at}"
        elif status.state == core.STATE_CHARGING:
            if status.percent is not None:
                tooltip = f"{APP_NAME}\n充电中 · 电量 {status.percent}%\n更新于 {status.updated_at}"
            else:
                tooltip = f"{APP_NAME}\n充电中（已接入 USB）"
        elif status.state == core.STATE_SLEEPING:
            tooltip = f"{APP_NAME}\n鼠标休眠中"
            if self.last and self.last.percent is not None:
                tooltip += f"\n上次读数 {self.last.percent}%"
        else:
            tooltip = f"{APP_NAME}\n未检测到鼠标"

        try:
            self.icon.icon = make_icon(status.percent, status.charging)
            self.icon.title = tooltip[:127]
        except Exception:
            pass

    # ---------- 菜单 ----------

    def show_detail(self, icon=None, item=None) -> None:
        text = _detail_text(
            self.current or core.Status(core.STATE_ABSENT), self.last, self._clock_line()
        )
        threading.Thread(target=show_message, args=(APP_NAME, text), daemon=True).start()

    # ---------- 底座时钟 ----------

    def _clock_line(self) -> str:
        if not self.config.get("clock_sync"):
            return "底座时钟：已关闭（菜单「底座显示时间」可开启）"
        if self._clock_at is None:
            return "底座时钟：尚未同步"
        stamp = time.strftime("%H:%M:%S", time.localtime(self._clock_at))
        return f"底座时钟：{'已同步' if self._clock_ok else '同步失败（底座不在线）'}于 {stamp}"

    def sync_clock_now(self, icon=None, item=None) -> None:
        threading.Thread(target=self._sync_clock, daemon=True).start()

    def _sync_clock(self) -> None:
        if not self.config.get("clock_sync"):
            return
        ok = core.set_dock_clock()
        self._clock_ok = ok
        if ok:
            self._clock_at = time.time()

    def toggle_clock(self, icon=None, item=None) -> None:
        self.config["clock_sync"] = not self.config.get("clock_sync")
        save_config(self.config)
        if self.config["clock_sync"]:
            threading.Thread(target=self._sync_clock, daemon=True).start()
        try:
            self.icon.update_menu()
        except Exception:
            pass

    def toggle_startup(self, icon=None, item=None) -> None:
        try:
            if startup_enabled():
                set_startup(False)
            else:
                set_startup(True)
        except Exception as exc:
            threading.Thread(
                target=show_message, args=(APP_NAME, f"设置开机启动失败：{exc}"), daemon=True
            ).start()

    def quit(self, icon=None, item=None) -> None:
        self._stop.set()
        self.icon.stop()

    # ---------- 生命周期 ----------

    def _loop(self) -> None:
        while not self._stop.wait(REFRESH_SECONDS):
            self.refresh()

    def _clock_loop(self) -> None:
        # 启动后先等 3 秒：固件上电约 2 秒后才接受时钟帧，太早发会被静默丢弃
        if self._stop.wait(3):
            return
        while True:
            self._sync_clock()
            if self._stop.wait(CLOCK_SYNC_SECONDS):
                return

    def _watch_quit(self) -> None:
        """支持 `--quit` 优雅退出（进程被系统拒绝直接结束时的备用通道）。"""
        while not self._stop.wait(1):
            if QUIT_FLAG.exists():
                try:
                    QUIT_FLAG.unlink()
                except OSError:
                    pass
                self.quit()
                return

    def run(self) -> None:
        threading.Thread(target=self.refresh, daemon=True).start()
        threading.Thread(target=self._loop, daemon=True).start()
        threading.Thread(target=self._clock_loop, daemon=True).start()
        threading.Thread(target=self._watch_quit, daemon=True).start()
        self.icon.run()


def single_instance() -> bool:
    """命名互斥体，保证只有一个托盘实例。"""
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, "Local\\AJ179BatteryTray")
    if not handle:
        return True
    return kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def main() -> int:
    if "--quit" in sys.argv:
        QUIT_FLAG.write_text("1", encoding="utf-8")
        # 等运行中的实例自己退出（最多 3 秒）
        for _ in range(30):
            time.sleep(0.1)
            if not QUIT_FLAG.exists():
                print("已退出")
                return 0
        QUIT_FLAG.unlink(missing_ok=True)
        print("没有响应退出请求（可能没有实例在运行，或版本较旧）")
        return 1

    if "--sync-clock" in sys.argv:
        ok = core.set_dock_clock()
        print(json.dumps({"clock_synced": ok, "at": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False))
        return 0 if ok else 1

    if "--once" in sys.argv:
        status = core.read_status()
        clock_ok = core.set_dock_clock()
        print(
            json.dumps(
                {
                    "ok": status.state == core.STATE_WIRELESS,
                    "state": status.state,
                    "product": status.product,
                    "percent": status.percent,
                    "charging": status.charging,
                    "updated_at": status.updated_at,
                    "clock_synced": clock_ok,
                    "raw": status.raw.hex(" ") if status.raw else None,
                },
                ensure_ascii=False,
            )
        )
        return 0 if status.state != core.STATE_ABSENT else 1

    if not single_instance():
        return 0

    try:
        Tray().run()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
