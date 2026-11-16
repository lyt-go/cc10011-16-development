"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。

除业务模块表之外，还有一组不参与运营概览的辅助表（指标窗口聚合记录、基准快照、
对比数据集、状态记录等），以及一个「整表快照提交」的事务对象：事务内所有改动先落在
副本上，提交时在锁内整体替换，回滚时直接丢弃副本，因此不会出现只写了一半的配置、
快照和状态记录。
"""
from __future__ import annotations

import copy
import threading
from datetime import datetime, timezone
from typing import Any, Iterator

from app.seed import SEED_ROWS
from app.seed_runtime import apply_runtime_seed

AUX_TABLES = (
    "metric_window_aggregate",
    "metric_version",
    "metric_live_state",
    "metric_snapshot",
    "comparison_snapshot",
    "comparison_dataset",
    "comparison_journal",
    "sync_journal",
)


class StoreTransaction:
    """一份可整体提交或整体丢弃的仓库副本。

    业务代码只通过它读写数据；`commit()` 之前任何异常都不会影响正式仓库。
    """

    def __init__(self, store: "Store") -> None:
        self._store = store
        self._tables: dict[str, list[dict[str, Any]]] = copy.deepcopy(store._tables)
        self._aux: dict[str, list[dict[str, Any]]] = copy.deepcopy(store._aux)
        self._finished = False

    # --- 读取 ---
    def rows(self, name: str) -> list[dict[str, Any]]:
        if name in self._tables:
            return self._tables[name]
        return self._aux.setdefault(name, [])

    def find(self, name: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(name):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def next_id(self, name: str) -> int:
        return max((int(row.get("id", 0)) for row in self.rows(name)), default=0) + 1

    def append(self, name: str, values: dict[str, Any]) -> dict[str, Any]:
        row = {"id": self.next_id(name)}
        row.update(values)
        self.rows(name).append(row)
        return row

    def journal(self, table: str, event: dict[str, Any]) -> dict[str, Any]:
        """状态记录跟业务改动写在同一份事务副本里，提交时一起生效。"""
        return self.append(table, event)

    def commit(self) -> None:
        if self._finished:
            raise RuntimeError("事务已经结束，不能重复提交")
        with self._store._lock:
            self._store._tables = self._tables
            self._store._aux = self._aux
        self._finished = True

    def rollback(self) -> None:
        self._finished = True

    def __enter__(self) -> "StoreTransaction":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        if not self._finished:
            self.rollback()
        return False


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        self._aux: dict[str, list[dict[str, Any]]] = {name: [] for name in AUX_TABLES}
        self._lock = threading.RLock()
        apply_runtime_seed(self._tables, self._aux, datetime.now(timezone.utc))

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def aux_rows(self, name: str) -> list[dict[str, Any]]:
        return self._aux.setdefault(name, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def transaction(self) -> StoreTransaction:
        return StoreTransaction(self)

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
