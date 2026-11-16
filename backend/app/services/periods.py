"""基准周期解析与偏移：同比环比只接受可枚举、可回放的周期写法。

支持的写法：
    2026-10-16           日
    2026-10              月
    2026-W42             周（ISO 周，周一到周日）
    2026                 年
    2026-10-01~2026-10-15 显式区间（不支持偏移，只能作为固定基准）

基准周期一旦落库，就会被对比快照按原样冻结；这里不做“最近30天”之类的相对口径，
避免服务重启或时间推进后同一个配置算出不同的基准段。

偏移量的推进单位随基准周期粒度走：日按天、周按周、月按月、年按年；显式区间按天平移。
对比类型（同比 / 环比）只影响展示口径，不改变推进方式——同比通常配合“年”粒度或
12 个“月”粒度的偏移量，环比通常配合 1 个周期的偏移量。
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta


class PeriodError(ValueError):
    """基准周期写法不合法，或者偏移量无法应用到该周期。"""


@dataclass(frozen=True)
class Period:
    unit: str  # day / week / month / year / custom
    start: date
    end: date  # 含端点
    label: str

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end

    def to_dict(self) -> dict[str, str]:
        return {
            "周期粒度": self.unit,
            "周期标识": self.label,
            "周期起": self.start.isoformat(),
            "周期止": self.end.isoformat(),
        }


_DAY_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$", re.IGNORECASE)
_YEAR_RE = re.compile(r"^(\d{4})$")
_RANGE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})$")


def parse_period(text: str) -> Period:
    raw = str(text or "").strip()
    if not raw:
        raise PeriodError("基准周期不能为空")

    match = _RANGE_RE.match(raw)
    if match:
        start = date.fromisoformat(match.group(1))
        end = date.fromisoformat(match.group(2))
        if end < start:
            raise PeriodError(f"基准周期区间起点晚于终点：{raw}")
        return Period("custom", start, end, f"{start.isoformat()}~{end.isoformat()}")

    match = _DAY_RE.match(raw)
    if match:
        day = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        return Period("day", day, day, day.isoformat())

    match = _MONTH_RE.match(raw)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if not 1 <= month <= 12:
            raise PeriodError(f"基准周期月份不合法：{raw}")
        last = calendar.monthrange(year, month)[1]
        return Period("month", date(year, month, 1), date(year, month, last), f"{year:04d}-{month:02d}")

    match = _WEEK_RE.match(raw)
    if match:
        year, week = int(match.group(1)), int(match.group(2))
        try:
            start = date.fromisocalendar(year, week, 1)
        except ValueError as exc:
            raise PeriodError(f"基准周期周序号不合法：{raw}") from exc
        return Period("week", start, start + timedelta(days=6), f"{year:04d}-W{week:02d}")

    match = _YEAR_RE.match(raw)
    if match:
        year = int(match.group(1))
        return Period("year", date(year, 1, 1), date(year, 12, 31), f"{year:04d}")

    raise PeriodError(
        f"基准周期「{raw}」无法识别，请使用 2026-10-16、2026-10、2026-W42、2026 或 2026-10-01~2026-10-15"
    )


def _add_months(day: date, months: int) -> date:
    total = (day.year * 12 + day.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last))


def shift_period(period: Period, offset: int) -> Period:
    """把基准周期整体向前推 offset 个周期，得到当期区间。

    同比/环比都走同一条规则；对比类型只影响展示口径，不改变周期推进方式。
    显式区间没有“一个周期”的概念，不接受偏移。
    """
    if offset <= 0:
        raise PeriodError("偏移量必须是正整数")

    if period.unit == "custom":
        # 显式区间按天平移，保持区间长度不变。
        start = period.start + timedelta(days=offset)
        end = period.end + timedelta(days=offset)
        return Period("custom", start, end, f"{start.isoformat()}~{end.isoformat()}")

    if period.unit == "day":
        start = period.start + timedelta(days=offset)
        end = period.end + timedelta(days=offset)
        label = start.isoformat()
    elif period.unit == "week":
        start = period.start + timedelta(weeks=offset)
        end = period.end + timedelta(weeks=offset)
        iso = start.isocalendar()
        label = f"{iso.year:04d}-W{iso.week:02d}"
    elif period.unit == "month":
        start = _add_months(period.start, offset)
        last = calendar.monthrange(start.year, start.month)[1]
        end = date(start.year, start.month, last)
        label = f"{start.year:04d}-{start.month:02d}"
    elif period.unit == "year":
        start = date(period.start.year + offset, 1, 1)
        end = date(period.end.year + offset, 12, 31)
        label = f"{start.year:04d}"
    else:  # pragma: no cover - parse_period 已经兜住
        raise PeriodError(f"不支持的周期粒度：{period.unit}")

    return Period(period.unit, start, end, label)


def parse_offset(value: object) -> int:
    try:
        offset = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise PeriodError(f"偏移量「{value}」不是整数") from exc
    if offset <= 0:
        raise PeriodError("偏移量必须是正整数")
    return offset
