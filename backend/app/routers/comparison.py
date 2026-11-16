"""同比环比接口：维护对比配置、计算基准快照、发布数据集、导出导入与大盘读取。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.comparison import ComparisonService
from app.services.comparison_engine import COMPARISON_TYPES, DISPLAY_FORMATS
from app.services.periods import PeriodError, parse_offset, parse_period
from app.store import store

router = APIRouter(prefix="/api/comparison", tags=["同比环比"])

service = ComparisonService()

LIST_FIELDS = ["配置编号", "目标指标", "对比类型", "基准周期", "偏移量", "差异阈值", "显示格式", "对比维度"]
STATUSES = ["草稿", "配置评审中", "已启用", "已停用"]


def _failure(detail: str) -> ActionResult:
    return ActionResult(ok=False, message=detail)


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按配置编号检索"),
    status: str | None = Query(default=None, description="草稿、配置评审中、已启用、已停用"),
    metric: str | None = Query(default=None, description="按目标指标编号过滤"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按配置编号、目标指标与状态过滤同比环比列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, metric=metric, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/options")
def options() -> dict[str, Any]:
    """配置表单可选项：对比类型、显示格式、基准周期示例与可复用指标。"""
    metrics = [
        {"指标编号": row.get("指标编号"), "指标名称": row.get("指标名称"), "status": row.get("status")}
        for row in store.rows("realtimemetric")
    ]
    return {
        "对比类型": list(COMPARISON_TYPES),
        "显示格式": list(DISPLAY_FORMATS),
        "基准周期示例": ["2026-10-16", "2026-10", "2026-W42", "2026", "2026-10-01~2026-10-15"],
        "指标": metrics,
    }


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出同比环比清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "comparison", "total": total, "items": items}


@router.get("/dashboard")
def dashboard_refresh() -> dict[str, Any]:
    """大盘刷新：只读取已发布对比快照；缺基准或新鲜度越界时返回可解释的不可比结果。"""
    return service.dashboard_refresh()


@router.get("/metric-snapshots")
def metric_snapshots() -> dict[str, Any]:
    """另一个页面的指标快照：随对比基准发布同步更新。"""
    items = service.metric_snapshots()
    return {"items": items, "total": len(items)}


@router.get("/journal")
def journal(business_key: str | None = Query(default=None, description="按配置编号过滤状态记录")) -> dict[str, Any]:
    """状态记录：计算、发布、导入成功与失败都留痕，便于复盘半成品问题。"""
    items = service.list_journal(business_key)
    return {"items": items, "total": len(items)}


@router.get("/datasets")
def datasets(
    entry_id: int | None = Query(default=None),
    business_key: str | None = Query(default=None),
) -> dict[str, Any]:
    """对比数据集版本列表：同一业务键只应有一个状态为草稿或已发布的当前版本。"""
    items = service.list_datasets(entry_id=entry_id, business_key=business_key)
    return {"items": items, "total": len(items)}


@router.get("/snapshots")
def snapshots(entry_id: int | None = Query(default=None)) -> dict[str, Any]:
    """基准快照列表：含不可比原因、版本裁决与窗口记录 id。"""
    items = service.list_snapshots(entry_id=entry_id)
    return {"items": items, "total": len(items)}


@router.get("/check")
def check_config(
    metric: str = Query(..., description="目标指标编号"),
    comparison_type: str = Query(..., description="同比或环比"),
    period: str = Query(..., description="基准周期"),
    offset: str = Query(default="1", description="偏移量"),
) -> dict[str, Any]:
    """计算前预检：基准周期是否可解析、指标版本能否唯一覆盖、数据是否新鲜。"""
    errors: list[str] = []
    try:
        parsed = parse_period(period)
    except PeriodError as exc:
        return {"ok": False, "errors": [str(exc)]}
    try:
        parse_offset(offset)
    except PeriodError as exc:
        errors.append(str(exc))
    if comparison_type not in COMPARISON_TYPES:
        errors.append(f"对比类型必须是 {' / '.join(COMPARISON_TYPES)}")
    if parsed.unit == "custom" and str(offset).strip().lstrip("-").isdigit() and int(offset) <= 0:
        errors.append("偏移量必须是正整数")
    metrics = [
        {"指标编号": row.get("指标编号"), "指标名称": row.get("指标名称")}
        for row in store.rows("realtimemetric")
    ]
    if not any(item["指标编号"] == metric for item in metrics):
        errors.append(f"目标指标「{metric}」在实时指标里不存在")
    return {
        "ok": not errors,
        "errors": errors,
        "基准周期": parsed.to_dict(),
    }


@router.post("/preflight")
def preflight(payload: EntryPayload) -> dict[str, Any]:
    """按表单草稿做同样的计算前确认，但不落库。"""
    values = payload.values
    errors = service.validate_config(values)
    return {"ok": not errors, "errors": errors}


@router.post("/{entry_id}/compute", response_model=ActionResult)
def compute(entry_id: int) -> ActionResult:
    """计算基准周期快照并生成对比草稿数据集；失败整体回滚，不残留半成品。"""
    snapshot, dataset, message = service.compute(entry_id)
    if snapshot is None or dataset is None:
        return _failure(message)
    return ActionResult(
        ok=True,
        message=message,
        entry={
            "snapshot": snapshot,
            "dataset": dataset,
            "comparable": snapshot.get("不可比原因") is None,
        },
    )


@router.post("/snapshots/{snapshot_id}/publish", response_model=ActionResult)
def publish(snapshot_id: int) -> ActionResult:
    """发布基准快照：同一业务键的旧发布版本归档，大盘开始读取这一份。"""
    dataset, message = service.publish(snapshot_id)
    if dataset is None:
        return _failure(message)
    return ActionResult(ok=True, message=message, entry=dataset)


@router.post("/{entry_id}/adjust", response_model=ActionResult)
def adjust(entry_id: int, payload: EntryPayload) -> ActionResult:
    """调整基准周期 / 偏移量 / 阈值等；修订号 +1 后旧草稿快照失效，需要重新计算。"""
    entry, errors = service.update_entry(entry_id, payload.values)
    if entry is None:
        return _failure("；".join(errors))
    return ActionResult(ok=True, message=f"配置已调整到修订 {entry.get('配置修订号')}，请重新计算基准快照", entry=entry)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条对比配置明细，附带当前已发布对比数据集。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"对比配置 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条对比配置，缺字段或口径不合法时说明原因而不是静默丢弃。"""
    entry, errors = service.create_entry(payload.values)
    if entry is None:
        return _failure("；".join(errors))
    return ActionResult(ok=True, message="对比配置已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条对比配置执行提交配置评审、启用配置、停用配置；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/{entry_id}/bundle")
def export_bundle(entry_id: int) -> dict[str, Any]:
    """对比基准打包下载：快照、数据集、指标版本与基准窗口记录一起带走。"""
    bundle = service.bundle(entry_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail=f"对比配置 {entry_id} 还没有可导出的基准快照")
    return bundle


@router.post("/import", response_model=ActionResult)
def import_bundle(payload: dict[str, Any] = Body(...)) -> ActionResult:
    """导入对比基准文件：原快照编号、指纹、生成时间与数值原样保留。"""
    dataset, message = service.import_bundle(payload)
    if dataset is None:
        return _failure(message)
    return ActionResult(ok=True, message=message, entry=dataset)
