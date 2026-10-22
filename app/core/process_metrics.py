"""本进程运行指标：在线用户、自然日 API 调用量、知识库存储用量。

单实例验收；不汇总多副本。自然日按本机本地日历日切分。
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from pathlib import Path

_lock = threading.Lock()
_day: date | None = None
_request_count = 0
# user_id -> 最近一次合法鉴权成功的单调时刻（datetime.now）
_online: dict[str, datetime] = {}
_ONLINE_WINDOW = timedelta(minutes=15)


def _roll_day(now: datetime) -> None:
    global _day, _request_count
    today = now.date()
    if _day != today:
        _day = today
        _request_count = 0


def record_api_request() -> None:
    """统计本进程自然日内的 API 请求次数。"""
    global _request_count
    now = datetime.now()
    with _lock:
        _roll_day(now)
        _request_count += 1


def record_authenticated_user(user_id: str) -> None:
    """合法鉴权成功后刷新在线窗口。"""
    if not user_id:
        return
    now = datetime.now()
    with _lock:
        _online[user_id] = now


def online_user_count(*, now: datetime | None = None) -> int:
    """过去 15 分钟内有合法 API 鉴权的去重用户数。"""
    moment = now or datetime.now()
    cutoff = moment - _ONLINE_WINDOW
    with _lock:
        stale = [uid for uid, seen in _online.items() if seen < cutoff]
        for uid in stale:
            del _online[uid]
        return len(_online)


def api_request_count_today(*, now: datetime | None = None) -> int:
    """本进程当前自然日的 API 请求数。"""
    moment = now or datetime.now()
    with _lock:
        _roll_day(moment)
        return _request_count


def knowledge_storage_bytes(root: Path | None = None) -> int:
    """知识库源文件目录占用的字节数。目录不存在时为 0。"""
    from app.rag.document_storage import knowledge_storage_root

    base = root if root is not None else knowledge_storage_root()
    if not base.exists():
        return 0
    total = 0
    for path in base.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


def reset_for_tests() -> None:
    """测试隔离：清空进程内计数。"""
    global _day, _request_count
    with _lock:
        _day = None
        _request_count = 0
        _online.clear()
