"""日志显示控件。

对应设计稿的 LogWrap：行号 + 时间戳 + 级别 + 消息，
ERROR / WARN 整行带底色与左侧竖条，点击整行高亮，行数受设置限制。
"""

from __future__ import annotations

import re

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget

from utils.ansi_helper import ansi_to_html

from . import theme

# 只在行首附近找级别标记，避免把正文里的单词误判成级别
_LEVEL_RE = re.compile(
    r"(?:^|[\s\[\(])(?P<tag>ERROR|ERR|WARN|WARNING|WRN|INFO|INF|DEBUG|DBG|E|W|I|D)\s*(?:\]|:|\))",
)
_LEVEL_ALIASES = {
    "ERROR": "ERROR",
    "ERR": "ERROR",
    "E": "ERROR",
    "WARN": "WARN",
    "WARNING": "WARN",
    "WRN": "WARN",
    "W": "WARN",
    "INFO": "INFO",
    "INF": "INFO",
    "I": "INFO",
    "DEBUG": "DEBUG",
    "DBG": "DEBUG",
    "D": "DEBUG",
}
_LEVEL_ORDER = {"ERROR": 0, "WARN": 1, "INFO": 2, "DEBUG": 3}


def detect_level(text: str) -> str:
    """从日志文本里识别级别，识别不到返回空串。"""
    window = text[:64]
    found = []
    for match in _LEVEL_RE.finditer(window):
        level = _LEVEL_ALIASES.get(match.group("tag").upper())
        if level:
            found.append(level)
    if found:
        return sorted(found, key=lambda item: _LEVEL_ORDER[item])[0]

    # 没有标记时只在行首认关键字，且后面必须是分隔符：
    # 否则 "处理完成, no error"、"error_count=0" 这类正文会被误判成 ERROR。
    head = window.lstrip().lstrip("[\"(\").lstrip()")
    upper = head.upper()
    for keyword, level in (
        ("ERROR", "ERROR"),
        ("FAILED", "ERROR"),
        ("FAIL", "ERROR"),
        ("WARNING", "WARN"),
        ("WARN", "WARN"),
        ("DEBUG", "DEBUG"),
    ):
        if not upper.startswith(keyword):
            continue
        rest = head[len(keyword) :]
        if rest[:1].isalnum() or rest[:1] == "_":
            continue
        return level
    return ""


class LineNumberArea(QWidget):
    """日志行号槽。"""

    def __init__(self, editor: "LogView"):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 命名约定
        return QSize(self._editor.gutter_width(), 0)

    def paintEvent(self, event) -> None:  # noqa: N802
        self._editor.paint_gutter(event)


