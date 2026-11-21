"""使用时长统计的离线测试，不需要鼠标。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import usage


class TrackerTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "usage.json"
        self.t = usage.Tracker(self.path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ---------- 基线 ----------

    def test_first_wireless_sample_sets_baseline(self) -> None:
        self.assertTrue(self.t.on_sample(usage.WIRELESS, 80, now=1000))
        self.assertEqual(self.t.seconds, 0)
        self.assertEqual(self.t.charge_end, 1000)
        self.assertTrue(self.t.has_data)

    def test_first_sample_is_persisted(self) -> None:
        self.t.on_sample(usage.WIRELESS, 80, now=1000)
        self.assertTrue(self.path.exists())
        self.assertIn("charge_end", self.path.read_text(encoding="utf-8"))

    def test_absent_state_does_not_create_baseline(self) -> None:
        self.t.on_sample(usage.ABSENT, None, now=1000)
        self.assertFalse(self.t.has_data)

    # ---------- 累计 ----------

    def test_accumulates_between_wireless_samples(self) -> None:
        self.t.on_sample(usage.WIRELESS, 80, now=1000)
        self.t.on_sample(usage.WIRELESS, 79, now=1060)
        self.assertAlmostEqual(self.t.seconds, 60)
        self.t.on_sample(usage.WIRELESS, 79, now=1120)
        self.assertAlmostEqual(self.t.seconds, 120)

    def test_charging_time_is_not_counted(self) -> None:
        self.t.on_sample(usage.WIRELESS, 80, now=1000)
        self.t.on_sample(usage.WIRELESS, 79, now=1060)
        self.t.on_sample(usage.CHARGING, None, now=1120)
        self.t.on_sample(usage.CHARGING, None, now=1180)
        self.assertAlmostEqual(self.t.seconds, 60)

    def test_sleeping_time_is_not_counted(self) -> None:
        self.t.on_sample(usage.WIRELESS, 80, now=1000)
        self.t.on_sample(usage.SLEEPING, None, now=1060)
        self.assertAlmostEqual(self.t.seconds, 0)
        # 休眠醒来后第一帧不补算休眠期间的间隔
        self.t.on_sample(usage.WIRELESS, 80, now=1120)
        self.assertAlmostEqual(self.t.seconds, 0)

    def test_long_gap_is_discarded(self) -> None:
        """关机或电脑睡眠期间的时间不应计入。"""
        self.t.on_sample(usage.WIRELESS, 80, now=1000)
        self.t.on_sample(usage.WIRELESS, 79, now=1000 + 3600)
        self.assertEqual(self.t.seconds, 0)

    def test_gap_right_at_limit_is_kept(self) -> None:
        self.t.on_sample(usage.WIRELESS, 80, now=1000)
        self.t.on_sample(usage.WIRELESS, 80, now=1000 + usage.MAX_GAP_SECONDS)
        self.assertAlmostEqual(self.t.seconds, usage.MAX_GAP_SECONDS)

    # ---------- 重置 ----------

    def test_unplug_resets_counter(self) -> None:
        self.t.on_sample(usage.WIRELESS, 80, now=1000)
        self.t.on_sample(usage.WIRELESS, 79, now=1060)
        self.t.on_sample(usage.CHARGING, None, now=1120)
        self.assertTrue(self.t.on_sample(usage.WIRELESS, 60, now=1180))
        self.assertEqual(self.t.seconds, 0)

    def test_percent_jump_resets_counter(self) -> None:
        """本程序没运行时充过电，只能靠电量跳升反推。"""
        self.t.on_sample(usage.WIRELESS, 40, now=1000)
        self.t.on_sample(usage.WIRELESS, 39, now=1060)
        self.assertTrue(self.t.on_sample(usage.WIRELESS, 44, now=1120))
        self.assertEqual(self.t.seconds, 0)

    def test_small_readout_noise_does_not_reset(self) -> None:
        self.t.on_sample(usage.WIRELESS, 40, now=1000)
        self.assertFalse(self.t.on_sample(usage.WIRELESS, 42, now=1060))
        self.assertAlmostEqual(self.t.seconds, 60)

    # ---------- 持久化 ----------

    def test_round_trip_keeps_counter(self) -> None:
        self.t.on_sample(usage.WIRELESS, 80, now=1000)
        self.t.on_sample(usage.WIRELESS, 79, now=1060)
        self.t.save(now=1060)

        restored = usage.Tracker(self.path)
        self.assertAlmostEqual(restored.seconds, 60)
        self.assertEqual(restored.charge_end, 1000)

    def test_counter_continues_after_restart(self) -> None:
        self.t.on_sample(usage.WIRELESS, 80, now=1000)
        self.t.on_sample(usage.WIRELESS, 79, now=1060)
        self.t.save(now=1060)

        restored = usage.Tracker(self.path)
        restored.on_sample(usage.WIRELESS, 78, now=1120)
        self.assertAlmostEqual(restored.seconds, 120)

    def test_corrupt_file_falls_back_to_zero(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")
        tracker = usage.Tracker(self.path)
        self.assertEqual(tracker.seconds, 0)
        self.assertFalse(tracker.has_data)


class FormatTest(unittest.TestCase):
    def test_human_duration(self) -> None:
        cases = [
            (0, "不足 1 分钟"),
            (59, "不足 1 分钟"),
            (60, "1 分钟"),
            (3599, "59 分钟"),
            (3600, "1 小时"),
            (3900, "1 小时 5 分"),
            (86400, "1 天"),
            (90000, "1 天 1 小时"),
        ]
        for seconds, expected in cases:
            with self.subTest(seconds=seconds):
                self.assertEqual(usage.human_duration(seconds), expected)

    def test_menu_text_when_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tracker = usage.Tracker(Path(tmp) / "usage.json")
            self.assertIn("自上次充电", tracker.menu_text())
            self.assertIn("本次启动", tracker.menu_text())


if __name__ == "__main__":
    unittest.main()
