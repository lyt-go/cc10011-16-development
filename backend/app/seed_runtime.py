"""运行期示例数据：在基础样例之上补齐同比环比真正需要的数据流。

基础 seed 只保证每个模块有几条记录；这里补的是指标版本、指标窗口聚合记录、
以及带真实基准周期的对比配置。时间都相对服务启动时刻生成，所以新鲜度判定
在克隆下来之后仍然有意义。

指标窗口聚合记录的写入口径（与实时指标同步模块共用）：
    指标编号 + 窗口起 + 指标版本 唯一；同一天重复写入时以「记录生成时间」最新的一条为准。

指标版本刻意做出两段：
    v1 / v2 在 7 月下旬到 9 月底重叠 —— 用来演示版本冲突下仍只裁决一个基准快照；
    v2 / v3 之间在 10 月中旬留出空档 —— 用来演示周期跨越版本时判为不可比。
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from app.services.comparison_engine import compute_snapshot

METRIC_NAMES = {"REAL-0001": "支付成功数", "REAL-0002": "下单量", "REAL-0003": "活跃用户数"}

METRIC_PROFILE: dict[str, dict[str, Any]] = {
    "REAL-0001": {"基数": 12000.0, "滞后秒": 180},
    "REAL-0002": {"基数": 8600.0, "滞后秒": 3 * 86400},
    "REAL-0003": {"基数": 52000.0, "滞后秒": 900},
}

_WINDOW_DAYS = 500


def _month_start(anchor: date, months_back: int) -> date:
    total = anchor.year * 12 + anchor.month - 1 - months_back
    year, month = divmod(total, 12)
    return date(year, month + 1, 1)


def _build_versions(today: date) -> list[dict[str, Any]]:
    return [
        {
            "指标编号": "REAL-0001",
            "版本号": 1,
            "生效起": "2024-01-01",
            "生效止": (today - timedelta(days=47)).isoformat(),
            "变更说明": "首版：按自然日聚合支付成功数",
        },
        {
            "指标编号": "REAL-0001",
            "版本号": 2,
            "生效起": (today - timedelta(days=118)).isoformat(),
            "生效止": (today - timedelta(days=32)).isoformat(),
            "变更说明": "v2：剔除测试商户，与 v1 有约 10 周重叠",
        },
        {
            "指标编号": "REAL-0001",
            "版本号": 3,
            "生效起": (today - timedelta(days=27)).isoformat(),
            "生效止": None,
            "变更说明": "v3：口径空档后重新上线，按商户去重",
        },
        {
            "指标编号": "REAL-0002",
            "版本号": 1,
            "生效起": "2024-01-01",
            "生效止": None,
            "变更说明": "首版：按自然日聚合下单量",
        },
        {
            "指标编号": "REAL-0003",
            "版本号": 1,
            "生效起": "2024-01-01",
            "生效止": None,
            "变更说明": "首版：按自然日聚合活跃用户数",
        },
    ]


def _build_configs(today: date) -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "配置编号": "COMP-0001",
            "目标指标": "REAL-0001",
            "指标名称": "支付成功数",
            "对比类型": "同比",
            "基准周期": _month_start(today, 14).strftime("%Y-%m"),
            "偏移量": "12",
            "差异阈值": "5",
            "显示格式": "百分比(两位小数)",
            "对比维度": "全部",
            "新鲜度上限秒": "7200",
            "指标版本": "",
            "status": "已启用",
            "pending": False,
            "abnormal": False,
        },
        {
            "id": 2,
            "配置编号": "COMP-0002",
            "目标指标": "REAL-0001",
            "指标名称": "支付成功数",
            "对比类型": "环比",
            "基准周期": _month_start(today, 2).strftime("%Y-%m"),
            "偏移量": "1",
            "差异阈值": "3",
            "显示格式": "百分比(两位小数)",
            "对比维度": "业务线",
            "新鲜度上限秒": "7200",
            "指标版本": "",
            "status": "已启用",
            "pending": False,
            "abnormal": False,
        },
        {
            "id": 3,
            "配置编号": "COMP-0003",
            "目标指标": "REAL-0002",
            "指标名称": "下单量",
            "对比类型": "环比",
            "基准周期": (today - timedelta(days=9)).isoformat(),
            "偏移量": "7",
            "差异阈值": "10",
            "显示格式": "百分比(一位小数)",
            "对比维度": "渠道",
            "新鲜度上限秒": "3600",
            "指标版本": "",
            "status": "已启用",
            "pending": False,
            "abnormal": False,
        },
        {
            "id": 4,
            "配置编号": "COMP-0004",
            "目标指标": "REAL-0003",
            "指标名称": "活跃用户数",
            "对比类型": "环比",
            "基准周期": "2023-01",
            "偏移量": "1",
            "差异阈值": "8",
            "显示格式": "绝对值(两位小数)",
            "对比维度": "地域",
            "新鲜度上限秒": "7200",
            "指标版本": "",
            "status": "已启用",
            "pending": False,
            "abnormal": False,
        },
        {
            "id": 5,
            "配置编号": "COMP-0005",
            "目标指标": "REAL-0001",
            "指标名称": "支付成功数",
            "对比类型": "环比",
            "基准周期": (today - timedelta(days=16)).strftime("%Y-%m"),
            "偏移量": "1",
            "差异阈值": "5",
            "显示格式": "百分比(两位小数)",
            "对比维度": "全部",
            "新鲜度上限秒": "7200",
            "指标版本": "",
            "status": "已启用",
            "pending": False,
            "abnormal": False,
        },
    ]


def _version_for(versions: list[dict[str, Any]], metric: str, day: date) -> int | None:
    candidates = [
        item for item in versions
        if item["指标编号"] == metric
        and date.fromisoformat(item["生效起"]) <= day
        and (item["生效止"] is None or day <= date.fromisoformat(item["生效止"]))
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item["生效起"], item["版本号"]), reverse=True)
    return int(candidates[0]["版本号"])


def _value(metric: str, day: date, version: int) -> float:
    profile = METRIC_PROFILE[metric]
    seed = hashlib.sha256(f"{metric}:{day.isoformat()}:{version}".encode("utf-8")).digest()
    jitter = int.from_bytes(seed[:4], "big") % 2000 / 10000 - 0.10
    weekly = 1.12 if day.weekday() >= 5 else 1.0
    trend = 1 + (day.toordinal() % 365) * 0.0004
    version_factor = 0.94 if version >= 2 else 1.0
    return round(profile["基数"] * (1 + jitter) * weekly * trend * version_factor, 2)


def _build_windows(
    versions: list[dict[str, Any]],
    today: date,
    now: datetime,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    next_id = 1
    day = today - timedelta(days=_WINDOW_DAYS)
    while day <= today:
        window_end = min(datetime.combine(day, time(23, 59, 59), tzinfo=timezone.utc), now)
        for metric, profile in METRIC_PROFILE.items():
            version = _version_for(versions, metric, day)
            if version is None:
                continue
            data_time = max(
                window_end - timedelta(seconds=profile["滞后秒"]),
                datetime.combine(day, time(0, 0, 0), tzinfo=timezone.utc),
            )
            rows.append({
                "id": next_id,
                "指标编号": metric,
                "窗口起": day.isoformat(),
                "窗口止": window_end.isoformat(),
                "指标版本": version,
                "聚合值": _value(metric, day, version),
                "数据时点": data_time.isoformat(),
                "记录生成时间": data_time.isoformat(),
                "来源流": "metric-window-aggregate",
                "状态": "已落库",
            })
            next_id += 1
        day += timedelta(days=1)
    return rows


def _seed_publish(
    aux: dict[str, list[dict[str, Any]]],
    config: dict[str, Any],
    versions: list[dict[str, Any]],
    windows: list[dict[str, Any]],
    now: datetime,
) -> None:
    """预置一份已发布对比数据集，让大盘和画像页面打开就有真实来源可看。"""
    snapshots = aux["comparison_snapshot"]
    datasets = aux["comparison_dataset"]
    records = aux["metric_snapshot"]
    journal = aux["comparison_journal"]

    snapshot_code = f"CMP-SNAP-{len(snapshots) + 1:04d}"
    snapshot = compute_snapshot(
        config,
        versions,
        windows,
        as_of=now,
        snapshot_code=snapshot_code,
        source="种子计算",
    )
    snapshots.append({"id": len(snapshots) + 1, "状态": "已计算", **snapshot})

    dataset_code = f"CMP-DS-{len(datasets) + 1:04d}"
    datasets.append({
        "id": len(datasets) + 1,
        "数据集编号": dataset_code,
        "业务键": config["配置编号"],
        "版本号": 1,
        "状态": "已发布",
        "快照编号": snapshot_code,
        "配置id": config["id"],
        "配置编号": config["配置编号"],
        "目标指标": config["目标指标"],
        "指标名称": config.get("指标名称"),
        "对比类型": config["对比类型"],
        "基准周期": snapshot["基准周期"],
        "当期周期": snapshot["当期周期"],
        "指标版本": snapshot["基准指标版本"],
        "当期指标版本": snapshot["当期指标版本"],
        "快照指纹": snapshot["快照指纹"],
        "基准值": snapshot["基准值"],
        "当期值": snapshot["当期值"],
        "差异": snapshot["差异"],
        "增长率": snapshot["增长率"],
        "增长率文本": snapshot["增长率文本"],
        "显示格式": snapshot["显示格式"],
        "是否超阈值": snapshot["是否超阈值"],
        "不可比原因": snapshot["不可比原因"],
        "全部不可比原因": snapshot["全部不可比原因"],
        "新鲜度秒": snapshot["新鲜度秒"],
        "新鲜度上限秒": snapshot["新鲜度上限秒"],
        "最新数据时点": snapshot["最新数据时点"],
        "生成时间": now.isoformat(),
        "发布时间": now.isoformat(),
        "来源": "种子计算",
    })
    _sync_metric_snapshot(records, snapshot, dataset_code, now)
    journal.append({
        "id": len(journal) + 1,
        "业务键": config["配置编号"],
        "事件": "发布",
        "快照编号": snapshot_code,
        "数据集编号": dataset_code,
        "时间": now.isoformat(),
        "结果": "已发布",
        "说明": "种子数据首次发布",
    })


def _sync_metric_snapshot(
    records: list[dict[str, Any]],
    snapshot: dict[str, Any],
    dataset_code: str,
    now: datetime,
) -> None:
    """把对比基准同步成指标快照：同一对比配置只保留当前一份。"""
    for row in records:
        if (
            row.get("指标编号") == snapshot["目标指标"]
            and str(row.get("配置编号")) == str(snapshot.get("配置编号"))
            and row.get("状态") == "当前"
        ):
            row["状态"] = "历史"
    records.append({
        "id": max((int(row.get("id", 0)) for row in records), default=0) + 1,
        "指标编号": snapshot["目标指标"],
        "指标名称": snapshot.get("指标名称"),
        "配置编号": snapshot.get("配置编号"),
        "对比类型": snapshot.get("对比类型"),
        "指标版本": snapshot["基准指标版本"],
        "当期指标版本": snapshot["当期指标版本"],
        "快照编号": snapshot["快照编号"],
        "数据集编号": dataset_code,
        "基准周期": snapshot["基准周期"],
        "当期周期": snapshot["当期周期"],
        "基准值": snapshot["基准值"],
        "当期值": snapshot["当期值"],
        "增长率": snapshot["增长率"],
        "增长率文本": snapshot["增长率文本"],
        "不可比原因": snapshot["不可比原因"],
        "最新数据时点": snapshot["最新数据时点"],
        "新鲜度上限秒": snapshot["新鲜度上限秒"],
        "同步时间": now.isoformat(),
        "状态": "当前",
    })


def apply_runtime_seed(
    tables: dict[str, list[dict[str, Any]]],
    aux: dict[str, list[dict[str, Any]]],
    now: datetime,
) -> None:
    today = now.date()

    # 1) 指标定义：把样例字段换成能看懂的名称，并保留窗口聚合口径。
    for row in tables.get("realtimemetric", []):
        code = str(row.get("指标编号") or "")
        if code in METRIC_NAMES:
            row["指标名称"] = METRIC_NAMES[code]
            row["计算窗口秒"] = "86400"
            row["聚合函数"] = "sum"
            row["数据源流"] = "metric-window-aggregate"
            row["维度字段组"] = "业务线,渠道,地域"
            row["延迟容忍毫秒"] = "300000"
            row["精度位数"] = "2"

    # 2) 对比配置：真实基准周期与阈值。
    tables["comparison"] = _build_configs(today)

    # 3) 指标版本与指标窗口聚合记录。
    versions = _build_versions(today)
    aux["metric_version"] = [{"id": index + 1, **item} for index, item in enumerate(versions)]
    windows = _build_windows(versions, today, now)
    aux["metric_window_aggregate"] = windows
    aux["metric_live_state"] = []
    aux["metric_snapshot"] = []
    aux["comparison_snapshot"] = []
    aux["comparison_dataset"] = []
    aux["comparison_journal"] = []
    aux["sync_journal"] = []

    # 4) 预置两份已发布对比数据集。
    for config in tables["comparison"][:2]:
        _seed_publish(aux, config, versions, windows, now)