class LogView(QPlainTextEdit):
    """带行号与级别配色的日志视图。"""

    rowClicked = Signal(int)
    commandModeRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("logView")
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setFrameStyle(0)
        self.setFont(theme.mono_font(12))
        self.setCursorWidth(1)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self._line_number = 0
        self._timestamps = True
        self._max_lines = 20000
        self._selected_block = -1
        self._find_matches: list[tuple[int, int]] = []
        self._find_current: tuple[int, int] | None = None
        self._has_lines = False
        self._gutter = LineNumberArea(self)

        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._on_update_request)
        self.set_max_lines(self._max_lines)
        self._update_gutter_width()

    # ------------------------------------------------------------------
    # 设置
    # ------------------------------------------------------------------
    def set_max_lines(self, count: int) -> None:
        self._max_lines = max(1, int(count))
        self.document().setMaximumBlockCount(self._max_lines)

    def set_timestamps(self, enabled: bool) -> None:
        self._timestamps = bool(enabled)

    def timestamps(self) -> bool:
        return self._timestamps

    def line_number(self) -> int:
        return self._line_number

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def append_log(self, text: str, timestamp: str = "") -> str:
        """追加一条设备日志，返回识别出的级别。"""
        level = detect_level(text)
        stick = self._at_bottom()
        self._write_device_line(self._end_cursor(), text, timestamp, level)
        self._scroll_if_sticky(stick)
        return level

    def append_logs(self, entries: list[tuple[str, str]]) -> None:
        """批量追加设备日志：共用一个编辑块、只刷新一次滚动条。"""
        if not entries:
            return
        stick = self._at_bottom()
        cursor = self._end_cursor()
        cursor.beginEditBlock()
        try:
            for text, timestamp in entries:
                self._write_device_line(cursor, text, timestamp, detect_level(text))
        finally:
            cursor.endEditBlock()
        self._scroll_if_sticky(stick)

    def append_system(self, text: str, timestamp: str = "") -> None:
        """追加一条本地状态消息（连接、重连、错误提示等）。"""
        stick = self._at_bottom()
        self._write_system_line(self._end_cursor(), text, timestamp)
        self._scroll_if_sticky(stick)

    def clear_log(self) -> None:
        self._line_number = 0
        self._selected_block = -1
        self._find_matches = []
        self._find_current = None
        self._has_lines = False
        self.setExtraSelections([])
        self.clear()

    # ------------------------------------------------------------------
    def _at_bottom(self) -> bool:
        scrollbar = self.verticalScrollBar()
        return scrollbar.value() >= scrollbar.maximum() - 2

    def _scroll_if_sticky(self, stick: bool) -> None:
        # 只在用户本来就贴底时自动滚动，避免打断向上翻阅
        if stick:
            scrollbar = self.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def _end_cursor(self) -> QTextCursor:
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.End)
        return cursor

    def _begin_block(self, cursor: QTextCursor, level: str) -> None:
        block_format = QTextBlockFormat()
        if level == "ERROR":
            block_format.setBackground(QColor(*theme.ERROR_ROW_RGBA))
        elif level == "WARN":
            block_format.setBackground(QColor(*theme.WARN_ROW_RGBA))

        if self._has_lines:
            cursor.insertBlock(block_format)
        else:
            # 复用文档初始的空块，避免开头多一个空行
            cursor.setBlockFormat(block_format)
            self._has_lines = True
        self._line_number += 1
        # 行号跟着块一起存，裁剪旧行后编号仍然是绝对值
        cursor.block().setUserState(self._line_number)

    def _write_device_line(
        self, cursor: QTextCursor, text: str, timestamp: str, level: str
    ) -> None:
        """写一条设备日志（级别标记 + 时间戳 + 级别 + 消息）。"""
        self._begin_block(cursor, level)
        if level:
            marker = QTextCharFormat()
            marker.setForeground(QColor(theme.LEVEL_COLORS[level]))
            cursor.insertText("\u258c", marker)
        if timestamp and self._timestamps:
            stamp_format = QTextCharFormat()
            stamp_format.setForeground(QColor(theme.TEXT_DIM))
            cursor.insertText(f"{timestamp} ", stamp_format)
        if level:
            level_format = QTextCharFormat()
            level_format.setForeground(QColor(theme.LEVEL_COLORS[level]))
            level_format.setFontWeight(QFont.Bold)
            cursor.insertText(f"{level:<5} ", level_format)
        message_format = QTextCharFormat()
        message_format.setForeground(QColor(theme.LEVEL_COLORS.get(level, theme.TEXT)))
        cursor.setCharFormat(message_format)
        cursor.insertHtml(ansi_to_html(text))

    def _write_system_line(self, cursor: QTextCursor, text: str, timestamp: str) -> None:
        self._begin_block(cursor, "")
        marker = QTextCharFormat()
        marker.setForeground(QColor(theme.TEXT_FAINT))
        cursor.insertText("\u00b7 ", marker)
        if timestamp and self._timestamps:
            stamp_format = QTextCharFormat()
            stamp_format.setForeground(QColor(theme.TEXT_FAINT))
            cursor.insertText(f"{timestamp} ", stamp_format)
        text_format = QTextCharFormat()
        text_format.setForeground(QColor(theme.TEXT_DIM))
        text_format.setFontItalic(True)
        cursor.setCharFormat(text_format)
        cursor.insertText(text)

    # ------------------------------------------------------------------
    # 行选中
    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        super().mousePressEvent(event)
        cursor = self.cursorForPosition(event.pos())
        self.select_block(cursor.blockNumber())

    def select_block(self, block_number: int, emit: bool = True) -> None:
        document = self.document()
        block = document.findBlockByNumber(block_number)
        if not block.isValid():
            return

        self._selected_block = block_number
        self._refresh_extra_selections()
        if emit:
            self.rowClicked.emit(block_number + 1)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() == Qt.NoModifier:
            self.commandModeRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def highlight_matches(
        self, matches: list[tuple[int, int]], current: tuple[int, int] | None
    ) -> None:
        """高亮全部查找匹配项，并用更深背景标出当前项。"""
        self._find_matches = [
            (start, end)
            for start, end in matches
            if 0 <= start < end <= self.document().characterCount()
        ]
        self._find_current = current if current in self._find_matches else None
        self._refresh_extra_selections()

    def clear_find_highlights(self) -> None:
        self._find_matches = []
        self._find_current = None
        self._refresh_extra_selections()

    def _refresh_extra_selections(self) -> None:
        """合并行选中与查找高亮，避免二者互相覆盖。"""
        selection = QTextEdit.ExtraSelection()
        block = self.document().findBlockByNumber(self._selected_block)
        cursor = QTextCursor(block) if block.isValid() else QTextCursor(self.document())
        cursor.select(QTextCursor.LineUnderCursor)
        selection.cursor = cursor
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(34, 211, 238, 36))
        fmt.setProperty(QTextCharFormat.FullWidthSelection, True)
        selection.format = fmt

        selections = [selection] if block.isValid() else []
        for match in self._find_matches:
            start, end = match
            cursor = QTextCursor(self.document())
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            find_selection = QTextEdit.ExtraSelection()
            find_selection.cursor = cursor
            find_fmt = QTextCharFormat()
            alpha = 245 if match == self._find_current else 190
            find_fmt.setBackground(QColor(250, 204, 21, alpha))
            find_selection.format = find_fmt
            selections.append(find_selection)
        self.setExtraSelections(selections)

    # ------------------------------------------------------------------
    # 行号槽
    # ------------------------------------------------------------------
    def gutter_width(self) -> int:
        digits = max(3, len(str(max(self._line_number, 1))))
        return 14 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_gutter_width(self, _count: int = 0) -> None:
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _on_update_request(self, rect: QRect, dy: int) -> None:
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        area = self.contentsRect()
        self._gutter.setGeometry(
            QRect(area.left(), area.top(), self.gutter_width(), area.height())
        )

    def paint_gutter(self, event) -> None:
        painter = QPainter(self._gutter)
        painter.fillRect(event.rect(), QColor(theme.BG_APP))

        block = self.firstVisibleBlock()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())
        height = self.fontMetrics().height()

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.setPen(QColor(theme.TEXT_FAINT))
                painter.drawText(
                    0,
                    top,
                    self._gutter.width() - 8,
                    height,
                    Qt.AlignRight,
                    str(block.userState() or block.blockNumber() + 1),
                )
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
        painter.end()
