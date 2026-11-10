"""协议解析的离线自测（不需要鼠标，可在任意机器上跑）。

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aj179_battery as core  # noqa: E402


class ParseBatteryFrame(unittest.TestCase):
    def test_valid_frame(self):
        # 实测帧：05 00 00 38 -> 56%
        self.assertEqual(core._parse_frame(bytes([0x05, 0x00, 0x00, 0x38])), 0x38)

    def test_full_frame_with_tail(self):
        frame = bytes([0x05, 0x00, 0x00, 0x35]) + bytes(61)
        self.assertEqual(core._parse_frame(frame), 0x35)

    def test_hundred_percent(self):
        self.assertEqual(core._parse_frame(bytes([0x05, 0x00, 0x00, 0x64])), 100)

    def test_link_not_ready_is_none(self):
        # 链路未拉起时接收器返回全零，绝不能当成 0%
        self.assertIsNone(core._parse_frame(bytes(65)))

    def test_dirty_frame_is_none(self):
        # 重连瞬间 byte1/2 非零 -> 丢弃
        self.assertIsNone(core._parse_frame(bytes([0x05, 0x00, 0x03, 0x40])))

    def test_out_of_range_is_none(self):
        self.assertIsNone(core._parse_frame(bytes([0x05, 0x00, 0x00, 0x65])))

    def test_short_frame_is_none(self):
        self.assertIsNone(core._parse_frame(b"\x05\x00"))


class ClockFrame(unittest.TestCase):
    WHEN = time.strptime("2026-11-10 18:30:00", "%Y-%m-%d %H:%M:%S")

    def test_layout(self):
        frame = core.build_clock_frame(self.WHEN)
        self.assertEqual(len(frame), 65)
        self.assertEqual(frame[0], 0x00)  # report id
        self.assertEqual(frame[1], 0x28)  # SET_OLEDCLOCK
        self.assertEqual(frame[2:8], bytes(6))
        self.assertEqual(frame[8], 0xD7)  # 必需标记，缺了固件静默丢包
        self.assertEqual(frame[9:11], b"\x07\xea")  # 2026，大端
        self.assertEqual(
            (frame[11], frame[12], frame[13], frame[14], frame[15]), (11, 10, 18, 30, 0)
        )
        self.assertEqual(frame[16:], bytes(49))

    def test_documented_sample(self):
        # README 中记录的实测字节
        expected = bytes.fromhex("00 28 00 00 00 00 00 00 d7 07 ea 0b 0a 12 1e 00")
        self.assertEqual(core.build_clock_frame(self.WHEN)[:16], expected)

    def test_no_checksum_dependency(self):
        # 换一个日期，除时间字段外其余字节必须保持不变
        other = core.build_clock_frame(time.strptime("2030-01-02 03:04:05", "%Y-%m-%d %H:%M:%S"))
        self.assertEqual(other[:9], core.build_clock_frame(self.WHEN)[:9])
        self.assertEqual(other[16:], bytes(49))


if __name__ == "__main__":
    unittest.main()
