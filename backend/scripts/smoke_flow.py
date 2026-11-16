"""端到端校验：配置 → 预检 → 计算 → 发布 → 大盘读 → 指标快照 → 打包导入 → 同步回滚。"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8012"


def call(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def show(label, payload):
    print(f"--- {label}")
    print(json.dumps(payload, ensure_ascii=False, indent=2)[:1200])


# 1) 概览与配置
status, cfg = call("/api/comparison/4")
print("config 4:", status, cfg["配置编号"], cfg["基准周期"], cfg["status"], "published=", cfg["已发布数据集"])
status, pre = call("/api/comparison/check?" + urllib.parse.urlencode({"metric": "REAL-0003", "comparison_type": "环比", "period": "2023-01", "offset": "1"}))
show("check", pre)

# 2) 计算 COMP-0004（历史区间无数据 → 不可比）
status, result = call("/api/comparison/4/compute", "POST", {})
print("compute:", status, result["ok"], result["message"])
snapshot = result["entry"]["snapshot"]
print(" snapshot:", snapshot["快照编号"], "原因:", snapshot["不可比原因"])
print(" 基准窗口记录:", len(snapshot["基准窗口记录"]), "指纹:", snapshot["快照指纹"])
snapshot_id = snapshot["id"]

# 3) 发布
status, published = call(f"/api/comparison/snapshots/{snapshot_id}/publish", "POST", {})
print("publish:", status, published["ok"], published["message"])
print(" dataset:", published["entry"]["数据集编号"], published["entry"]["状态"])

# 4) 同一业务键只有一个当前版本
status, versions = call("/api/comparison/datasets?business_key=COMP-0004")
print("versions:", [(row["数据集编号"], row["版本号"], row["状态"]) for row in versions["items"]])

# 5) 指标快照同步
status, snaps = call("/api/comparison/metric-snapshots")
print("metric snapshots:", [(row["配置编号"], row["状态"], row["数据集编号"]) for row in snaps["items"]])

# 6) 大盘读取
status, dash = call("/api/comparison/dashboard")
card = next(item for item in dash["cards"] if item["配置编号"] == "COMP-0004")
print("dashboard COMP-0004:", card["状态"], card["不可比原因"])
card1 = next(item for item in dash["cards"] if item["配置编号"] == "COMP-0001")
print("dashboard COMP-0001:", card1["状态"], card1["增长率文本"], card1["数据来源"], card1["快照编号"])

# 7) 打包导出
status, bundle = call("/api/comparison/1/bundle")
print("bundle:", status, bundle["格式"], bundle["快照"]["快照编号"], len(bundle["基准窗口记录"]))
original_code = bundle["快照"]["快照编号"]
original_fp = bundle["快照"]["快照指纹"]
original_growth = bundle["快照"]["增长率"]

# 8) 导入到 COMP-0005（同文件、原快照保留）
bundle["快照"]["配置编号"] = "COMP-0005"
status, imported = call("/api/comparison/import", "POST", bundle)
print("import:", status, imported["ok"], imported["message"])
print(" imported dataset:", imported["entry"]["数据集编号"], imported["entry"]["快照编号"], imported["entry"]["增长率"], imported["entry"]["原快照编号"])
print(" snapshot preserved growth:", imported["entry"]["增长率"] == original_growth)
status, snaps5 = call("/api/comparison/snapshots?entry_id=5")
print(" snapshot 5:", [(row["快照编号"], row["原快照编号"], row["状态"], row["来源"]) for row in snaps5["items"]])

# 9) 重复导入同一指纹 → 幂等
status, again = call("/api/comparison/import", "POST", bundle)
print("import again:", status, again["ok"], again["message"])

# 10) 同步：一批合法 + 一条非法 → 整批回滚
status, state = call("/api/realtimemetric/sync/state")
before = len(state["windows"])
batch = {
    "records": [
        {
            "指标编号": "REAL-0003",
            "窗口起": "2026-11-16",
            "窗口止": "2026-11-16T23:59:59+00:00",
            "指标版本": 1,
            "聚合值": 99999.99,
            "数据时点": "2026-11-16T08:55:00+00:00",
            "记录生成时间": "2026-11-16T08:55:00+00:00",
        },
        {
            "指标编号": "",
            "窗口起": "2026-11-16",
            "指标版本": 1,
            "聚合值": 1,
            "数据时点": "2026-11-16T08:55:00+00:00",
        },
    ]
}
status, sync = call("/api/realtimemetric/sync/windows", "POST", {"values": {"records": batch["records"]}})
print("sync bad:", status, sync["ok"], sync["message"])
status, state = call("/api/realtimemetric/sync/state")
after = len(state["windows"])
print(" window count before/after:", before, after, "live:", state["live"])
print(" batches:", [(row["结果"], row["说明"]) for row in state["batches"]])

# 11) 合法同步
batch["records"] = batch["records"][:1]
status, sync = call("/api/realtimemetric/sync/windows", "POST", {"values": {"records": batch["records"]}})
print("sync good:", status, sync["ok"], sync["message"])
status, state = call("/api/realtimemetric/sync/state")
print(" live now:", state["live"])

# 12) 计算草稿 → 调整基准周期（同一业务键出现两个当前版本）→ 旧草稿作废、只留一个发布版
status, draft = call("/api/comparison/5/compute", "POST", {})
draft_id = draft["entry"]["snapshot"]["id"]
print("compute draft 5:", status, draft["entry"]["snapshot"]["快照编号"], draft["entry"]["snapshot"]["不可比原因"])
status, datasets5 = call("/api/comparison/datasets?business_key=COMP-0005")
print("versions after draft:", [(row["数据集编号"], row["版本号"], row["状态"]) for row in datasets5["items"]])
status, adjust = call("/api/comparison/5/adjust", "POST", {"values": {"基准周期": "2024-06", "偏移量": "12"}})
print("adjust:", status, adjust["ok"], "修订号", adjust["entry"].get("配置修订号"))
status, old_publish = call(f"/api/comparison/snapshots/{draft_id}/publish", "POST", {})
print("publish stale snapshot:", status, old_publish["ok"], old_publish["message"])
status, datasets5 = call("/api/comparison/datasets?business_key=COMP-0005")
print("versions after adjust:", [(row["数据集编号"], row["版本号"], row["状态"]) for row in datasets5["items"]])
current = [row for row in datasets5["items"] if row["状态"] in ("草稿", "已发布")]
print("current dataset count:", len(current))

status, journal = call("/api/comparison/journal?" + urllib.parse.urlencode({"business_key": "COMP-0005"}))
print("journal COMP-0005:", [(row["事件"], row["结果"]) for row in journal["items"]])
