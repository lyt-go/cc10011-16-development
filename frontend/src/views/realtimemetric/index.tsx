import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/realtimemetric'
const columns = [
  "指标编号",
  "指标名称",
  "计算窗口秒",
  "聚合函数",
  "数据源流",
  "维度字段组",
  "延迟容忍毫秒",
  "精度位数"
]
const actions = [
  "提交定义评审",
  "上线指标",
  "下线指标"
]
const statuses = [
  "草稿",
  "定义评审中",
  "已上线",
  "已下线"
]
const stats = [
  {
    "label": "指标定义总数",
    "value": 0
  },
  {
    "label": "已上线指标",
    "value": 0
  },
  {
    "label": "评审中指标",
    "value": 0
  }
]

type Row = Record<string, string | number | null>
type SyncBatch = Record<string, string | number | string[] | null>

const sampleBatch = {
  records: [
    {
      指标编号: 'REAL-0003',
      窗口起: new Date().toISOString().slice(0, 10),
      窗口止: new Date().toISOString(),
      指标版本: 1,
      聚合值: 52000,
      数据时点: new Date().toISOString(),
      记录生成时间: new Date().toISOString(),
    },
  ],
}

export default function RealtimemetricView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [syncState, setSyncState] = useState<{
    live: Row[]
    batches: SyncBatch[]
    windows: Row[]
  }>({ live: [], batches: [], windows: [] })
  const [syncInput, setSyncInput] = useState(JSON.stringify(sampleBatch, null, 2))
  const [metricSnapshots, setMetricSnapshots] = useState<Row[]>([])
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reloadSync = useCallback(async () => {
    try {
      const response = await request(`${ENDPOINT}/sync/state`)
      if (response.ok) {
        const payload = await response.json()
        setSyncState({ live: payload.live ?? [], batches: payload.batches ?? [], windows: payload.windows ?? [] })
      }
    } catch {
      /* 同步面板读取失败不覆盖主表错误 */
    }
  }, [])

  useEffect(() => {
    void reloadSync()
  }, [reloadSync])

  const reloadSnapshots = useCallback(async () => {
    try {
      const response = await request('/api/comparison/metric-snapshots')
      if (response.ok) {
        const payload = await response.json()
        setMetricSnapshots(payload.items ?? [])
      }
    } catch {
      /* 指标快照读取失败不覆盖主表错误 */
    }
  }, [])

  useEffect(() => {
    void reloadSnapshots()
  }, [reloadSnapshots])

  async function runSync() {
    setErrorMessage('')
    setNotice('')
    try {
      const parsed = JSON.parse(syncInput)
      const response = await request(`${ENDPOINT}/sync/windows`, {
        method: 'POST',
        body: JSON.stringify({ values: parsed }),
      })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok || !payload.ok) {
        throw new Error(payload.message ?? '指标窗口聚合记录同步失败')
      }
      setNotice(payload.message)
      await reloadSync()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '指标窗口聚合记录同步失败')
    }
  }

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('指标定义列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '实时指标列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ values: { action } }),
      })
      if (!response.ok) {
        throw new Error('实时指标动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '实时指标操作失败')
    }
  }

  return (
    <section className="page" data-module="realtimemetric">
      <header className="page-head">
        <div>
          <h2>实时指标管理</h2>
          <p className="page-desc">定义实时计算指标，配置计算窗口、聚合逻辑和延迟容忍。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('指标定义登记入口尚未接入审批流')}>
            登记指标定义
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出实时指标清单
          </button>
          <button className="btn" type="button" onClick={() => { void reloadSync(); void reloadSnapshots() }}>
            刷新同步状态
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无实时指标数据，可先登记指标定义</td>
            </tr>
          )}
        </tbody>
      </table>

      <section className="panel">
        <div className="panel-head">
          <h3>实时指标同步（消费指标窗口聚合记录）</h3>
          <span className="hint">写入键：指标编号 + 窗口起 + 指标版本；整批成功或整批回滚</span>
        </div>
        <div className="split">
          <div>
            <h4 className="sub-title">当前实时值</h4>
            <table className="data-table compact">
              <thead>
                <tr><th>指标</th><th>最新窗口</th><th>版本</th><th>聚合值</th><th>数据时点</th><th>批次</th></tr>
              </thead>
              <tbody>
                {syncState.live.map((item) => (
                  <tr key={String(item['指标编号'])}>
                    <td>{String(item['指标编号'])}</td>
                    <td>{String(item['最新窗口起'] ?? '—')}</td>
                    <td>v{String(item['指标版本'] ?? '—')}</td>
                    <td>{String(item['聚合值'] ?? '—')}</td>
                    <td>{String(item['数据时点'] ?? '—')}</td>
                    <td>{String(item['同步批次'] ?? '—')}</td>
                  </tr>
                ))}
                {syncState.live.length === 0 && (
                  <tr><td className="empty-state" colSpan={6}>还没有同步过的实时值</td></tr>
                )}
              </tbody>
            </table>

            <h4 className="sub-title">同步批次状态记录</h4>
            <table className="data-table compact">
              <thead>
                <tr><th>批次</th><th>结果</th><th>接收</th><th>写入</th><th>覆盖</th><th>跳过</th><th>说明</th></tr>
              </thead>
              <tbody>
                {syncState.batches.map((item, index) => (
                  <tr key={`${String(item['批次号'])}-${index}`}>
                    <td>{String(item['批次号'] ?? '—')}</td>
                    <td>{String(item['结果'] ?? '')}</td>
                    <td>{String(item['接收条数'] ?? '—')}</td>
                    <td>{String(item['写入条数'] ?? '—')}</td>
                    <td>{String(item['覆盖条数'] ?? '—')}</td>
                    <td>{String(item['跳过条数'] ?? '—')}</td>
                    <td className="reason-cell">{String(item['说明'] ?? '')}</td>
                  </tr>
                ))}
                {syncState.batches.length === 0 && (
                  <tr><td className="empty-state" colSpan={7}>还没有同步批次</td></tr>
                )}
              </tbody>
            </table>
          </div>
          <div>
            <h4 className="sub-title">提交一批窗口聚合记录</h4>
            <textarea
              className="json-input"
              value={syncInput}
              spellCheck={false}
              onChange={(event) => setSyncInput(event.target.value)}
            />
            <div className="panel-actions">
              <button className="btn primary" type="button" onClick={() => void runSync()}>消费这一批</button>
              <span className="hint">任意一条校验失败时整批回滚，只保留失败状态记录</span>
            </div>
            <h4 className="sub-title">最近窗口聚合记录</h4>
            <table className="data-table compact">
              <thead>
                <tr><th>指标</th><th>窗口起</th><th>版本</th><th>聚合值</th><th>数据时点</th></tr>
              </thead>
              <tbody>
                {syncState.windows.slice(0, 12).map((item) => (
                  <tr key={String(item.id)}>
                    <td>{String(item['指标编号'])}</td>
                    <td>{String(item['窗口起'])}</td>
                    <td>v{String(item['指标版本'])}</td>
                    <td>{String(item['聚合值'])}</td>
                    <td>{String(item['数据时点'])}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h3>指标快照（跟随对比基准同步）</h3>
          <span className="hint">由同比环比发布基准快照时写入，同一对比配置只保留一份“当前”</span>
        </div>
        <table className="data-table compact">
          <thead>
            <tr><th>指标</th><th>配置</th><th>对比</th><th>基准 → 当期</th><th>版本</th><th>增长率</th><th>不可比原因</th><th>同步时间</th></tr>
          </thead>
          <tbody>
            {metricSnapshots.filter((item) => item['状态'] === '当前').map((item) => (
              <tr key={String(item.id)}>
                <td>{String(item['指标编号'])} {String(item['指标名称'] ?? '')}</td>
                <td>{String(item['配置编号'] ?? '—')}</td>
                <td>{String(item['对比类型'] ?? '—')}</td>
                <td>{String(item['基准周期'] ?? '—')} → {String(item['当期周期'] ?? '—')}</td>
                <td>v{String(item['指标版本'] ?? '—')}</td>
                <td>{String(item['增长率文本'] ?? '—')}</td>
                <td className="reason-cell">{String(item['不可比原因'] ?? '—')}</td>
                <td>{String(item['同步时间'] ?? '')}</td>
              </tr>
            ))}
            {metricSnapshots.filter((item) => item['状态'] === '当前').length === 0 && (
              <tr><td className="empty-state" colSpan={8}>还没有随对比基准同步的指标快照</td></tr>
            )}
          </tbody>
        </table>
      </section>

      <footer className="page-foot">
        <span>共 {total} 条实时指标记录</span>
        <span>
          {notice && <span className="ok-text">{notice}</span>}
          {errorMessage && <span className="error-text">{errorMessage}</span>}
        </span>
      </footer>
    </section>
  )
}
