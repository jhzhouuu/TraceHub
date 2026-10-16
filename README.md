# TraceHub

多源追踪终端：同时查看多个调试源（串口 / J-Link RTT）的实时日志。
界面按 `TraceHub_UI_PRD.md`（v1.0）实现，采集能力接真实硬件。

## 界面结构

```
┌ 标题栏 38px ─ 品牌 + 新建J-Link 会话 / 新建串口会话 + 窗口控制 ─┐
├ 侧栏 248px ───────────┬ 主视图 ───────────────────────────────┤
│ 追踪源                │ 标签栏 24px                            │
│  ▾ 已存会话           │  ┌──────────────────────────────────┐  │
│     · 会话节点        │  │ 日志流                            │  │
│  显示设置             │  │ 行号 · 时间戳 · 级别 · 消息        │  │
│  自动保存             │  └──────────────────────────────────┘  │
│  清空显示             │                                        │
└───────────────────────┴────────────────────────────────────────┘
```

## 交互

| 操作 | 行为 |
|------|------|
| 双击侧栏会话 | 打开标签并连接；已打开时切回该标签（单击不打开） |
| 右键侧栏会话 | 编辑会话 / 删除会话（删除有二次确认） |
| 标签 `✕` | 关闭视图并断开连接；关掉最后一个标签后主视图回到空状态提示 |
| 右键标签 | 发送数据… / 打开连接｜断开连接 / 关闭标签 |
| 点击日志行 | 整行高亮选中 |
| `Ctrl+F` | 查找（全词匹配、区分大小写、上下一个、回车即查找），右上角显示匹配进度如 `1/23` |
| 启动 | 默认最大化；拖动标题栏移动、双击标题栏切换最大化 |
| 窗口 ≤920px | 自动隐藏侧栏 |
| 标题栏双击 | 最大化 / 还原；拖动标题栏移动窗口 |

显示设置（Hex 显示、显示时间戳、最大行数）与自动保存开关作用于**当前选中的会话**，
写入 `config.json` 持久化。

## 日志渲染

- 级别自动识别：`[ERROR]`/`E:`/`WARN`/`[I]`/`D:` 等标记，识别不到就不显示级别
- INFO 蓝、DEBUG 灰、WARN 黄、ERROR 红；ERROR / WARN 整行带底色与左侧竖条
- 支持 ANSI 前景色（设备输出带颜色时按色显示）
- 行号是绝对值，裁剪旧行后不回卷

## 采集能力

- 串口：端口扫描、波特率、断线自动重连、发送数据（文本 / 十六进制）
- 串口断开后可用 `Ctrl+R` 手动重连当前激活的串口会话
- 串口会话中，在日志区按 `Enter` 可进入底部命令输入条；再次按 `Enter` 发送，按 `Esc` 退出输入状态
- J-Link：扫描已连接的仿真器、设备名称、SWD/JTAG、速率、可选序列号，RTT 采集与重连；
  若 J-Link 自动搜索找不到 RTT 控制块，工具会自己扫一遍 SRAM 定位，并把地址记进会话配置的
  `rtt_address`，下次连接直接用
- **多 J-Link 并发**：SEGGER 的 JLinkARM.dll 在一个进程里只能维持一条 J-Link 连接
  （打开一块后选另一块会返回 -1），所以每个 J-Link 会话跑在独立子进程里，
  可以同时打开多块探针、互不干扰。重连在子进程内部做（不会每 2 秒重建进程），
  父进程退出时子进程会自动结束，不会留下占着探针的孤儿进程
- 自动保存：`<会话名>[_SN序列号]_YYYYMMDD_HHMMSS.log`，按 64KB 或 1 秒刷盘
- 单实例：重复启动只会把已有窗口拉到前台，不会开出第二个实例

## 运行

```bash
uv venv --python 3.12
uv pip install -r requirements.txt
python main.py
```

## 与设计稿的差异（已确认）

