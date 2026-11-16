"""同比环比计算引擎：不依赖仓库，输入配置、指标版本与窗口聚合记录，输出可回放的快照。

版本歧义的处理（本实现的选择）
------------------------------------------------------------------
基准周期与新旧指标版本同时重叠时，有两种常见做法：

A. 拼接：按日期把重叠区间拆给各自版本，再把两段数值相加/加权。
B. 单一版本：要求整个周期归属同一个指标版本，取“覆盖整个周期的最新版本”，
   其余重叠版本只作为备选记录；找不到完整覆盖版本时判为不可比。

这里选 B。原因是对比结果必须能回答“这个增长率到底用的是哪一版指标、哪一段数据”，
拼接会把两个口径的数字合成一个没有单一来源的值，事后无法复盘；而按 B 得到的结果
即使存在版本冲突，也只有一个基准快照、一个版本裁决和一组备选版本，全部可追溯。
代价是跨版本的周期会显示“不可比”而不是给一个近似值。
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from app.services.periods import Period, PeriodError, parse_offset, parse_period, shift_period

REASON_NO_VERSION = "基准周期没有可用的指标版本"
REASON_SPAN_VERSION = "周期跨越指标版本，无法归属单一版本"
REASON_BASELINE_GAP = "缺少基准窗口聚合记录"
REASON_CURRENT_GAP = "缺少当期窗口聚合记录"
REASON_STALE = "数据新鲜度不足"
REASON_NO_BASELINE_VALUE = "基准值为 0，增长率不可计算"

COMPARISON_TYPES = ("同比", "环比")
DISPLAY_FORMATS = ("百分比(两位小数)", "百分比(一位小数)", "绝对值(两位小数)", "原始值")


def _iso_day(value: date) -> str:
    return value.isoformat()


def _parse_day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def parse_moment(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _version_covers(version: dict[str, Any], period: Period) -> bool:
    start = _parse_day(version.get("生效起"))
    end = _parse_day(version.get("生效止"))
    if start is None:
        return False
    return start <= period.start and (end is None or end >= period.end)


def _version_overlaps(version: dict[str, Any], period: Period) -> bool:
    start = _parse_day(version.get("生效起"))
    end = _parse_day(version.get("生效止"))
    if start is None:
        return False
    return start <= period.end and (end is None or end >= period.start)


def _version_label(version: dict[str, Any]) -> str:
    return f"v{version.get('版本号')}({version.get('生效起')}~{version.get('生效止') or '至今'})"


def resolve_version(
    versions: list[dict[str, Any]],
    period: Period,
    pinned_version: int | None = None,
) -> tuple[dict[str, Any] | None, str | None, list[dict[str, Any]], str]:
    """为一个周期选出唯一的指标版本，返回 (版本, 不可比原因, 备选版本, 裁决说明)。"""
    candidates = [item for item in versions if _version_overlaps(item, period)]
    if pinned_version is not None:
        pinned = [item for item in candidates if int(item.get("版本号", 0)) == pinned_version]
        if not pinned:
            return None, f"配置指定的指标版本 v{pinned_version} 未覆盖周期 {period.label}", [], ""
        candidates = pinned

    covering = [item for item in candidates if _version_covers(item, period)]
    if not covering:
        if candidates:
            labels = "、".join(_version_label(item) for item in sorted(candidates, key=lambda x: int(x.get("版本号", 0))))
            return None, f"{REASON_SPAN_VERSION}：{period.label} 涉及 {labels}", candidates, ""
        return None, f"{REASON_NO_VERSION}（{period.label}）", [], ""

    covering.sort(key=lambda item: (str(item.get("生效起") or ""), int(item.get("版本号", 0))), reverse=True)
    chosen = covering[0]
    alternatives = covering[1:]
    if alternatives:
        decision = (
            f"周期 {period.label} 同时被 {len(covering)} 个指标版本覆盖，"
            f"取覆盖该周期的最新版本 v{chosen.get('版本号')}，"
            f"备选版本 {'、'.join('v' + str(item.get('版本号')) for item in alternatives)} 不参与计算"
        )
    else:
        decision = f"周期 {period.label} 唯一归属指标版本 v{chosen.get('版本号')}"
    return chosen, None, alternatives, decision


def _dedupe_windows(rows: Iterable[dict[str, Any]]) -> tuple[dict[date, dict[str, Any]], int]:
    """同一个窗口日只取生成时间最新的一条，返回 (按日索引, 被去重条数)。"""
    picked: dict[date, dict[str, Any]] = {}
    duplicates = 0
    for row in rows:
        day = _parse_day(row.get("窗口起"))
        if day is None:
            continue
        previous = picked.get(day)
        if previous is None:
            picked[day] = row
            continue
        duplicates += 1
        if str(row.get("记录生成时间") or "") >= str(previous.get("记录生成时间") or ""):
            picked[day] = row
    return picked, duplicates


def _period_windows(
    windows: list[dict[str, Any]],
    metric_code: str,
    version_no: int | None,
    period: Period,
) -> tuple[list[dict[str, Any]], int]:
    if version_no is None:
        return [], 0
    scoped = [
        row for row in windows
        if str(row.get("指标编号")) == metric_code
        and int(row.get("指标版本", 0)) == version_no
        and (day := _parse_day(row.get("窗口起"))) is not None
        and period.start <= day <= period.end
    ]
    picked, duplicates = _dedupe_windows(scoped)
    return [picked[day] for day in sorted(picked)], duplicates


def _missing_days(records: list[dict[str, Any]], period: Period) -> list[str]:
    present = {_parse_day(row.get("窗口起")) for row in records}
    missing: list[str] = []
    cursor = period.start
    while cursor <= period.end:
        if cursor not in present:
            missing.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return missing


def latest_data_point(windows: list[dict[str, Any]], metric_code: str) -> datetime | None:
    moments = [
        parse_moment(row.get("数据时点"))
        for row in windows
        if str(row.get("指标编号")) == metric_code
    ]
    moments = [moment for moment in moments if moment is not None]
    return max(moments) if moments else None


def freshness_seconds(latest: datetime | None, as_of: datetime) -> int | None:
    if latest is None:
        return None
    return max(int((as_of - latest).total_seconds()), 0)


def format_growth(growth: float | None, display_format: str) -> str:
    if growth is None:
        return "—"
    if display_format == "百分比(一位小数)":
        return f"{growth:+.1f}%"
    if display_format == "绝对值(两位小数)":
        return f"{growth:+.2f}"
    if display_format == "原始值":
        return f"{growth:+.4g}"
    return f"{growth:+.2f}%"


def format_difference(difference: float | None, display_format: str) -> str:
    if difference is None:
        return "—"
    if display_format == "百分比(一位小数)":
        return f"{difference:+.1f}"
    if display_format == "原始值":
        return f"{difference:+.4g}"
    return f"{difference:+.2f}"


def snapshot_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def compute_snapshot(
    config: dict[str, Any],
    versions: list[dict[str, Any]],
    windows: list[dict[str, Any]],
    *,
    as_of: datetime,
    snapshot_code: str,
    source: str = "计算",
) -> dict[str, Any]:
    """按基准周期计算同比环比差异，输出一份可落库、可导出的基准快照。

    不可比时数值字段为 None，原因写入 `不可比原因`；不会退化成“沿用上次数字”。
    """
    config_id = config.get("id")
    config_code = str(config.get("配置编号") or "")
    metric_code = str(config.get("目标指标") or "").strip()
    comparison_type = str(config.get("对比类型") or "").strip()
    display_format = str(config.get("显示格式") or "百分比(两位小数)").strip()
    pinned = config.get("指标版本")
    pinned_version = int(pinned) if str(pinned or "").strip().isdigit() else None
    try:
        offset = parse_offset(config.get("偏移量"))
    except PeriodError as exc:
        offset = 1
        offset_error = str(exc)
    else:
        offset_error = ""

    base_period = parse_period(str(config.get("基准周期") or ""))
    current_period = shift_period(base_period, offset)
    metric_versions = [item for item in versions if str(item.get("指标编号")) == metric_code]

    base_version, base_reason, base_alts, base_decision = resolve_version(metric_versions, base_period, pinned_version)
    current_version, current_reason, current_alts, current_decision = resolve_version(
        metric_versions, current_period, pinned_version
    )

    base_records, base_dupes = _period_windows(windows, metric_code, base_version and int(base_version.get("版本号", 0)), base_period)
    current_records, current_dupes = _period_windows(
        windows, metric_code, current_version and int(current_version.get("版本号", 0)), current_period
    )

    latest = latest_data_point(windows, metric_code)
    freshness = freshness_seconds(latest, as_of)
    limit_raw = config.get("新鲜度上限秒")
    try:
        freshness_limit = int(str(limit_raw).strip())
    except (TypeError, ValueError):
        freshness_limit = 86400 * 7

    notes: list[str] = []
    reasons: list[str] = []
    if offset_error:
        reasons.append(offset_error)
    if comparison_type not in COMPARISON_TYPES:
        reasons.append(f"对比类型「{comparison_type or '空'}」不是同比或环比")
    if base_reason:
        reasons.append(f"基准周期：{base_reason}")
    if current_reason:
        reasons.append(f"当期周期：{current_reason}")

    base_missing = _missing_days(base_records, base_period) if base_version else []
    current_missing = _missing_days(current_records, current_period) if current_version else []
    if not base_reason and base_missing:
        reasons.append(
            f"{REASON_BASELINE_GAP}：{base_period.label} 缺 {len(base_missing)} 天"
            f"（如 {base_missing[0]}）"
        )
    if not current_reason and current_missing:
        reasons.append(
            f"{REASON_CURRENT_GAP}：{current_period.label} 缺 {len(current_missing)} 天"
            f"（如 {current_missing[0]}）"
        )
    if freshness is not None and freshness > freshness_limit:
        reasons.append(
            f"{REASON_STALE}：{metric_code} 最新数据时点 {latest.isoformat()}，"
            f"滞后 {freshness} 秒，超过配置上限 {freshness_limit} 秒"
        )
    elif freshness is None and not base_missing and not current_missing:
        reasons.append(f"{REASON_STALE}：{metric_code} 没有任何指标窗口聚合记录")

    base_total = round(sum(float(row.get("聚合值", 0)) for row in base_records), 4) if base_records else None
    current_total = round(sum(float(row.get("聚合值", 0)) for row in current_records), 4) if current_records else None
    difference = round(current_total - base_total, 4) if base_total is not None and current_total is not None else None
    growth: float | None = None
    if difference is not None and base_total:
        growth = round(difference / base_total * 100, 4)
    elif difference is not None and base_total == 0:
        notes.append(REASON_NO_BASELINE_VALUE)

    try:
        threshold = float(str(config.get("差异阈值")).strip()) if str(config.get("差异阈值") or "").strip() else None
    except ValueError:
        threshold = None
        notes.append(f"差异阈值「{config.get('差异阈值')}」不是数字，本次未做阈值判定")
    exceeded = bool(growth is not None and threshold is not None and abs(growth) >= threshold)

    if base_dupes:
        notes.append(f"基准周期有 {base_dupes} 条重复窗口记录，已按记录生成时间取最新")
    if current_dupes:
        notes.append(f"当期周期有 {current_dupes} 条重复窗口记录，已按记录生成时间取最新")
    if base_version and current_version and int(base_version.get("版本号", 0)) != int(current_version.get("版本号", 0)):
        notes.append(
            f"基准与当期指标版本不同（v{base_version.get('版本号')} → v{current_version.get('版本号')}），"
            "差异包含口径变更"
        )

    primary_reason = reasons[0] if reasons else None
    snapshot = {
        "快照编号": snapshot_code,
        "配置id": config_id,
        "配置编号": config_code,
        "配置修订号": int(config.get("配置修订号", 1) or 1),
        "业务键": config_code,
        "目标指标": metric_code,
        "指标名称": str(config.get("指标名称") or ""),
        "对比类型": comparison_type,
        "基准周期": base_period.label,
        "偏移量": offset,
        "当期周期": current_period.label,
        "周期粒度": base_period.unit,
        "基准周期起": _iso_day(base_period.start),
        "基准周期止": _iso_day(base_period.end),
        "当期周期起": _iso_day(current_period.start),
        "当期周期止": _iso_day(current_period.end),
        "基准指标版本": int(base_version.get("版本号", 0)) if base_version else None,
        "当期指标版本": int(current_version.get("版本号", 0)) if current_version else None,
        "基准版本裁决": base_decision,
        "当期版本裁决": current_decision,
        "版本冲突": bool(base_alts or current_alts),
        "备选版本": [int(item.get("版本号", 0)) for item in base_alts],
        "基准聚合记录数": len(base_records),
        "当期聚合记录数": len(current_records),
        "基准值": base_total,
        "当期值": current_total,
        "差异": difference,
        "增长率": growth,
        "增长率文本": format_growth(growth, display_format),
        "差异文本": format_difference(difference, display_format),
        "差异阈值": threshold,
        "显示格式": display_format,
        "是否超阈值": exceeded,
        "新鲜度秒": freshness,
        "新鲜度上限秒": freshness_limit,
        "最新数据时点": latest.isoformat() if latest else None,
        "不可比原因": primary_reason,
        "全部不可比原因": reasons,
        "口径提示": notes,
        "基准窗口记录": [
            {
                "id": row.get("id"),
                "窗口起": row.get("窗口起"),
                "窗口止": row.get("窗口止"),
                "指标版本": row.get("指标版本"),
                "聚合值": row.get("聚合值"),
            }
            for row in base_records
        ],
        "当期窗口记录": [
            {
                "id": row.get("id"),
                "窗口起": row.get("窗口起"),
                "窗口止": row.get("窗口止"),
                "指标版本": row.get("指标版本"),
                "聚合值": row.get("聚合值"),
            }
            for row in current_records
        ],
        "快照来源": source,
        "生成时间": as_of.isoformat(),
        "观测时间": as_of.isoformat(),
        "配置修订": {
            "目标指标": metric_code,
            "对比类型": comparison_type,
            "基准周期": str(config.get("基准周期") or ""),
            "偏移量": config.get("偏移量"),
            "差异阈值": config.get("差异阈值"),
            "显示格式": display_format,
            "新鲜度上限秒": freshness_limit,
        },
        "计算公式": "增长率 = (当期聚合值 - 基准聚合值) / 基准聚合值 × 100%",
    }
    trace = {
        "快照编号": snapshot_code,
        "指标版本": snapshot["基准指标版本"],
        "当期指标版本": snapshot["当期指标版本"],
        "基准聚合记录": [row.get("id") for row in base_records],
        "当期聚合记录": [row.get("id") for row in current_records],
        "数据时点": snapshot["最新数据时点"],
        "新鲜度秒": freshness,
        "新鲜度上限秒": freshness_limit,
        "版本裁决": base_decision,
        "版本备选": [int(item.get("版本号", 0)) for item in base_alts],
        "不可比原因": primary_reason,
        "全部不可比原因": reasons,
        "口径提示": notes,
        "计算公式": snapshot["计算公式"],
        "基准周期": [snapshot["基准周期起"], snapshot["基准周期止"]],
        "当期周期": [snapshot["当期周期起"], snapshot["当期周期止"]],
    }
    snapshot["追溯"] = trace
    snapshot["快照指纹"] = snapshot_fingerprint(
        {
            "配置编号": config_code,
            "目标指标": metric_code,
            "对比类型": comparison_type,
            "基准周期": base_period.label,
            "当期周期": current_period.label,
            "基准版本": snapshot["基准指标版本"],
            "当期版本": snapshot["当期指标版本"],
            "基准记录": trace["基准聚合记录"],
            "当期记录": trace["当期聚合记录"],
            "as_of": as_of.isoformat(),
            "source": source,
        }
    )
    return snapshot
