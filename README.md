# 实时指标计算与监控大盘平台

面向实时指标计算引擎、多维度下钻分析、告警阈值管理、指标异常检测、数据服务开放与可视化看板配置的数据分析与应用工程平台。

这是一个前后端分离的管理平台：前端 React + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 React + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 实时指标 | `realtimemetric` | 指标定义 | 指标编号、指标名称、计算窗口秒 |
| 监控大盘 | `dashboard` | 大盘配置 | 大盘编号、大盘名称、包含指标组 |
| 多维下钻 | `drilldown` | 下钻路径 | 路径编号、起始维度、下钻层级序列 |
| 阈值管理 | `threshold` | 阈值规则 | 规则编号、关联指标、阈值下限 |
| 异常检测 | `anomalydetect` | 检测模型 | 模型编号、监控指标、检测算法 |
| 数据服务 | `dataservice` | 服务接口 | 服务编号、服务名称、接口路径 |
| 告警历史 | `alarmhistory` | 告警记录 | 记录编号、告警规则、触发时间 |
| 同比环比 | `comparison` | 对比配置 | 配置编号、目标指标、对比类型 |
| 聚合配置 | `aggregate` | 聚合规则 | 规则编号、源数据粒度、目标粒度 |
| 筛选预设 | `filterpreset` | 预设模板 | 预设编号、预设名称、维度条件组 |
| 用户洞察 | `userinsight` | 洞察报告 | 报告编号、分析主题、目标人群 |
| 事件分析 | `eventanalysis` | 分析模型 | 模型编号、事件名称、分组维度 |
| 分群画像 | `segmentprofile` | 画像定义 | 画像编号、分群名称、筛选规则 |
| 趋势预测 | `trendforecast` | 预测方案 | 方案编号、预测指标、算法模型 |
| 可视化模板 | `viztemplate` | 图表模板 | 模板编号、图表类型、数据绑定 |
| 订阅推送 | `subscription` | 订阅配置 | 订阅编号、订阅内容、接收人 |
| 维度映射 | `dimensionmap` | 映射规则 | 规则编号、原始维度名、标准维度名 |
| 数据质量分 | `qualityscore` | 评分规则 | 规则编号、数据集名称、完整性权重 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 同比环比：基准快照与新鲜度准入

同比环比不是直接拿实时指标现算，而是「配置 → 基准快照 → 发布数据集 → 大盘读取」四步：

1. **配置**（`POST /api/comparison`）保存目标指标、对比类型、基准周期、偏移量、差异阈值、
   显示格式、对比维度、新鲜度上限秒与可选指标版本。基准周期支持 `2026-10-16`、
   `2026-10`、`2026-W42`、`2026`、`2026-10-01~2026-10-15`，落库后原样冻结。
2. **计算**（`POST /api/comparison/{id}/compute`）读取指标窗口聚合记录与指标版本，
   先做版本裁决和新鲜度判定，再算差异与增长率，产出基准快照与对比草稿数据集。
   不可比时数值为空、原因写进 `不可比原因`/`全部不可比原因`，不会给出增长的伪结论。
3. **发布**（`POST /api/comparison/snapshots/{id}/publish`）把草稿转成已发布，
   同一业务键的旧发布版本归档；另一个页面的指标快照同步更新。
4. **大盘读取**（`GET /api/comparison/dashboard`）只读已发布数据集，并在刷新时刻
   重新判一次新鲜度；缺基准或越界时返回可解释的不可比结果，不回退历史数字。

### 版本重叠的取舍

基准周期与新旧指标版本同时重叠时，本实现选择「单一版本」而不是「按日期拼接」：
取覆盖整个周期的最新版本，其余版本只记录为备选。这样每个对比结果都只对应一个基准
周期快照和一个版本裁决，事后能回答“用的是哪一版指标、哪一段数据”；代价是跨版本
周期会判为不可比，而不是给一个混合口径的近似值。

### 事务与版本

- 同一业务键（配置编号）任何时刻只有一个「当前」对比数据集：草稿或已发布。
- 计算 / 发布 / 导入都在仓库副本上完成，提交时整体替换；失败只留一条失败状态记录，
  不会残留半张配置、半个快照或半条数据集。
- 状态记录见 `GET /api/comparison/journal`，数据集版本见 `GET /api/comparison/datasets`。

### 导出导入

`GET /api/comparison/{id}/bundle` 打包基准快照（含快照编号、指纹、指标版本、
基准窗口记录）；`POST /api/comparison/import` 导入时原快照编号、指纹、生成时间与数值
原样保留，不做按导入时刻的重算，同一指纹重复导入不会重复落库。

### 实时指标同步

`POST /api/realtimemetric/sync/windows` 消费指标窗口聚合记录，写入键为
「指标编号 + 窗口起 + 指标版本」。同键旧记录被拒绝覆盖，新记录替换；版本越界的记录
跳过并写入问题清单；任意一条硬校验失败整批回滚。现状读取 `GET /api/realtimemetric/sync/state`。


领域：数据工程
子方向：数据分析与数据应用
