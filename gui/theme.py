"""TraceHub 视觉规范。

颜色与尺寸取自设计稿（TraceHub_UI_PRD.md 第 6 节 Design Tokens），
界面模块统一从这里取色，避免样式散落在各文件里。
"""

from __future__ import annotations

from PySide6.QtGui import QFont

from utils.paths import assets_dir

ASSETS_DIR = assets_dir()

# ---- 背景 ----
BG_APP = "#0e1116"
BG_PANEL = "#151a21"
BG_PANEL_2 = "#1b2129"
BG_ELEV = "#20272f"
BG_INPUT = "#0b0e12"

# ---- 边框 ----
BORDER = "#2a313b"
BORDER_SOFT = "#232a32"

# ---- 文字 ----
TEXT_STRONG = "#e6edf3"
TEXT = "#c2cad3"
TEXT_DIM = "#7d8794"
TEXT_FAINT = "#565f6b"

# ---- 强调与语义 ----
ACCENT = "#22d3ee"
ACCENT_2 = "#3b82f6"
SUCCESS = "#34d399"
WARNING = "#fbbf24"
ERROR = "#f87171"
DEBUG = "#94a3b8"
INFO = "#60a5fa"

SEL_BG = "rgba(34, 211, 238, 0.14)"
ERROR_ROW_RGBA = (248, 113, 113, 20)
WARN_ROW_RGBA = (251, 191, 36, 14)

LEVEL_COLORS = {
    "INFO": INFO,
    "DEBUG": DEBUG,
    "WARN": WARNING,
    "ERROR": ERROR,
}

UI_FAMILIES = ["Inter", "Segoe UI", "Microsoft YaHei UI", "PingFang SC"]
MONO_FAMILIES = ["JetBrains Mono", "Cascadia Mono", "Consolas", "Courier New"]
_MONO_FAMILIES_CSS = ", ".join(f'"{name}"' for name in MONO_FAMILIES)


def ui_font(size: int = 13, bold: bool = False) -> QFont:
    font = QFont()
    font.setFamilies(UI_FAMILIES)
    font.setPixelSize(size)
    font.setBold(bold)
    return font


def mono_font(size: int = 12) -> QFont:
    font = QFont()
    font.setFamilies(MONO_FAMILIES)
    font.setPixelSize(size)
    return font


def asset(name: str) -> str:
    """assets 目录下资源的绝对路径（QSS 里用 url() 需要绝对路径）。"""
    return (ASSETS_DIR / name).as_posix()


