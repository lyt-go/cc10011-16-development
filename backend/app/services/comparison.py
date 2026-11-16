"""同比环比业务规则：配置维护、基准快照计算、发布与导入导出。

状态约定
--------
对比配置：草稿 / 配置评审中 / 已启用 / 已停用
对比快照：已计算（草稿） / 已发布 / 已归档 / 已作废
对比数据集：草稿 / 已发布 / 已归档 / 已作废

同一业务键（配置编号）在任何时刻只允许一个「当前」数据集：草稿或已发布。
计算新快照会把旧的草稿数据集作废；发布新快照会把旧的已发布数据集归档。
大盘刷新只读取已发布数据集，不会现算，也不会复用上一次的数值。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.comparison_engine import (
    COMPARISON_TYPES,
    DISPLAY_FORMATS,
    compute_snapshot,
    freshness_seconds,
    parse_moment,
)
from app.services.periods import PeriodError, parse_offset, parse_period
from app.store import StoreTransaction, store

MODULE = "comparison"
SNAPSHOT_TABLE = "comparison_snapshot"
DATASET_TABLE = "comparison_dataset"
JOURNAL_TABLE = "comparison_journal"
METRIC_SNAPSHOT_TABLE = "metric_snapshot"
VERSION_TABLE = "metric_version"
WINDOW_TABLE = "metric_window_aggregate"

REQUIRED_FIELDS = ["配置编号", "目标指标", "对比类型"]
CONFIG_FIELDS = [
    "配置编号",
    "目标指标",
    "指标名称",
    "对比类型",
    "基准周期",
    "偏移量",
    "差异阈值",
    "显示格式",
    "对比维度",
    "新鲜度上限秒",
    "指标版本",
]
STATUS_ORDER = ["草稿", "配置评审中", "已启用", "已停用"]
ACTION_RULES = {"提交配置评审": "配置评审中", "启用配置": "已启用", "停用配置": "已停用"}
NEGATIVE_ACTIONS = ["停用配置"]

BUNDLE_FORMAT = "comparison-baseline-bundle"
BUNDLE_VERSION = 1


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _journal(txn: StoreTransaction, business_key: str, event: str, **extra: Any) -> dict[str, Any]:
    payload = {
        "业务键": business_key,
        "事件": event,
        "时间": _now().isoformat(),
    }
    payload.update(extra)
    return txn.journal(JOURNAL_TABLE, payload)


def _published_dataset(rows: list[dict[str, Any]], business_key: str) -> dict[str, Any] | None:
    """只读查询：调用方传 `store.aux_rows(...)` 或事务内 `txn.rows(...)` 的结果。"""
    for row in rows:
        if str(row.get("业务键")) == business_key and row.get("状态") == "已发布":
            return row
    return None


def _next_dataset_code(txn: StoreTransaction) -> str:
    return f"CMP-DS-{len(txn.rows(DATASET_TABLE)) + 1:04d}"


def _next_snapshot_code(txn: StoreTransaction) -> str:
    return f"CMP-SNAP-{len(txn.rows(SNAPSHOT_TABLE)) + 1:04d}"


def _sync_metric_snapshot(txn: StoreTransaction, dataset: dict[str, Any], now: datetime) -> dict[str, Any]:
    """另一个页面的指标快照：跟随已发布对比基准同步，同一对比配置只保留一份当前。"""
    for row in txn.rows(METRIC_SNAPSHOT_TABLE):
        if (
            row.get("指标编号") == dataset.get("目标指标")
            and str(row.get("配置编号")) == str(dataset.get("配置编号"))
            and row.get("状态") == "当前"
        ):
            row["状态"] = "历史"
    return txn.append(METRIC_SNAPSHOT_TABLE, {
        "指标编号": dataset.get("目标指标"),
        "指标名称": dataset.get("指标名称"),
        "配置编号": dataset.get("配置编号"),
        "对比类型": dataset.get("对比类型"),
        "指标版本": dataset.get("指标版本"),
        "当期指标版本": dataset.get("当期指标版本"),
        "快照编号": dataset.get("快照编号"),
        "数据集编号": dataset.get("数据集编号"),
        "基准周期": dataset.get("基准周期"),
        "当期周期": dataset.get("当期周期"),
        "基准值": dataset.get("基准值"),
        "当期值": dataset.get("当期值"),
        "增长率": dataset.get("增长率"),
        "增长率文本": dataset.get("增长率文本"),
        "不可比原因": dataset.get("不可比原因"),
        "最新数据时点": dataset.get("最新数据时点"),
        "新鲜度上限秒": dataset.get("新鲜度上限秒"),
        "同步时间": now.isoformat(),
        "状态": "当前",
    })


class ComparisonService:
    # ------------------------------------------------------------------ 配置
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        metric: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("配置编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if metric:
            rows = [row for row in rows if str(row.get("目标指标")) == metric]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        enriched = dict(entry)
        dataset = _published_dataset(store.aux_rows(DATASET_TABLE), str(entry.get("配置编号")))
        enriched["已发布数据集"] = dataset
        return enriched

    def validate_config(self, values: dict[str, Any], *, base: dict[str, Any] | None = None) -> list[str]:
        merged = dict(base or {})
        merged.update(values)
        errors: list[str] = []

        for field in REQUIRED_FIELDS:
            if not str(merged.get(field) or "").strip():
                errors.append(f"缺少必填字段：{field}")
        if errors:
            return errors

        metric = str(merged.get("目标指标") or "").strip()
        if not any(str(row.get("指标编号")) == metric for row in store.rows("realtimemetric")):
            errors.append(f"目标指标「{metric}」在实时指标里不存在")

        comparison_type = str(merged.get("对比类型") or "").strip()
        if comparison_type not in COMPARISON_TYPES:
            errors.append(f"对比类型必须是 {' / '.join(COMPARISON_TYPES)}，收到「{comparison_type}」")

        try:
            period = parse_period(str(merged.get("基准周期") or ""))
        except PeriodError as exc:
            errors.append(str(exc))
        else:
            raw_offset = merged.get("偏移量")
            if raw_offset is None or str(raw_offset).strip() == "":
                merged["偏移量"] = raw_offset = 1
            try:
                offset = parse_offset(raw_offset)
            except PeriodError as exc:
                errors.append(str(exc))
            else:
                if offset <= 0:
                    errors.append("偏移量必须是正整数")

        threshold = str(merged.get("差异阈值") or "").strip()
        if threshold:
            try:
                if float(threshold) < 0:
                    errors.append("差异阈值不能为负数")
            except ValueError:
                errors.append(f"差异阈值「{threshold}」不是数字")

        display_format = str(merged.get("显示格式") or "百分比(两位小数)").strip()
        if display_format not in DISPLAY_FORMATS:
            errors.append(f"显示格式必须是 {' / '.join(DISPLAY_FORMATS)}")

        freshness = str(merged.get("新鲜度上限秒") or "").strip()
        if freshness:
            try:
                if int(freshness) <= 0:
                    errors.append("新鲜度上限秒必须是正整数")
            except ValueError:
                errors.append(f"新鲜度上限秒「{freshness}」不是整数")

        pinned = str(merged.get("指标版本") or "").strip()
        if pinned and not pinned.isdigit():
            errors.append(f"指标版本「{pinned}」不是整数版本号")
        return errors

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        errors = self.validate_config(values)
        if errors:
            return None, errors
        business_key = str(values["配置编号"]).strip()
        if any(str(row.get("配置编号")) == business_key for row in store.rows(MODULE)):
            return None, [f"配置编号「{business_key}」已存在"]
        txn = store.transaction()
        entry = txn.append(MODULE, {})
        entry.update({field: values.get(field) for field in CONFIG_FIELDS})
        entry["配置编号"] = business_key
        entry["偏移量"] = str(values.get("偏移量") or "1")
        entry["显示格式"] = str(values.get("显示格式") or "百分比(两位小数)")
        entry["配置修订号"] = 1
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        _journal(txn, entry["配置编号"], "登记配置", 配置id=entry["id"], 结果="草稿", 配置修订号=1)
        txn.commit()
        return entry, []

    def update_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """调整基准周期等字段会让旧快照失去口径依据，因此修订号 +1 并要求重新计算。"""
        base = store.find(MODULE, entry_id)
        if base is None:
            return None, [f"对比配置 {entry_id} 不存在或已归档"]
        errors = self.validate_config(values, base=base)
        if errors:
            return None, errors
        incoming = str(values.get("配置编号") or "").strip()
        if incoming and incoming != str(base.get("配置编号")):
            return None, ["配置编号是业务键，不允许修改"]

        txn = store.transaction()
        entry = txn.find(MODULE, entry_id)
        changed = {
            field: values[field]
            for field in CONFIG_FIELDS
            # 配置编号是业务键，空白表示沿用原值，永不在调整里改写。
            if field != "配置编号"
            and field in values
            and str(values[field] if values[field] is not None else "") != str(entry.get(field) if entry.get(field) is not None else "")
        }
        if not changed:
            return entry, []
        entry.update(changed)
        entry["配置修订号"] = int(entry.get("配置修订号", 1)) + 1
        for row in txn.rows(DATASET_TABLE):
            if str(row.get("业务键")) == str(entry.get("配置编号")) and row.get("状态") == "草稿":
                row["状态"] = "已作废"
                row["作废原因"] = "配置修订后旧草稿失效"
        for row in txn.rows(SNAPSHOT_TABLE):
            if str(row.get("配置编号")) == str(entry.get("配置编号")) and row.get("状态") == "已计算":
                row["状态"] = "已作废"
                row["作废原因"] = "配置修订后旧草稿快照失效"
        _journal(
            txn,
            str(entry.get("配置编号")),
            "调整配置",
            配置id=entry_id,
            结果="已调整",
            配置修订号=entry["配置修订号"],
            变更字段=changed,
        )
        txn.commit()
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"对比配置 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于同比环比可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"对比配置已{action}"

    # ------------------------------------------------------------------ 计算
    def compute(self, entry_id: int, *, now: datetime | None = None, source: str = "计算") -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
        """计算基准周期快照并生成草稿数据集；失败整体回滚，只留一条失败状态记录。"""
        moment = now or _now()
        config = store.find(MODULE, entry_id)
        if config is None:
            return None, None, f"对比配置 {entry_id} 不存在或已归档"
        if config.get("status") == "已停用":
            return None, None, f"对比配置 {entry_id} 已停用，不能计算基准快照"

        txn = store.transaction()
        try:
            versions = list(txn.rows(VERSION_TABLE))
            windows = list(txn.rows(WINDOW_TABLE))
            snapshot_code = _next_snapshot_code(txn)
            snapshot = compute_snapshot(
                config,
                versions,
                windows,
                as_of=moment,
                snapshot_code=snapshot_code,
                source=source,
            )

            # 同一业务键只保留一个当前版本：新草稿让旧草稿作废，已发布版本保持不动。
            for row in txn.rows(DATASET_TABLE):
                if str(row.get("业务键")) == str(config.get("配置编号")) and row.get("状态") == "草稿":
                    row["状态"] = "已作废"
                    row["作废原因"] = "新生成了对比草稿"
            for row in txn.rows(SNAPSHOT_TABLE):
                if str(row.get("配置编号")) == str(config.get("配置编号")) and row.get("状态") == "已计算":
                    row["状态"] = "已作废"
                    row["作废原因"] = "新生成了对比草稿"

            snapshot_row = txn.append(SNAPSHOT_TABLE, {"状态": "已计算", **snapshot})
            existing_versions = [
                int(row.get("版本号", 0))
                for row in txn.rows(DATASET_TABLE)
                if str(row.get("业务键")) == str(config.get("配置编号"))
            ]
            dataset = txn.append(DATASET_TABLE, self._dataset_payload(
                config,
                snapshot,
                snapshot_code=snapshot_code,
                dataset_code=_next_dataset_code(txn),
                version_no=max(existing_versions, default=0) + 1,
                state="草稿",
                source=source,
                moment=moment,
            ))
            _journal(
                txn,
                str(config.get("配置编号")),
                "计算",
                配置id=entry_id,
                快照编号=snapshot_code,
                数据集编号=dataset["数据集编号"],
                结果="草稿",
                可比=snapshot["不可比原因"] is None,
                说明=snapshot["不可比原因"] or "已生成可比快照",
            )
            txn.commit()
        except Exception as exc:  # noqa: BLE001 - 计算失败必须整体回滚
            txn.rollback()
            self._record_failure(str(config.get("配置编号")), entry_id, "计算", str(exc))
            return None, None, f"基准快照计算失败，已回滚：{exc}"

        return snapshot_row, dataset, (
            "基准快照已计算，生成对比草稿数据集"
            if snapshot["不可比原因"] is None
            else f"基准快照已计算，但结果不可比：{snapshot['不可比原因']}"
        )

    def _dataset_payload(
        self,
        config: dict[str, Any],
        snapshot: dict[str, Any],
        *,
        snapshot_code: str,
        dataset_code: str,
        version_no: int,
        state: str,
        source: str,
        moment: datetime,
    ) -> dict[str, Any]:
        return {
            "数据集编号": dataset_code,
            "业务键": config.get("配置编号"),
            "版本号": version_no,
            "状态": state,
            "快照编号": snapshot_code,
            "配置id": config.get("id"),
            "配置编号": config.get("配置编号"),
            "配置修订号": config.get("配置修订号", 1),
            "目标指标": config.get("目标指标"),
            "指标名称": config.get("指标名称"),
            "对比类型": config.get("对比类型"),
            "基准周期": snapshot.get("基准周期"),
            "当期周期": snapshot.get("当期周期"),
            "周期粒度": snapshot.get("周期粒度"),
            "指标版本": snapshot.get("基准指标版本"),
            "当期指标版本": snapshot.get("当期指标版本"),
            "版本冲突": snapshot.get("版本冲突"),
            "备选版本": snapshot.get("备选版本"),
            "快照指纹": snapshot.get("快照指纹"),
            "基准值": snapshot.get("基准值"),
            "当期值": snapshot.get("当期值"),
            "差异": snapshot.get("差异"),
            "增长率": snapshot.get("增长率"),
            "增长率文本": snapshot.get("增长率文本"),
            "差异文本": snapshot.get("差异文本"),
            "差异阈值": snapshot.get("差异阈值"),
            "显示格式": snapshot.get("显示格式"),
            "是否超阈值": snapshot.get("是否超阈值"),
            "不可比原因": snapshot.get("不可比原因"),
            "全部不可比原因": snapshot.get("全部不可比原因"),
            "新鲜度秒": snapshot.get("新鲜度秒"),
            "新鲜度上限秒": snapshot.get("新鲜度上限秒"),
            "最新数据时点": snapshot.get("最新数据时点"),
            "生成时间": moment.isoformat(),
            "发布时间": moment.isoformat() if state == "已发布" else None,
            "来源": source,
        }

    def _record_failure(self, business_key: str, entry_id: int, event: str, reason: str) -> None:
        txn = store.transaction()
        _journal(txn, business_key, event, 配置id=entry_id, 结果="失败", 说明=reason)
        txn.commit()

    # ------------------------------------------------------------------ 发布
    def publish(self, snapshot_id: int, *, now: datetime | None = None) -> tuple[dict[str, Any] | None, str]:
        moment = now or _now()
        snapshot = next(
            (row for row in store.aux_rows(SNAPSHOT_TABLE) if int(row.get("id", 0)) == snapshot_id),
            None,
        )
        if snapshot is None:
            return None, f"基准快照 {snapshot_id} 不存在"
        if snapshot.get("状态") == "已发布":
            dataset = _published_dataset(store.aux_rows(DATASET_TABLE), str(snapshot.get("配置编号")))
            return dataset, "该基准快照已经是当前发布版本，无需重复发布"
        if snapshot.get("状态") != "已计算":
            return None, f"基准快照 {snapshot_id} 当前状态为「{snapshot.get('状态')}」，只有已计算的草稿可以发布"

        config = store.find(MODULE, int(snapshot.get("配置id") or 0))
        if config is None:
            return None, "快照关联的对比配置不存在或已归档"
        if int(snapshot.get("配置修订号", config.get("配置修订号", 1))) != int(config.get("配置修订号", 1)):
            return None, "配置已调整（基准周期或指标版本变化），请重新计算基准快照后再发布"

        txn = store.transaction()
        snapshot = txn.find(SNAPSHOT_TABLE, snapshot_id)
        dataset = next(
            (
                row for row in txn.rows(DATASET_TABLE)
                if row.get("快照编号") == snapshot.get("快照编号")
            ),
            None,
        )
        if dataset is None:
            return None, "快照没有对应的对比数据集，请重新计算"
        if dataset.get("状态") not in ("草稿", "已发布"):
            return None, f"对比数据集状态为「{dataset.get('状态')}」，不能发布"

        business_key = str(snapshot.get("配置编号"))
        archived: list[str] = []
        for row in txn.rows(DATASET_TABLE):
            if str(row.get("业务键")) == business_key and row.get("状态") == "已发布" and row is not dataset:
                row["状态"] = "已归档"
                row["归档时间"] = moment.isoformat()
                archived.append(str(row.get("数据集编号")))
        for row in txn.rows(SNAPSHOT_TABLE):
            if str(row.get("配置编号")) == business_key and row.get("状态") == "已发布" and row.get("id") != snapshot_id:
                row["状态"] = "已归档"
                row["归档时间"] = moment.isoformat()

        snapshot["状态"] = "已发布"
        snapshot["发布时间"] = moment.isoformat()
        dataset["状态"] = "已发布"
        dataset["发布时间"] = moment.isoformat()
        dataset["配置修订号"] = snapshot.get("配置修订号", config.get("配置修订号", 1))
        metric_snapshot = _sync_metric_snapshot(txn, dataset, moment)
        _journal(
            txn,
            business_key,
            "发布",
            快照编号=snapshot.get("快照编号"),
            数据集编号=dataset.get("数据集编号"),
            结果="已发布",
            可比=dataset.get("不可比原因") is None,
            说明=dataset.get("不可比原因") or "已发布可比对比基准",
            归档数据集=archived,
        )
        txn.commit()
        dataset["指标快照id"] = metric_snapshot.get("id")
        return dataset, "对比基准已发布，大盘刷新与指标快照将读取这一份快照"

    # ------------------------------------------------------------------ 查询
    def dataset_for_config(self, entry_id: int) -> dict[str, Any] | None:
        config = store.find(MODULE, entry_id)
        if config is None:
            return None
        return _published_dataset(store.aux_rows(DATASET_TABLE), str(config.get("配置编号")))

    def list_datasets(self, entry_id: int | None = None, business_key: str | None = None) -> list[dict[str, Any]]:
        rows = store.aux_rows(DATASET_TABLE)
        if entry_id is not None:
            config = store.find(MODULE, entry_id)
            if config is None:
                return []
            business_key = str(config.get("配置编号"))
        if business_key:
            rows = [row for row in rows if str(row.get("业务键")) == business_key]
        return sorted(rows, key=lambda row: (str(row.get("业务键")), -int(row.get("版本号", 0))))

    def list_snapshots(self, entry_id: int | None = None) -> list[dict[str, Any]]:
        rows = store.aux_rows(SNAPSHOT_TABLE)
        if entry_id is not None:
            config = store.find(MODULE, entry_id)
            if config is None:
                return []
            rows = [row for row in rows if int(row.get("配置id") or 0) == entry_id or row.get("配置编号") == config.get("配置编号")]
        return sorted(rows, key=lambda row: int(row.get("id", 0)), reverse=True)

    def list_journal(self, business_key: str | None = None) -> list[dict[str, Any]]:
        rows = store.aux_rows(JOURNAL_TABLE)
        if business_key:
            rows = [row for row in rows if str(row.get("业务键")) == business_key]
        return sorted(rows, key=lambda row: int(row.get("id", 0)), reverse=True)

    def metric_snapshots(self) -> list[dict[str, Any]]:
        return sorted(store.aux_rows(METRIC_SNAPSHOT_TABLE), key=lambda row: int(row.get("id", 0)), reverse=True)

    # ------------------------------------------------------------------ 大盘读
    def dashboard_refresh(self, *, now: datetime | None = None) -> dict[str, Any]:
        """大盘刷新：只读已发布对比数据集；不可比时给原因，绝不回退到上次数字。"""
        moment = now or _now()
        published = [row for row in store.aux_rows(DATASET_TABLE) if row.get("状态") == "已发布"]
        by_key = {str(row.get("业务键")): row for row in published}
        cards: list[dict[str, Any]] = []
        for config in store.rows(MODULE):
            key = str(config.get("配置编号"))
            dataset = by_key.get(key)
            if dataset is None:
                cards.append({
                    "配置编号": key,
                    "目标指标": config.get("目标指标"),
                    "指标名称": config.get("指标名称"),
                    "对比类型": config.get("对比类型"),
                    "状态": "不可比",
                    "可比": False,
                    "增长率": None,
                    "增长率文本": "—",
                    "数据集编号": None,
                    "快照编号": None,
                    "不可比原因": "该对比配置还没有已发布的对比快照",
                    "全部不可比原因": ["该对比配置还没有已发布的对比快照"],
                    "数据来源": "无",
                })
                continue
            reasons = list(dataset.get("全部不可比原因") or [])
            if dataset.get("不可比原因"):
                reasons.insert(0, dataset["不可比原因"])
            reasons = list(dict.fromkeys(reasons))
            # 读取时再判一次新鲜度：发布后数据不再更新也要变成可解释的不可比。
            latest = parse_moment(dataset.get("最新数据时点"))
            limit = int(dataset.get("新鲜度上限秒") or 0)
            live_freshness = freshness_seconds(latest, moment)
            if latest is not None and limit and live_freshness is not None and live_freshness > limit:
                message = (
                    f"数据新鲜度不足：最新数据时点 {latest.isoformat()}，"
                    f"刷新时刻已滞后 {live_freshness} 秒，超过上限 {limit} 秒"
                )
                if message not in reasons:
                    reasons.append(message)
            comparable = not reasons
            cards.append({
                "配置编号": key,
                "目标指标": dataset.get("目标指标"),
                "指标名称": dataset.get("指标名称"),
                "对比类型": dataset.get("对比类型"),
                "状态": "可比" if comparable else "不可比",
                "可比": comparable,
                "基准周期": dataset.get("基准周期"),
                "当期周期": dataset.get("当期周期"),
                "指标版本": dataset.get("指标版本"),
                "当期指标版本": dataset.get("当期指标版本"),
                "基准值": dataset.get("基准值") if comparable else None,
                "当期值": dataset.get("当期值") if comparable else None,
                "差异": dataset.get("差异") if comparable else None,
                "增长率": dataset.get("增长率") if comparable else None,
                "增长率文本": (dataset.get("增长率文本") if comparable else "—"),
                "显示格式": dataset.get("显示格式"),
                "是否超阈值": bool(dataset.get("是否超阈值")) if comparable else False,
                "数据集编号": dataset.get("数据集编号"),
                "数据集版本": dataset.get("版本号"),
                "快照编号": dataset.get("快照编号"),
                "快照指纹": dataset.get("快照指纹"),
                "新鲜度秒": live_freshness,
                "新鲜度上限秒": limit,
                "不可比原因": None if comparable else reasons[0],
                "全部不可比原因": reasons,
                "数据来源": "已发布对比快照",
                "发布时间": dataset.get("发布时间"),
            })
        return {
            "刷新时间": moment.isoformat(),
            "读取口径": "仅读取已发布对比数据集，不重新计算，不回退历史数字",
            "数据集总数": len(published),
            "可比数": sum(1 for card in cards if card["可比"]),
            "不可比数": sum(1 for card in cards if not card["可比"]),
            "cards": cards,
        }

    # ------------------------------------------------------------------ 导出导入
    def bundle(self, entry_id: int, *, now: datetime | None = None) -> dict[str, Any] | None:
        moment = now or _now()
        config = store.find(MODULE, entry_id)
        if config is None:
            return None
        snapshot = next(
            (row for row in self.list_snapshots(entry_id) if row.get("状态") in ("已发布", "已计算")),
            None,
        )
        if snapshot is None:
            return None
        dataset = _published_dataset(store.aux_rows(DATASET_TABLE), str(config.get("配置编号")))
        window_ids = {
            int(row.get("id") or 0)
            for row in (snapshot.get("基准窗口记录") or []) + (snapshot.get("当期窗口记录") or [])
        }
        windows = [
            dict(row) for row in store.aux_rows(WINDOW_TABLE)
            if int(row.get("id") or 0) in window_ids
        ]
        versions = [
            dict(row) for row in store.aux_rows(VERSION_TABLE)
            if row.get("指标编号") == config.get("目标指标")
            and int(row.get("版本号", 0)) in {snapshot.get("基准指标版本"), snapshot.get("当期指标版本")}
        ]
        return {
            "格式": BUNDLE_FORMAT,
            "格式版本": BUNDLE_VERSION,
            "导出时间": moment.isoformat(),
            "业务键": config.get("配置编号"),
            "配置": {field: config.get(field) for field in CONFIG_FIELDS}
            | {"配置修订号": config.get("配置修订号", 1), "status": config.get("status")},
            "快照": snapshot,
            "数据集": dataset,
            "指标版本": versions,
            "基准窗口记录": windows,
            "口径说明": "导入时原样保留快照编号、指纹、生成时间与数值，不按导入时刻重算",
        }

    def import_bundle(self, bundle: dict[str, Any], *, now: datetime | None = None) -> tuple[dict[str, Any] | None, str]:
        moment = now or _now()
        if not isinstance(bundle, dict):
            return None, "导入内容不是有效的 JSON 对象"
        if bundle.get("格式") != BUNDLE_FORMAT:
            return None, f"文件格式不是 {BUNDLE_FORMAT}"
        snapshot = bundle.get("快照")
        if not isinstance(snapshot, dict):
            return None, "缺少基准快照，无法导入"
        required_snapshot_fields = ["配置编号", "目标指标", "基准周期", "基准指标版本", "不可比原因"]
        missing = [field for field in required_snapshot_fields if field not in snapshot]
        if missing:
            return None, f"基准快照缺少字段：{'、'.join(missing)}"
        config_payload = bundle.get("配置")
        if not isinstance(config_payload, dict):
            config_payload = {field: snapshot.get(field) for field in CONFIG_FIELDS}
        business_key = str(snapshot.get("配置编号") or bundle.get("业务键") or "")
        if not business_key:
            return None, "基准快照缺少配置编号"
        config = next((row for row in store.rows(MODULE) if str(row.get("配置编号")) == business_key), None)

        original_code = str(snapshot.get("快照编号") or "")
        fingerprint = str(snapshot.get("快照指纹") or "")
        existing = [
            row for row in store.aux_rows(SNAPSHOT_TABLE)
            if row.get("状态") == "已发布"
            and str(row.get("配置编号")) == business_key
            and fingerprint
            and str(row.get("快照指纹")) == fingerprint
        ]
        if existing:
            dataset = _published_dataset(store.aux_rows(DATASET_TABLE), business_key)
            return dataset, f"同一份基准快照（指纹 {fingerprint}）已发布，导入未重复落库"

        txn = store.transaction()
        try:
            for row in txn.rows(METRIC_SNAPSHOT_TABLE):
                if row.get("指标编号") == snapshot.get("目标指标") and row.get("状态") == "当前":
                    row["状态"] = "历史"
            for row in txn.rows(DATASET_TABLE):
                if str(row.get("业务键")) == business_key and row.get("状态") == "已发布":
                    row["状态"] = "已归档"
                    row["归档时间"] = moment.isoformat()
                    row["归档原因"] = "导入新的基准快照"
            for row in txn.rows(SNAPSHOT_TABLE):
                if str(row.get("配置编号")) == business_key and row.get("状态") == "已发布":
                    row["状态"] = "已归档"
                    row["归档时间"] = moment.isoformat()
                    row["归档原因"] = "导入新的基准快照"
            for row in txn.rows(DATASET_TABLE):
                if str(row.get("业务键")) == business_key and row.get("状态") == "草稿":
                    row["状态"] = "已作废"
                    row["作废原因"] = "导入新的基准快照"

            imported = dict(snapshot)
            imported["原快照编号"] = original_code
            imported["快照编号"] = _next_snapshot_code(txn)
            imported["状态"] = "已发布"
            imported["来源"] = "导入"
            imported["导入时间"] = moment.isoformat()
            imported["发布时间"] = moment.isoformat()
            imported["导出时间"] = bundle.get("导出时间")
            imported["原生成时间"] = snapshot.get("生成时间")
            imported.pop("id", None)
            snapshot_row = txn.append(SNAPSHOT_TABLE, imported)

            dataset_payload = bundle.get("数据集") if isinstance(bundle.get("数据集"), dict) else {}
            existing_versions = [
                int(row.get("版本号", 0))
                for row in txn.rows(DATASET_TABLE)
                if str(row.get("业务键")) == business_key
            ]
            dataset = txn.append(DATASET_TABLE, {
                "数据集编号": _next_dataset_code(txn),
                "业务键": business_key,
                "版本号": max(existing_versions, default=0) + 1,
                "状态": "已发布",
                "快照编号": snapshot_row["快照编号"],
                "原快照编号": original_code,
                "配置id": config.get("id") if config else dataset_payload.get("配置id"),
                "配置编号": business_key,
                "配置修订号": int(config_payload.get("配置修订号") or snapshot.get("配置修订号") or 1),
                "目标指标": snapshot.get("目标指标"),
                "指标名称": config_payload.get("指标名称") or snapshot.get("指标名称"),
                "对比类型": snapshot.get("对比类型"),
                "基准周期": snapshot.get("基准周期"),
                "当期周期": snapshot.get("当期周期"),
                "周期粒度": snapshot.get("周期粒度"),
                "指标版本": snapshot.get("基准指标版本"),
                "当期指标版本": snapshot.get("当期指标版本"),
                "版本冲突": snapshot.get("版本冲突"),
                "备选版本": snapshot.get("备选版本"),
                "快照指纹": fingerprint,
                "基准值": snapshot.get("基准值"),
                "当期值": snapshot.get("当期值"),
                "差异": snapshot.get("差异"),
                "增长率": snapshot.get("增长率"),
                "增长率文本": snapshot.get("增长率文本"),
                "差异文本": snapshot.get("差异文本"),
                "差异阈值": snapshot.get("差异阈值"),
                "显示格式": snapshot.get("显示格式"),
                "是否超阈值": bool(snapshot.get("是否超阈值")),
                "不可比原因": snapshot.get("不可比原因"),
                "全部不可比原因": snapshot.get("全部不可比原因") or [],
                "新鲜度秒": snapshot.get("新鲜度秒"),
                "新鲜度上限秒": snapshot.get("新鲜度上限秒"),
                "最新数据时点": snapshot.get("最新数据时点"),
                "生成时间": snapshot.get("生成时间"),
                "发布时间": moment.isoformat(),
                "来源": "导入",
                "原生成时间": snapshot.get("生成时间"),
            })
            _sync_metric_snapshot(txn, dataset, moment)
            _journal(
                txn,
                business_key,
                "导入",
                快照编号=snapshot_row["快照编号"],
                原快照编号=original_code,
                数据集编号=dataset["数据集编号"],
                结果="已发布",
                可比=snapshot.get("不可比原因") is None,
                说明=f"导入保留原快照（指纹 {fingerprint or '无'}，生成于 {snapshot.get('生成时间')}）",
            )
            txn.commit()
        except Exception as exc:  # noqa: BLE001
            txn.rollback()
            self._record_failure(business_key, 0, "导入", str(exc))
            return None, f"导入失败，已回滚：{exc}"
        return dataset, f"已导入并发布基准快照，原快照指纹 {fingerprint or '未提供'} 原样保留"
