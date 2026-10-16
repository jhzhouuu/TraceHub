"""查找对话框：右上角显示匹配进度（如 1/23），支持回车查找。"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class _DragHeader(QWidget):
    """无边框弹窗的标题行，可拖动窗口。"""

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
                event.accept()
                return
        super().mousePressEvent(event)


class FindDialog(QDialog):
    """在日志视图中查找文本。"""

    def __init__(self, editor: QPlainTextEdit, parent=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setWindowTitle("查找")
        self.setModal(False)
        self.editor = editor

        self._matches: list[tuple[int, int]] = []
        self._current = 0
        self._count_timer = QTimer(self)
        self._count_timer.setSingleShot(True)
        self._count_timer.timeout.connect(self.recount)
        self._immediate_timer = QTimer(self)
        self._immediate_timer.setSingleShot(True)
        self._immediate_timer.setInterval(0)
        self._immediate_timer.timeout.connect(self._refresh_after_document_change)
        self.editor.document().contentsChanged.connect(self._on_document_changed)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_header())
        outer.addWidget(self._build_body())

    # ------------------------------------------------------------------
    def _build_header(self) -> QWidget:
        header = _DragHeader(self)
        header.setObjectName("dialogHead")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(14, 9, 8, 9)
        layout.setSpacing(8)

        title = QLabel("查找")
        title.setObjectName("dialogTitle")
        self.counter_label = QLabel("")
        self.counter_label.setObjectName("hintLabel")

        self.close_button = QPushButton("\u2715")
        self.close_button.setObjectName("dialogClose")
        self.close_button.setFixedSize(22, 22)
        self.close_button.setCursor(Qt.PointingHandCursor)
        self.close_button.setToolTip("关闭")
        self.close_button.clicked.connect(self.close)

        layout.addWidget(title)
        layout.addStretch(1)
        layout.addWidget(self.counter_label)
        layout.addWidget(self.close_button)
        return header

    def _build_body(self) -> QWidget:
        body = QWidget()
        layout = QGridLayout(body)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(10)

        self.find_edit = QLineEdit(body)
        self.find_edit.setPlaceholderText("输入要查找的文本")
        self.find_edit.setMinimumWidth(260)
        self.find_edit.returnPressed.connect(self.find_next)
        self.find_edit.textChanged.connect(lambda _text: self._count_timer.start(200))

        self.whole_check = QCheckBox("全词匹配", body)
        self.case_check = QCheckBox("区分大小写", body)
        self.whole_check.toggled.connect(lambda _on: self.recount())
        self.case_check.toggled.connect(lambda _on: self.recount())

        self.first_button = QPushButton("顶部", body)
        self.first_button.setObjectName("dialogButton")
        self.last_button = QPushButton("底部", body)
        self.last_button.setObjectName("dialogButton")
        self.previous_button = QPushButton("上一个", body)
        self.previous_button.setObjectName("dialogButton")
        self.next_button = QPushButton("下一个", body)
        self.next_button.setObjectName("dialogButton")
        self.find_button = QPushButton("查找", body)
        self.find_button.setObjectName("primaryButton")
        for button in (
            self.first_button,
            self.last_button,
            self.previous_button,
            self.next_button,
            self.find_button,
        ):
            button.setCursor(Qt.PointingHandCursor)
        self.first_button.setToolTip("跳转到第一个匹配项")
        self.last_button.setToolTip("跳转到最后一个匹配项")
        self.first_button.clicked.connect(self.find_first)
        self.last_button.clicked.connect(self.find_last)
        self.previous_button.clicked.connect(self.find_previous)
        self.next_button.clicked.connect(self.find_next)
        self.find_button.clicked.connect(self.find_next)

        self.status_label = QLabel("", body)
        self.status_label.setObjectName("hintLabel")

        layout.addWidget(QLabel("查找内容"), 0, 0)
        layout.addWidget(self.find_edit, 0, 1, 1, 5)
        layout.addWidget(self.whole_check, 1, 1)
        layout.addWidget(self.case_check, 1, 2)
        layout.addWidget(self.first_button, 2, 1)
        layout.addWidget(self.last_button, 2, 2)
        layout.addWidget(self.previous_button, 2, 3)
        layout.addWidget(self.next_button, 2, 4)
        layout.addWidget(self.find_button, 2, 5)
        layout.addWidget(self.status_label, 3, 1, 1, 5)
        self.setColumnStretch(1, 1)
        return body

    # ------------------------------------------------------------------
    # 匹配统计
    # ------------------------------------------------------------------
    def _pattern(self) -> re.Pattern | None:
        term = self.find_edit.text()
        if not term:
            return None
        pattern = re.escape(term)
        if self.whole_check.isChecked():
            pattern = rf"\b{pattern}\b"
        flags = 0 if self.case_check.isChecked() else re.IGNORECASE
        return re.compile(pattern, flags)

    def recount(self) -> None:
        """重算匹配总数，并按光标位置得出当前是第几个。"""
        pattern = self._pattern()
        if pattern is None:
            self._matches = []
            self._current = 0
            self._update_counter()
            self.editor.clear_find_highlights()
            return

        text = self.editor.toPlainText()
        self._matches = [(match.start(), match.end()) for match in pattern.finditer(text)]
        position = self.editor.textCursor().selectionStart()
        current = 0
        for index, (start, _end) in enumerate(self._matches, start=1):
            if start <= position:
                current = index
        self._current = current
        current_match = self._matches[current - 1] if current > 0 else None
        self.editor.highlight_matches(self._matches, current_match)
        self._update_counter()

    def _update_counter(self) -> None:
        if not self.find_edit.text():
            self.counter_label.setText("")
            return
        total = len(self._matches)
        if total == 0:
            self.counter_label.setText("0/0")
            return
        self.counter_label.setText(f"{max(1, self._current)}/{total}")

    def _on_document_changed(self) -> None:
        # contentsChanged 在一次插入过程中会触发多次，立即读文档可能还不完整。
        # 先排到下一个事件循环刷新，再用 200ms 节流合并持续追加的新日志。
        if not self.isVisible() or self._count_timer.isActive():
            return
        if not self._immediate_timer.isActive():
            self._immediate_timer.start()

    def _refresh_after_document_change(self) -> None:
        self._immediate_timer.stop()
        self.recount()
        self._count_timer.start(200)

    # ------------------------------------------------------------------
    # 查找动作
    # ------------------------------------------------------------------
    def _flags(self, backward: bool) -> QTextDocument.FindFlag:
        flags = QTextDocument.FindFlags()
        if self.case_check.isChecked():
            flags |= QTextDocument.FindCaseSensitively
        if self.whole_check.isChecked():
            flags |= QTextDocument.FindWholeWords
        if backward:
            flags |= QTextDocument.FindBackward
        return flags

    def _search(self, backward: bool) -> None:
        text = self.find_edit.text()
        if not text:
            return
        try:
            cursor = self.editor.textCursor()
            start = cursor.selectionStart() if backward else cursor.selectionEnd()

            found = self.editor.document().find(text, start, self._flags(backward))
            if found.isNull():
                wrapped = self.editor.document().characterCount() if backward else 0
                found = self.editor.document().find(text, wrapped, self._flags(backward))

            if found.isNull():
                self.status_label.setText("未找到更多匹配项")
                self.recount()
                return

            self.status_label.setText("")
            self.editor.setTextCursor(found)
            self.editor.ensureCursorVisible()
            self.recount()
        except Exception as exc:  # noqa: BLE001 - 查找功能发生异常
            self.status_label.setText(f"查找错误：{exc}")

    def find_next(self) -> None:
        self._search(backward=False)

    def find_previous(self) -> None:
        self._search(backward=True)

    def _jump_to_match(self, index: int) -> None:
        if not self.find_edit.text():
            return
        if not self._matches:
            self.recount()
        if not self._matches:
            self.status_label.setText("未找到匹配项")
            self._update_counter()
            return

        start, end = self._matches[index]
        cursor = QTextCursor(self.editor.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        self.status_label.setText("")
        self.editor.setTextCursor(cursor)
        self.editor.ensureCursorVisible()
        self.recount()

    def find_first(self) -> None:
        self._jump_to_match(0)

    def find_last(self) -> None:
        self._jump_to_match(-1)

    def show_for(self, text: str = "") -> None:
        """打开对话框并聚焦到查找输入框。"""
        if text:
            self.find_edit.setText(text)
        self.show()
        self.raise_()
        self.activateWindow()
        self.find_edit.setFocus()
        self.find_edit.selectAll()
        self.recount()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.editor.clear_find_highlights()
        self._count_timer.stop()
        self._immediate_timer.stop()
        super().closeEvent(event)
