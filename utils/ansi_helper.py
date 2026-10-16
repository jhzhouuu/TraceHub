"""ANSI 转义序列处理。

原始工具会把设备输出的 ANSI 颜色代码渲染成彩色文本，目前仅支持基础的前景色。
"""

import re

ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

# 基础前景色（30-37）与高亮前景色（90-97）
FOREGROUND_COLORS = {
    30: "#1e1e1e",
    31: "#e05561",
    32: "#8cc265",
    33: "#d18f00",
    34: "#4aa5f0",
    35: "#c162de",
    36: "#42b3c2",
    37: "#d7dae0",
    90: "#4d4d4d",
    91: "#ff616e",
    92: "#a5e075",
    93: "#f0a45d",
    94: "#4dc4ff",
    95: "#de8cff",
    96: "#4cd1e0",
    97: "#ffffff",
}


def strip_ansi(text: str) -> str:
    """去除字符串中的 ANSI 转义序列。"""
    return ESCAPE_RE.sub("", text)


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def ansi_to_html(text: str) -> str:
    """将包含 ANSI 颜色代码的文本转换为 HTML 格式。

    仅处理基础的前景色设置，其余控制序列会被丢弃。
    """
    result = []
    index = 0
    open_span = False
    for match in ESCAPE_RE.finditer(text):
        chunk = text[index:match.start()]
        if chunk:
            result.append(_escape_html(chunk))
        index = match.end()

        body = match.group()[2:-1]
        if not body or body.startswith("?"):
            continue
        codes = [part for part in body.split(";") if part != ""] or ["0"]
        for raw in codes:
            try:
                code = int(raw)
            except ValueError:
                continue
            if code == 0 or code == 39 or code == 22:
                if open_span:
                    result.append("</span>")
                    open_span = False
            elif code in FOREGROUND_COLORS:
                if open_span:
                    result.append("</span>")
                result.append(f'<span style="color:{FOREGROUND_COLORS[code]}">')
                open_span = True

    tail = text[index:]
    if tail:
        result.append(_escape_html(tail))
    if open_span:
        result.append("</span>")
    return "".join(result)
