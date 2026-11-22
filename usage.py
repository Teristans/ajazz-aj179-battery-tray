"""Track how long the mouse has been in use since its last charge.

Only time spent with a live wireless link is counted, so a powered-off mouse,
a sleeping machine, or a charging session never inflates the figure.  Sampling
gaps longer than MAX_GAP_SECONDS are discarded (the app was not running, or the
PC was suspended), which is why a shutdown does not add hours.

Two timestamps are kept, deliberately separate:

  ``since``       the start of the current counting cycle.  Always present once
                  counting begins, but it says nothing about when charging
                  finished — on a cold start it is simply "now".

  ``charge_end``  the completion of the last charge, and only recorded when the
                  app actually witnessed one.  ``charge_end_exact`` tells
                  whether it was observed (charging -> wireless) or inferred
                  from a percentage rebound, because an inferred stamp is only
                  accurate to "sometime before this sample".

Three things start a new cycle: the very first wireless sample (baseline), the
link coming back after a charge (charging -> wireless), and a percentage jump of
RESET_JUMP points or more while the mouse is wireless (a charge that happened
while the app was not running).

State is a tiny JSON file next to the script.  It is written on reset and at most
once per SAVE_INTERVAL_SECONDS, not on every sample, to avoid pointless disk
churn.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

WIRELESS = "wireless"
CHARGING = "charging"
SLEEPING = "sleeping"
ABSENT = "absent"

RESET_JUMP = 4  # a rise of this many points means it was charged meanwhile
MAX_GAP_SECONDS = 180.0  # longer sample gap -> stale, do not credit the time
SAVE_INTERVAL_SECONDS = 300.0

# 重置原因
BASELINE = "baseline"  # 首次运行，只为建立基线
UNPLUG = "unplug"  # 亲眼看到 charging -> wireless
JUMP = "jump"  # 无线状态下电量回升，只能推断


def human_duration(seconds: float) -> str:
    """Format a duration in the coarsest useful unit (minutes at minimum)."""
    total = max(0, int(seconds))
    minutes = total // 60
    if minutes < 1:
        return "不足 1 分钟"
    if minutes < 60:
        return f"{minutes} 分钟"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} 小时 {minutes} 分" if minutes else f"{hours} 小时"
    days, hours = divmod(hours, 24)
    return f"{days} 天 {hours} 小时" if hours else f"{days} 天"


def _stamp(ts: float | None) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "未记录"


class Tracker:
    """Cumulative on-time counter backed by a small JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.seconds: float = 0.0
        self.since: float | None = None
        self.charge_end: float | None = None
        self.charge_end_exact: bool = False
        self._anchor: float | None = None
        self._last_percent: int | None = None
        self._last_state: str | None = None
        self._saved_seconds: float = 0.0
        self._saved_at: float = 0.0
        self._load()

    # ---------- 持久化 ----------

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        try:
            self.seconds = float(data.get("seconds") or 0.0)
            since = data.get("since")
            charge_end = data.get("charge_end")
            if since is None and charge_end is not None:
                # 旧格式只有一个 charge_end，且它可能是“首次运行基线”，
                # 无法分辨，只能降级成计时起点，不再声称是充电完成时刻。
                since = charge_end
                charge_end = None
            self.since = float(since) if since is not None else None
            self.charge_end = float(charge_end) if charge_end is not None else None
            self.charge_end_exact = bool(data.get("charge_end_exact")) and (
                self.charge_end is not None
            )
            anchor = data.get("anchor")
            self._anchor = float(anchor) if anchor is not None else None
            percent = data.get("last_percent")
            self._last_percent = int(percent) if percent is not None else None
            self._last_state = data.get("last_state")
        except (TypeError, ValueError):
            return
        self._saved_seconds = self.seconds

    def save(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        payload = {
            "seconds": round(self.seconds, 1),
            "since": self.since,
            "charge_end": self.charge_end,
            "charge_end_exact": self.charge_end_exact,
            "anchor": self._anchor,
            "last_percent": self._last_percent,
            "last_state": self._last_state,
        }
        try:
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            return
        self._saved_seconds = self.seconds
        self._saved_at = now

    # ---------- 采样 ----------

    def on_sample(self, state: str, percent: int | None, now: float | None = None) -> bool:
        """Feed one reading. Returns True when a new cycle started."""
        now = time.time() if now is None else now
        reason = self._detect_reset(state, percent)
        if reason is not None:
            self.seconds = 0.0
            self.since = now
            if reason == UNPLUG and not self._blind_gap(now):
                # 完整看到“拔线 → 回到无线”，这个时间就是充电结束
                self.charge_end = now
                self.charge_end_exact = True
            elif reason == JUMP:
                # 电量回升只能说明“上次采样之后某个时刻充完了”
                self.charge_end = now
                self.charge_end_exact = False
            else:
                # 基线 / 盲区里的拔线：只知道从此开始计时，充电何时结束不可知
                self.charge_end = None
                self.charge_end_exact = False

        # 只在“无线在线”的连续两帧之间累计，充电 / 休眠 / 掉线都不算使用
        elif state == WIRELESS and self._last_state == WIRELESS and self._anchor is not None:
            gap = now - self._anchor
            if 0 < gap <= MAX_GAP_SECONDS:
                self.seconds += gap

        self._anchor = now
        if percent is not None:
            self._last_percent = percent
        self._last_state = state

        if reason is not None or (now - self._saved_at) >= SAVE_INTERVAL_SECONDS:
            self.save(now)
        return reason is not None

    def _blind_gap(self, now: float) -> bool:
        """上一帧离现在太久 -> 期间发生的事都是猜的。"""
        return self._anchor is None or (now - self._anchor) > MAX_GAP_SECONDS

    def _detect_reset(self, state: str, percent: int | None) -> str | None:
        # 首次运行：无线在线就以此为基线，否则菜单永远停在“尚未记录”
        if self.since is None and state == WIRELESS:
            return BASELINE
        # 正常路径：充完电拔线，链路回到无线
        if self._last_state == CHARGING and state == WIRELESS:
            return UNPLUG
        # 兜底：本程序没运行时充过电，只能靠电量跳升反推。
        # 充电中不参与，否则边充边涨会被判成一次“充满”。
        if (
            state == WIRELESS
            and percent is not None
            and self._last_percent is not None
            and percent >= self._last_percent + RESET_JUMP
        ):
            return JUMP
        return None

    # ---------- 展示 ----------

    @property
    def has_data(self) -> bool:
        return self.since is not None

    def text(self) -> str:
        if not self.has_data:
            return "本次启动开始计时"
        return human_duration(self.seconds)

    def menu_text(self) -> str:
        return f"自上次充电：{self.text()}"

    def detail_text(self) -> str:
        if not self.has_data:
            return "自上次充电：尚未记录（从本次运行开始计时）"
        lines = [f"自上次充电：{human_duration(self.seconds)}"]
        if self.charge_end is not None and self.charge_end_exact:
            lines.append(f"上次充电完成：{_stamp(self.charge_end)}")
        elif self.charge_end is not None:
            lines.append(f"上次充电完成：约 {_stamp(self.charge_end)}（据电量回升推断）")
        else:
            lines.append(f"计时起点：{_stamp(self.since)}（未观察到充电过程）")
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {
            "seconds": round(self.seconds, 1),
            "text": self.text(),
            "since": (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.since))
                if self.since
                else None
            ),
            "charge_end": (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.charge_end))
                if self.charge_end
                else None
            ),
            "charge_end_exact": self.charge_end_exact,
        }