| 项 | 设计稿原型 | 本实现 |
|----|-----------|--------|
| 弹窗「刷新」 | 600ms 后随机造一个选项 | 真实扫描串口 / J-Link 仿真器，保留「刷新中…」状态与扫描结果提示 |
| 日志级别 | 随机模拟数据 | 从真实日志文本识别（`[ERROR]`、`E:`、`[I]`、`D:` 等），识别不到就不显示级别列，不编造 |
| 发送数据 | 原型没有这个 UI | 放在标签右键菜单「发送数据…」，弹窗样式与其它弹窗一致 |
| 显示设置作用域 | 全局（原型只有一个会话） | 作用于当前选中的会话，并随会话写入 `config.json` |
| UDP 视频流源 | PRD 概述里提到 | 明确不做 |
| 搜索框 | PRD 说明已移除 | 布局里不出现，保留 `Ctrl+F` 快捷键打开查找 |

自检（不需要硬件）：

```bash
python main.py --self-test --screenshot ui.png
```

## 打包

目录版（启动快、便于排查问题）：

```bash
uv pip install pyinstaller
.venv\Scripts\pyinstaller.exe TraceHub.spec --noconfirm
```

产物：`dist\TraceHub\TraceHub.exe`（约 80MB，自带 Python 与 Qt 运行时）。

- `config.json` 与日志放在 exe 同目录，整个 `dist\TraceHub` 文件夹拷到别的机器即可运行
- J-Link 会话仍需目标机器安装 SEGGER 驱动（提供 `JLinkARM.dll`）
- 打包版也支持自检：`TraceHub.exe --self-test`，结果写入同目录 `tracehub-self-test.txt`

单文件版（只有一个 exe，方便拷贝）：

```bash
.venv\Scripts\pyinstaller.exe TraceHubOneFile.spec --noconfirm --distpath dist-onefile
```

产物：`dist-onefile\TraceHub.exe`，单独复制这个文件即可运行。它自带 Python、Qt、
pylink 等应用依赖；首次启动会比目录版慢，因为要先解压到临时目录。J-Link 功能仍
依赖目标机器已安装的 SEGGER 驱动，串口功能依赖对应设备驱动。

## 长时间运行（两周级）

| 机制 | 说明 |
|------|------|
| 重连策略 | J-Link 固定每 2 秒重连；**串口不自动重连**（打不开或断线后需手动重新打开） |
| 日志文件 | 按需求**不轮转**：一个会话从连接到断开只写一个文件，长时间挂机会持续变大（请留足磁盘空间，需要时自行归档） |
| 看门狗 | 每 30 秒巡检一次，采集线程异常退出会自动拉起 |
| 健康日志 | 每 10 分钟往 `<日志目录>\tracehub_health.log` 写一行（内存占用 + 各会话状态），超过 2MB 自动改名 |
| 视图限流 | 每个会话日志视图有行数上限（默认 20000），内存不会随运行时长增长 |

## 目录结构

```
main.py                     程序入口、单实例、自检
core/config.py              配置读写
core/interfaces.py          工作线程基类与统一信号
core/serial_worker.py       串口采集线程
core/jlink_worker.py        J-Link RTT 采集线程
core/logger.py              日志落盘
gui/theme.py                设计 token 与全局样式表
gui/title_bar.py            标题栏（品牌 / 新建会话 / 窗口控制）
gui/session_tree.py         侧栏：会话树 + 显示设置 + 自动保存 + 清空显示
gui/tab_strip.py            标签栏
gui/log_view.py             日志视图（行号、级别配色、整行选中）
gui/session_view.py         单个会话的日志页与采集线程
gui/session_dialog.py        新建/编辑/删除/发送 弹窗与遮罩
gui/find_dialog.py          查找对话框
gui/main_window.py          主窗口装配
utils/timestamp.py          时间戳格式化
utils/ansi_helper.py        ANSI 颜色处理
assets/TraceHub-C.ico       程序图标（assets 下有 .ico 就会自动使用）
```

## 配置格式

```json
{
  "sessions": [
    {
      "id": "uuid",
      "type": "jlink",
      "name": "52840_蓝牙",
      "serial_config": { "port": "COM47", "baudrate": 115200 },
      "jlink_config": {
        "device_name": "nRF52840_xxAA",
        "interface": "SWD",
        "speed": 4000,
        "serial_number": "69609946",
        "rtt_address": "0x20018bb8"
      },
      "display": { "hex": false, "timestamp": true, "max_lines": 20000, "autosave": true }
    }
  ],
  "log_path": "C:/log"
}
```

J-Link 会话需要安装 SEGGER J-Link 驱动（提供 `JLinkARM.dll`）。
