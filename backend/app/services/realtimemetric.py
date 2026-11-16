"""实时指标业务规则：定义维护 + 实时指标同步消费指标窗口聚合记录。

同步的数据流（按实际写入步骤）
------------------------------
1. 消费入口 `sync_windows()` 接收一批指标窗口聚合记录（上游墨迹：指标编号、窗口起止、
   指标版本、聚合值、数据时点、记录生成时间）。
2. 逐条校验并写入 `metric_window_aggregate`：业务键 = 指标编号 + 窗口起 + 指标版本，
   重复写入时比较记录生成时间，旧记录拒绝覆盖，新记录替换。
3. 把每个指标最新的窗口写入 `metric_live_state`，并核对窗口是否落在该指标版本的
   生效区间内；不在区间内的记录标记为「版本越界」并拒绝推进实时值。
4. 全部写入与状态记录在同一个事务副本里完成；任何一条校验失败整批回滚，
   `sync_journal` 里只留一条失败记录，不会出现写了一半的窗口。
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from app.store import StoreTransaction, store

MODULE = "realtimemetric"
REQUIRED_FIELDS = ["指标编号", "指标名称", "计算窗口秒"]
STATUS_ORDER = ["草稿", "定义评审中", "已上线", "已下线"]
ACTION_RULES = {"提交定义评审": "定义评审中", "上线指标": "已上线", "下线指标": "已下线"}
NEGATIVE_ACTIONS = ["下线指标"]

WINDOW_TABLE = "metric_window_aggregate"
LIVE_TABLE = "metric_live_state"
VERSION_TABLE = "metric_version"
SYNC_JOURNAL = "sync_journal"


def _moment(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


class RealtimemetricService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("指标编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"指标定义 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于实时指标可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"指标定义已{action}"

    # ------------------------------------------------------------------ 同步
    def _versions(self, txn: StoreTransaction, metric: str) -> list[dict[str, Any]]:
        return [row for row in txn.rows(VERSION_TABLE) if row.get("指标编号") == metric]

    def _version_covers(self, txn: StoreTransaction, metric: str, version: int, day: date) -> bool:
        for row in self._versions(txn, metric):
            if int(row.get("版本号", 0)) != version:
                continue
            start = _day(row.get("生效起"))
            end = _day(row.get("生效止"))
            return start is not None and start <= day and (end is None or day <= end)
        return False

    def sync_windows(self, records: list[dict[str, Any]], *, now: datetime | None = None) -> tuple[dict[str, Any] | None, str]:
        """消费一批指标窗口聚合记录；整批成功或整批回滚。"""
        moment = now or datetime.now(timezone.utc)
        if not isinstance(records, list) or not records:
            return None, "同步批次为空，未写入任何指标窗口聚合记录"

        txn = store.transaction()
        try:
            batch_id = max((int(row.get("批次号", 0)) for row in txn.rows(SYNC_JOURNAL)), default=0) + 1
            seen: set[tuple[str, str, int]] = set()
            inserted = 0
            replaced = 0
            skipped = 0
            touched_metrics: set[str] = set()
            issues: list[str] = []

            for index, raw in enumerate(records, start=1):
                if not isinstance(raw, dict):
                    raise ValueError(f"第 {index} 条不是对象")
                metric = str(raw.get("指标编号") or "").strip()
                start_day = _day(raw.get("窗口起"))
                try:
                    version = int(str(raw.get("指标版本")).strip())
                except (TypeError, ValueError):
                    version = 0
                try:
                    value = float(raw.get("聚合值"))
                except (TypeError, ValueError):
                    value = None
                data_time = _moment(raw.get("数据时点"))
                produced_at = _moment(raw.get("记录生成时间")) or data_time or moment

                if not metric:
                    raise ValueError(f"第 {index} 条缺少指标编号")
                if start_day is None:
                    raise ValueError(f"第 {index} 条窗口起「{raw.get('窗口起')}」不是日期")
                if version <= 0:
                    raise ValueError(f"第 {index} 条指标版本「{raw.get('指标版本')}」不合法")
                if value is None:
                    raise ValueError(f"第 {index} 条聚合值「{raw.get('聚合值')}」不是数字")
                if data_time is None:
                    raise ValueError(f"第 {index} 条数据时点「{raw.get('数据时点')}」不是时间")

                if not any(str(row.get("指标编号")) == metric for row in txn.rows(MODULE)):
                    raise ValueError(f"第 {index} 条的指标「{metric}」没有对应的指标定义")

                if not self._version_covers(txn, metric, version, start_day):
                    issues.append(
                        f"{metric} 窗口 {start_day.isoformat()} 声明的 v{version} 不在该版本生效区间内，已拒绝写入"
                    )
                    skipped += 1
                    continue

                key = (metric, start_day.isoformat(), version)
                if key in seen:
                    raise ValueError(f"第 {index} 条与同批次前面的记录重复：{metric} {start_day.isoformat()} v{version}")
                seen.add(key)

                existing = next(
                    (
                        row for row in txn.rows(WINDOW_TABLE)
                        if row.get("指标编号") == metric
                        and str(row.get("窗口起")) == start_day.isoformat()
                        and int(row.get("指标版本", 0)) == version
                    ),
                    None,
                )
                if existing is not None:
                    if str(existing.get("记录生成时间") or "") > produced_at.isoformat():
                        issues.append(
                            f"{metric} 窗口 {start_day.isoformat()} v{version} 已有更新的记录"
                            f"（{existing.get('记录生成时间')}），本次旧记录被拒绝"
                        )
                        skipped += 1
                        continue
                    existing["聚合值"] = value
                    existing["数据时点"] = data_time.isoformat()
                    existing["记录生成时间"] = produced_at.isoformat()
                    existing["窗口止"] = raw.get("窗口止") or existing.get("窗口止")
                    existing["状态"] = "已落库"
                    replaced += 1
                else:
                    txn.append(WINDOW_TABLE, {
                        "指标编号": metric,
                        "窗口起": start_day.isoformat(),
                        "窗口止": raw.get("窗口止") or start_day.isoformat(),
                        "指标版本": version,
                        "聚合值": value,
                        "数据时点": data_time.isoformat(),
                        "记录生成时间": produced_at.isoformat(),
                        "来源流": raw.get("来源流") or "metric-window-aggregate",
                        "状态": "已落库",
                    })
                    inserted += 1
                touched_metrics.add(metric)

            for metric in sorted(touched_metrics):
                windows = [
                    row for row in txn.rows(WINDOW_TABLE) if row.get("指标编号") == metric
                ]
                if not windows:
                    continue
                latest = max(windows, key=lambda row: (str(row.get("窗口起")), str(row.get("记录生成时间"))))
                state = next(
                    (row for row in txn.rows(LIVE_TABLE) if row.get("指标编号") == metric),
                    None,
                )
                payload = {
                    "指标编号": metric,
                    "最新窗口起": latest.get("窗口起"),
                    "最新窗口止": latest.get("窗口止"),
                    "指标版本": latest.get("指标版本"),
                    "聚合值": latest.get("聚合值"),
                    "数据时点": latest.get("数据时点"),
                    "同步时间": moment.isoformat(),
                    "同步批次": batch_id,
                    "窗口记录id": latest.get("id"),
                }
                if state is None:
                    txn.append(LIVE_TABLE, payload)
                else:
                    state.update(payload)

            summary = {
                "批次号": batch_id,
                "接收条数": len(records),
                "写入条数": inserted,
                "覆盖条数": replaced,
                "跳过条数": skipped,
                "影响指标": sorted(touched_metrics),
                "问题": issues,
                "同步时间": moment.isoformat(),
            }
            txn.journal(SYNC_JOURNAL, {
                "批次号": batch_id,
                "结果": "成功",
                "说明": f"写入 {inserted} 条、覆盖 {replaced} 条、跳过 {skipped} 条",
                **{key: summary[key] for key in ("接收条数", "写入条数", "覆盖条数", "跳过条数", "影响指标", "问题", "同步时间")},
            })
            txn.commit()
        except Exception as exc:  # noqa: BLE001 - 同步批次失败必须整体回滚
            txn.rollback()
            failed = store.transaction()
            failed.journal(SYNC_JOURNAL, {
                "批次号": 0,
                "结果": "失败",
                "说明": str(exc),
                "接收条数": len(records) if isinstance(records, list) else 0,
                "同步时间": moment.isoformat(),
            })
            failed.commit()
            return None, f"同步失败，整批已回滚：{exc}"

        return summary, (
            f"同步完成：写入 {inserted} 条、覆盖 {replaced} 条、跳过 {skipped} 条"
            + (f"，{len(issues)} 条需要关注" if issues else "")
        )

    def sync_batches(self) -> list[dict[str, Any]]:
        return sorted(store.aux_rows(SYNC_JOURNAL), key=lambda row: int(row.get("id", 0)), reverse=True)

    def live_states(self) -> list[dict[str, Any]]:
        return sorted(store.aux_rows(LIVE_TABLE), key=lambda row: str(row.get("指标编号")))

    def windows(self, metric: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        rows = store.aux_rows(WINDOW_TABLE)
        if metric:
            rows = [row for row in rows if row.get("指标编号") == metric]
        rows = sorted(rows, key=lambda row: (str(row.get("窗口起")), int(row.get("指标版本", 0))), reverse=True)
        return rows[:limit]
