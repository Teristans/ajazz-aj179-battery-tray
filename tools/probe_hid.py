"""HID 设备探测工具 — 判断某只 AJAZZ 鼠标能否被本项目支持。

只读，不发送任何写命令。用法：

    python tools/probe_hid.py            # 全量枚举 + 探测厂商集合
    python tools/probe_hid.py --json     # 机器可读输出，便于贴到 issue

输出三段：
  1. 所有 HID 设备（VID:PID / usage_page / usage / 接口号）
  2. 厂商私有集合（usage_page >= 0xFF00）的 feature report 探测（相同结构自动合并）
  3. 厂商集合上的 input report 监听（部分型号由设备主动推送状态，如星闪版）

把整段输出贴出来即可判断支持与否。
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import hid

# 已知同族：VID -> 方案说明
KNOWN_VIDS = {
    0x3151: "SONiX 方案，本项目直接支持（AJ179 / AJ159 APEX 2.4G）",
    0x7768: "NearLink 星闪方案（AJ159 NL / NLP），协议不同，需适配",
    0x248A: "AJAZZ 2.4G 接收器，未适配",
    0x249A: "AJAZZ 2.4G 接收器别名，未适配",
    0x25A7: "常见国产 2.4G 方案，未适配",
    0x373B: "常见国产 2.4G 方案，未适配",
}

SUPPORTED_VID = 0x3151
NEARLINK_VID = 0x7768

FEATURE_PROBE_IDS = list(range(0x00, 0x10)) + [0x28, 0x53]
INPUT_LISTEN_SECONDS = 2.5


def _as_text(value) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value or "")


def _enumerate() -> list[dict]:
    rows = []
    for d in hid.enumerate():
        rows.append(
            {
                "vid_pid": f"{d['vendor_id']:04X}:{d['product_id']:04X}",
                "vid": d["vendor_id"],
                "pid": d["product_id"],
                "usage_page": d.get("usage_page") or 0,
                "usage": d.get("usage") or 0,
                "interface": d.get("interface_number"),
                "manufacturer": d.get("manufacturer_string") or "",
                "product": d.get("product_string") or "",
                # 展示用字符串；hidapi 的 open_path 只接受原始 bytes
                "path": _as_text(d.get("path")),
                "_raw_path": d.get("path"),
            }
        )
    return rows


def _probe_feature(collection: dict) -> list[dict]:
    """逐个 report id 尝试 GET_FEATURE，按「载荷结构」合并结果。

    多数固件会把请求的 report id 原样回显在第 0 字节，于是十几个 id 看起来
    都有数据，实际是同一块状态寄存器。这里按 payload 前 8 字节分组去重。
    """
    groups: dict[bytes, dict] = {}
    handle = hid.device()
    try:
        handle.open_path(collection["_raw_path"])
    except OSError as exc:
        return [{"error": str(exc)}]
    try:
        for rid in FEATURE_PROBE_IDS:
            try:
                data = bytes(handle.get_feature_report(rid, 65))
            except OSError:
                continue
            if len(data) <= 1 or not any(data[1:]):
                continue
            body = data[1:]
            entry = groups.setdefault(
                body[:8],
                {"ids": [], "hex": data[:16].hex(" "), "len": len(data), "body": body},
            )
            entry["ids"].append(rid)
    finally:
        try:
            handle.close()
        except Exception:
            pass
    return list(groups.values())


def _listen_input(collection: dict) -> list[dict]:
    """监听设备主动推送的 input report（部分方案的唯一获取途径）。"""
    seen: dict[str, int] = {}
    handle = hid.device()
    try:
        handle.open_path(collection["_raw_path"])
    except OSError:
        return []
    try:
        deadline = time.time() + INPUT_LISTEN_SECONDS
        while time.time() < deadline:
            try:
                data = handle.read(64, timeout_ms=200)
            except OSError:
                break
            if not data:
                continue
            key = bytes(data).hex(" ")
            seen[key] = seen.get(key, 0) + 1
    finally:
        try:
            handle.close()
        except Exception:
            pass
    return [{"hex": k, "count": v} for k, v in list(seen.items())[:12]]


def _battery_hint(body: bytes) -> str:
    """对「疑似电量块」给出可读判断，免得用户盯原始字节猜。"""
    if len(body) < 3:
        return ""
    if body[0] == 0 and body[1] == 0 and 0 < body[2] <= 100:
        return f"← 疑为电量：byte3 = {body[2]}%（与已验证的 0x05 帧结构一致）"
    if body[0] == 0x03 and 0 < body[1] <= 100:
        return f"← 疑为星闪版电量：tag 0x03 后 = {body[1]}%"
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="HID 设备探测（只读）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    devices = _enumerate()
    vids = {d["vid"] for d in devices}
    vendor = [d for d in devices if d["usage_page"] >= 0xFF00]

    if SUPPORTED_VID in vids:
        verdict = "检测到 0x3151 设备：本项目应当可用，跑 python aj179_tray.py --once 验证读数"
    elif NEARLINK_VID in vids:
        verdict = "检测到 0x7768 星闪设备：当前版本不支持（协议不同），需新增适配层"
    elif not devices:
        verdict = "未枚举到任何 HID 设备"
    else:
        verdict = "未检测到 0x3151 / 0x7768，本项目的匹配条件不成立"

    report = {
        "verdict": verdict,
        "known_vids": {f"0x{v:04X}": KNOWN_VIDS[v] for v in vids if v in KNOWN_VIDS},
        "devices": [{k: v for k, v in d.items() if not k.startswith("_")} for d in devices],
        "vendor_collections": [],
    }
    probe_cache = {}
    for c in vendor:
        groups = _probe_feature(c)
        probe_cache[id(c)] = groups
        report["vendor_collections"].append(
            {
                "vid_pid": c["vid_pid"],
                "usage_page": f"0x{c['usage_page']:04X}",
                "usage": f"0x{c['usage']:04X}",
                "feature_reports": [
                    {
                        "report_ids": [f"0x{i:02X}" for i in g["ids"]],
                        "len": g["len"],
                        "hex": g["hex"],
                    }
                    for g in groups
                    if "error" not in g
                ],
                "input_reports": _listen_input(c),
            }
        )

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print("=" * 72)
    print("结论：" + verdict)
    for vid, desc in report["known_vids"].items():
        print(f"  {vid} -> {desc}")
    print("=" * 72)

    print(f"\n[1] 全部 HID 设备（{len(devices)} 个集合）")
    for d in devices:
        print(
            f"  {d['vid_pid']}  page=0x{d['usage_page']:04X} usage=0x{d['usage']:04X} "
            f"if={d['interface']}  {d['manufacturer']} | {d['product']}"
        )

    print(f"\n[2] 厂商私有集合（usage_page >= 0xFF00）：{len(vendor)} 个")
    if not vendor:
        print("  无 —— 该设备没有暴露厂商控制集合，任何读法都无从下手")
    for c in vendor:
        print(f"\n  --- {c['vid_pid']}  page=0x{c['usage_page']:04X} usage=0x{c['usage']:04X} ---")
        groups = probe_cache[id(c)]
        print("  feature report（相同结构已合并）：")
        if groups and "error" in groups[0]:
            print(f"    打开失败：{groups[0]['error']}")
        elif not groups:
            print("    所有 id 均返回全零或不可读")
        for g in groups:
            if "error" in g:
                continue
            ids = ",".join(f"0x{i:02X}" for i in g["ids"])
            print(f"    ids={ids}")
            print(f"      len={g['len']}  {g['hex']}  {_battery_hint(g['body'])}")
        print(f"  input report 监听 {INPUT_LISTEN_SECONDS}s（设备主动推送）：")
        packets = _listen_input(c)
        if not packets:
            print("    无推送（或该集合不支持读）")
        for p in packets:
            print(f"    x{p['count']:<4} {p['hex']}")

    print(
        "\n判据备忘：本项目认 VID 0x3151 + usage_page 0xFFFF/usage 0x02，电量在 0x05 帧 byte3。"
        "\n星闪版（AJ159 NL，VID 0x7768）走 usage 0xFFB1/0xFFB2/0xFFB3 + report id 6，"
        "\n电量由设备主动推送：06 fa 03 00 00 03 64 01（tag 0x03 后为百分比，再后为充电标志）。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
