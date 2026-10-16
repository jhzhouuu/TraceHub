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
utils/ansi_helper.py         ANSI 颜色处理
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
