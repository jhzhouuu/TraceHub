"""时间戳格式化工具。"""

from datetime import datetime

# 与旧版工具保持一致：完整日期 + 毫秒
DEFAULT_TIME_FORMAT = "%Y-%m-%d %H:%M:%S.%f"


def timestamp_text(fmt: str = DEFAULT_TIME_FORMAT, brackets: bool = True) -> str:
    """返回当前时间格式化的时间戳，默认保留到毫秒。"""
    text = datetime.now().strftime(fmt)
    if fmt.endswith("%f"):
        text = text[:-3]
    return f"[{text}]" if brackets else text
