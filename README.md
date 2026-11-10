# AJ179 电量托盘

[English](README.en.md) · 简体中文

[![Platform](https://img.shields.io/badge/platform-Windows-0078d4)](https://github.com/)
[![Python](https://img.shields.io/badge/python-3.9%2B-3776ab)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Driver](https://img.shields.io/badge/官方驱动-不需要-success)]()

黑爵 AJAZZ AJ179 APEX 无线鼠标的 Windows 托盘电量显示，外加**充电底座 OLED 时钟对时**。
直接读 HID，**不需要黑爵官方驱动、不联网、不改鼠标设置**。

![托盘图标状态](assets/icon-states.png)

## 功能

- 托盘图标直接显示**电量百分比**：绿（>50%）/ 橙（21–50%）/ 红（≤20%），充电时为蓝色
- 悬停看电量与更新时间，左键点击立即刷新（默认 60 秒自动刷新）
- 电量 ≤20% 弹一次系统通知
- **底座 OLED 时钟**：程序运行期间每 30 秒把系统时间推到底座屏幕
- 开机自启（写 `HKCU\...\Run`，不建快捷方式、不留残留文件）
- 单实例运行；`--once` 输出 JSON，方便接自己的脚本 / 状态栏 / 快捷指令

## 快速开始

需要 Windows + Python 3.9 或更新（[下载](https://www.python.org/downloads/windows/)，安装时勾选 *Add Python to PATH*）。

```text
1. 双击 setup.bat     # 建 .venv 并装依赖，只需一次
2. 双击 start.bat     # 托盘区出现数字电量
```

或者手动来：

```bash
pip install -r requirements.txt
pythonw aj179_tray.py
```

> 图标可能被 Windows 收进隐藏区，点任务栏的 `^` 展开后拖出来固定即可。

## 使用

右键托盘图标：

| 菜单 | 作用 |
|---|---|
| 立即刷新 | 立刻读一次（左键单击同效） |
| 电量详情 | 状态、设备名、更新时间、原始帧 hex |
| 底座显示时间 | 开 / 关底座时钟同步，状态记在 `config.json` |
| 立即同步时钟 | 手动补一次对时 |
| 开机自动启动 | 注册表自启开关 |
| 退出 | 退出程序 |

文件夹挪位置后，请把「开机自动启动」取消再勾选一次。

## 命令行

```bash
python aj179_tray.py --once         # 输出一次 JSON（含 clock_synced）
python aj179_tray.py --sync-clock   # 只对一次时
python aj179_tray.py --quit         # 优雅退出正在运行的实例
python aj179_battery.py             # 人类可读状态 + 原始帧
```

`--once` 输出示例：

```json
{"ok": true, "state": "wireless", "product": "AJAZZ 2.4G 8K",
 "percent": 56, "charging": false, "updated_at": "18:31:07",
 "clock_synced": true, "raw": "05 00 00 38 01 00 01 02 ..."}
```

## 协议

这部分是踩坑踩出来的，官方没有任何文档；有同方案设备的可以拿来复用。

设备枚举：VID `0x3151`，厂商控制集合 `usage_page=0xFFFF, usage=0x02`。

| 形态 | PID | 产品名 |
|---|---|---|
| 无线 2.4G 接收器 | `0x5007` | AJAZZ 2.4G 8K |
| 插线 / 充电 | `0x4026` | AJAZZ AJ179 APEX |

### 电量：光读是读不到的

接收器只有在收到厂商的状态轮询后，才会把鼠标的 2.4G 遥测链路拉起来；否则状态页**永远是全零**。所以每次读取都是三步：

1. **SET_FEATURE** 状态轮询（report id `0x00`，opcode `0xF7`，其余全 0，无载荷、无校验）
   —— 这是厂商驱动每秒发一次的心跳；
2. 等约 50 ms；
3. **GET_FEATURE** report `0x05`，电量在 byte3：

```text
05 00 00 38 01 00 01 02
      ^^^^^^
      电量 0x38 = 56%
```

有效帧要求 report id 与电量之间全为 0（重连瞬间会返回脏数据）；byte3 为 0 且整帧全零代表链路未就绪，按「未知」处理，**绝不显示成 0%**。

### 底座时钟：`0x28 SET_OLEDCLOCK`

同一条控制集合上的 64 字节 feature report，**无校验**：

```text
帧[0]=0x00 report id      帧[1]=0x28 命令       帧[2..7]=0
帧[8]=0xD7 必需标记       帧[9..10]=年（大端）  帧[11]=月 帧[12]=日
帧[13]=时 帧[14]=分 帧[15]=秒                 其余全 0
```

实测 `2026-11-10 18:30:00`：

```text
00 28 00 00 00 00 00 00 d7 07 ea 0b 0a 12 1e 00
```

三个反直觉的地方，踩过才知道：

1. **`0xD7` 少一个字节，固件直接静默丢包**，没有任何报错——这是别人对时失败最常见的根因。
2. **必须定时重发**。底座会在几分钟内把时钟自己重置回 `00:00`，而且设备一直枚举在线、收不到热插拔事件，所以「只在启动时设一次」是无效的；30 秒一轮是这个功能的正常机制，不是兜底。
3. 固件上电约 2 秒后才开始接受该帧，程序启动后先等 3 秒再发第一帧。

该命令**没有任何应答**，发送成功只代表 USB 层写进去了，唯一验证方式是看底座屏幕。

### 充电状态

插线后 PID 从 `0x5007` 变成 `0x4026`，鼠标改走有线通道。此时：

- 有线通道偶尔也能读到百分比 → 显示蓝色数字
- 读不到 → 显示蓝色「充」，**绝不编造 0%**

这一段是插拔实测出来的，公开的逆向文档里没有。

## 兼容性

按 VID `0x3151` + 厂商接口特征匹配，所以**同方案的其他黑爵型号（AJ159 / AJ199 等）大概率可用**，但我只实测了 AJ179 APEX。
其他品牌（罗技走 HID++ 私有协议）完全不是这套，用不了。

如果你在别的型号上跑通了，欢迎提 issue 报一下型号和 PID。

## 自测

协议解析是纯函数，不需要硬件：

```bash
python -m unittest discover -s tests -v
```

## 已知限制

- 鼠标深度休眠时接收器不返回数据，显示 `--` 并保留上次读数，唤醒后下一次刷新恢复。
- 鼠标插线充电时，底座的 HID 接口会从系统里消失，这段时间无法对时；拔线后 30 秒内自动补上（切回无线时会立刻补一次）。
- 只支持 Windows（托盘、注册表自启、`ctypes` 消息框都依赖 Win32 API）。

## 致谢

协议细节来自这两份公开的逆向资料，没有它们这个工具做不出来：

- [Aiacos/ajazz-control-center](https://github.com/Aiacos/ajazz-control-center) —— 黑爵 AJ 系列协议文档
- [reicheltp/ajazz-macos-mousetime-sync](https://github.com/reicheltp/ajazz-macos-mousetime-sync) —— 底座时钟帧格式
- [xrfc/gpw-battery-tray](https://github.com/xrfc/gpw-battery-tray) —— 本项目的灵感来源

## License

[MIT](LICENSE)