_STYLESHEET = """
QWidget#appRoot, QMainWindow { background: @BG_APP@; }

/* ---------------- 标题栏 ---------------- */
QWidget#titleBar {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #11161d, stop:1 #0c1015);
    border-bottom: 1px solid @BORDER@;
}
QLabel#brandLogo {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 @ACCENT@, stop:1 @ACCENT_2@);
    border-radius: 6px;
    color: #04121a;
    font-size: 13px;
    font-weight: 800;
}
QLabel#brandText { color: @TEXT_STRONG@; font-weight: 700; }
QPushButton#menuAccent {
    background: rgba(45, 225, 230, 0.07);
    border: 1px solid rgba(45, 225, 230, 0.28);
    border-radius: 6px;
    color: @TEXT@;
    font-weight: 600;
    font-size: 12px;
    padding: 5px 10px;
}
QPushButton#menuAccent:hover {
    background: rgba(45, 225, 230, 0.14);
    color: @TEXT_STRONG@;
}
QPushButton#winControl {
    background: transparent;
    border: 0;
    border-radius: 5px;
    color: @TEXT_DIM@;
    font-size: 13px;
}
QPushButton#winControl:hover { background: @BG_ELEV@; color: @TEXT_STRONG@; }
QPushButton#winControl[close="true"]:hover { background: #e5484d; color: #ffffff; }

/* ---------------- 侧边栏 ---------------- */
QWidget#sidebar { background: @BG_PANEL@; border-right: 1px solid @BORDER@; }
QLabel#sideHead { color: @TEXT_FAINT@; font-size: 11px; }
QLabel#panelTitle { color: @TEXT_DIM@; font-size: 11px; font-weight: 600; }
QFrame#panelBody {
    background: @BG_PANEL_2@;
    border: 1px solid @BORDER@;
    border-radius: 8px;
}
QToolButton#groupTitle {
    background: transparent;
    border: 0;
    color: @TEXT_DIM@;
    font-size: 11px;
    font-weight: 600;
    padding: 5px 8px;
    text-align: left;
}
QToolButton#groupTitle:hover { background: @BG_ELEV@; border-radius: 6px; }
QScrollArea#sessionScroll { background: transparent; border: 0; }
QScrollArea#sessionScroll > QWidget > QWidget { background: transparent; }

/* 会话节点 */
QFrame#sessionNode { border-radius: 6px; border-left: 2px solid transparent; }
QFrame#sessionNode:hover { background: @BG_ELEV@; }
QFrame#sessionNode[active="true"] {
    background: @SEL_BG@;
    border-left: 2px solid @ACCENT@;
}
QLabel#nodeName { color: @TEXT@; font-size: 12px; }
QLabel#nodeBadge {
    background: @BG_ELEV@;
    color: @TEXT_DIM@;
    border-radius: 8px;
    padding: 1px 6px;
    font-size: 10px;
}
QLabel#nodeBadge[state="live"] { background: rgba(52, 211, 153, 0.15); color: @SUCCESS@; }
QLabel#nodeBadge[state="err"] { background: rgba(248, 113, 113, 0.16); color: @ERROR@; }

/* ---------------- 通用控件 ---------------- */
QCheckBox { color: @TEXT@; font-size: 12px; spacing: 8px; }
QCheckBox:hover { color: @TEXT_STRONG@; }
QCheckBox::indicator {
    width: 14px; height: 14px;
    border: 1px solid @BORDER@;
    border-radius: 3px;
    background: @BG_INPUT@;
}
QCheckBox::indicator:hover { border-color: @ACCENT@; }
QCheckBox::indicator:checked { background: #2563eb; border-color: #2563eb; }

QLineEdit, QComboBox, QSpinBox {
    background: @BG_INPUT@;
    border: 1px solid @BORDER@;
    border-radius: 6px;
    color: @TEXT_STRONG@;
    padding: 6px 9px;
    font-size: 12px;
    selection-background-color: @ACCENT@;
    selection-color: #04121a;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: @ACCENT@; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled { color: @TEXT_FAINT@; }
QComboBox::drop-down { border: 0; width: 20px; }
QComboBox::down-arrow { image: url(@CHEVRON@); width: 9px; height: 9px; }
QComboBox QAbstractItemView {
    background: @BG_ELEV@;
    border: 1px solid @BORDER@;
    color: @TEXT@;
    selection-background-color: @BG_INPUT@;
    selection-color: @TEXT_STRONG@;
    outline: none;
}
QSpinBox::up-button, QSpinBox::down-button { width: 16px; border: 0; background: transparent; }

QPushButton#panelButton, QPushButton#dialogButton {
    background: @BG_ELEV@;
    border: 1px solid @BORDER@;
    border-radius: 6px;
    color: @TEXT_STRONG@;
    font-size: 12px;
    padding: 7px 14px;
}
QPushButton#panelButton:hover, QPushButton#dialogButton:hover {
    background: #2a323c;
    border-color: #3a444f;
}
QPushButton#primaryButton {
    background: rgba(34, 211, 238, 0.14);
    border: 1px solid rgba(34, 211, 238, 0.45);
    border-radius: 6px;
    color: @ACCENT@;
    font-size: 12px;
    padding: 7px 18px;
}
QPushButton#primaryButton:hover { background: rgba(34, 211, 238, 0.22); }
QPushButton#dangerButton {
    background: rgba(248, 113, 113, 0.16);
    border: 1px solid rgba(248, 113, 113, 0.5);
    border-radius: 6px;
    color: @ERROR@;
    font-size: 12px;
    padding: 7px 18px;
}
QPushButton#dangerButton:hover { background: rgba(248, 113, 113, 0.26); }

/* ---------------- 标签栏 ---------------- */
QWidget#tabStrip { background: @BG_PANEL@; border-bottom: 1px solid @BORDER@; }
QFrame#tabItem {
    background: transparent;
    border-right: 1px solid @BORDER@;
    border-bottom: 2px solid transparent;
}
QFrame#tabItem:hover { background: @BG_ELEV@; }
QFrame#tabItem[active="true"] {
    background: @BG_APP@;
    border-bottom: 2px solid @ACCENT@;
}
QLabel#tabTitle { color: @TEXT_DIM@; font-size: 12px; }
QFrame#tabItem[active="true"] QLabel#tabTitle { color: @TEXT_STRONG@; }
QPushButton#tabClose {
    background: transparent;
    border: 0;
    border-radius: 4px;
    color: @TEXT_FAINT@;
    font-size: 12px;
}
QPushButton#tabClose:hover { background: @BG_ELEV@; color: @TEXT_STRONG@; }
QPushButton#tabClose:disabled { color: #39424d; }
QPushButton#tabClose:disabled:hover { background: transparent; }

/* ---------------- 日志区 ---------------- */
QPlainTextEdit#logView {
    background: @BG_APP@;
    border: 0;
    color: @TEXT@;
}

/* ---------------- 串口命令输入条 ---------------- */
QWidget#commandBar {
    background: @BG_PANEL@;
    border-top: 1px solid @BORDER@;
}
QLabel#commandLabel {
    color: @TEXT_DIM@;
    font-size: 11px;
    font-weight: 600;
}
QLineEdit#commandInput {
    background: @BG_INPUT@;
    border: 1px solid @BORDER@;
    border-radius: 6px;
    color: @TEXT_STRONG@;
    font-family: @MONO@;
    font-size: 12px;
}
QLineEdit#commandInput:focus {
    border-color: @ACCENT@;
}
QPushButton#commandClose {
    background: transparent;
    border: 0;
    border-radius: 4px;
    color: @TEXT_FAINT@;
    font-size: 12px;
}
QPushButton#commandClose:hover {
    background: @BG_ELEV@;
    color: @TEXT_STRONG@;
}

/* ---------------- 滚动条 ---------------- */
QScrollBar:vertical { background: transparent; width: 9px; margin: 0; }
QScrollBar::handle:vertical { background: #2e3742; border-radius: 4px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: #3a444f; }
QScrollBar:horizontal { background: transparent; height: 9px; margin: 0; }
QScrollBar::handle:horizontal { background: #2e3742; border-radius: 4px; min-width: 28px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ---------------- 菜单 ---------------- */
QMenu {
    background: @BG_ELEV@;
    border: 1px solid @BORDER@;
    border-radius: 8px;
    padding: 5px;
}
QMenu::item {
    padding: 8px 14px;
    border-radius: 6px;
    color: @TEXT@;
    font-size: 12px;
}
QMenu::item:selected { background: @BG_INPUT@; color: @TEXT_STRONG@; }

/* ---------------- 弹窗 ---------------- */
QDialog { background: @BG_PANEL_2@; }
QWidget#dialogHead {
    background: @BG_PANEL@;
    border-bottom: 1px solid @BORDER@;
}
QLabel#dialogTitle { color: @TEXT_STRONG@; font-size: 13px; font-weight: 600; }
QPushButton#dialogClose {
    background: transparent;
    border: 0;
    color: @TEXT_FAINT@;
    font-size: 14px;
}
QPushButton#dialogClose:hover { color: @TEXT_STRONG@; }
QWidget#dialogFoot {
    background: @BG_PANEL@;
    border-top: 1px solid @BORDER@;
}
QLabel#fieldLabel { color: @TEXT@; font-size: 12px; }
QLabel#hintLabel { color: @TEXT_DIM@; font-size: 11px; }
QMessageBox { background: @BG_PANEL_2@; }
"""


def stylesheet() -> str:
    """返回替换好 token 与资源路径的全局样式表。"""
    text = _STYLESHEET
    for key, value in (
        ("BG_APP", BG_APP),
        ("BG_PANEL", BG_PANEL),
        ("BG_PANEL_2", BG_PANEL_2),
        ("BG_ELEV", BG_ELEV),
        ("BG_INPUT", BG_INPUT),
        ("BORDER", BORDER),
        ("TEXT_STRONG", TEXT_STRONG),
        ("TEXT", TEXT),
        ("TEXT_DIM", TEXT_DIM),
        ("TEXT_FAINT", TEXT_FAINT),
        ("ACCENT_2", ACCENT_2),
        ("ACCENT", ACCENT),
        ("SUCCESS", SUCCESS),
        ("WARNING", WARNING),
        ("ERROR", ERROR),
        ("SEL_BG", SEL_BG),
        ("MONO", _MONO_FAMILIES_CSS),
    ):
        text = text.replace(f"@{key}@", value)
    text = text.replace("@CHEVRON@", asset("chevron-down.png"))
    return text
