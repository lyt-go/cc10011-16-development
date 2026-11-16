import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/dashboard'
const columns = [
  "大盘编号",
  "大盘名称",
  "包含指标组",
  "布局模板",
  "刷新频率秒",
  "对比周期",
  "分享范围",
  "负责人"
]
const actions = [
  "提交大盘评审",
  "发布大盘",
  "下线大盘"
]
const statuses = [
  "草稿",
  "大盘评审中",
  "已发布",
  "已下线"
]
const stats = [
  {
    "label": "大盘配置总数",
    "value": 0
  },
  {
    "label": "已发布大盘",
    "value": 0
  },
  {
    "label": "评审中大盘",
    "value": 0
  }
]

type Row = Record<string, string | number | null>
type ComparisonCard = Record<string, string | number | boolean | string[] | null>

export default function DashboardView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [comparison, setComparison] = useState<{
    刷新时间?: string
    读取口径?: string
    可比数?: number
    不可比数?: number
    cards?: ComparisonCard[]
  }>({})
  const [metricSnapshots, setMetricSnapshots] = useState<Row[]>([])
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const refreshComparison = useCallback(async () => {
    setNotice('')
    try {
      const response = await request('/api/comparison/dashboard')
      if (!response.ok) {
        throw new Error('同比环比对比快照读取失败')
      }
      setComparison(await response.json())
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '同比环比对比快照读取失败')
    }
  }, [])

  const refreshMetricSnapshots = useCallback(async () => {
    try {
      const response = await request('/api/comparison/metric-snapshots')
      if (response.ok) {
        const payload = await response.json()
        setMetricSnapshots(payload.items ?? [])
      }
    } catch {
      /* 指标快照读取失败不覆盖大盘主表 */
    }
  }, [])

  useEffect(() => {
    void refreshComparison()
    void refreshMetricSnapshots()
  }, [refreshComparison, refreshMetricSnapshots])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('大盘配置列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '监控大盘列表读取失败')
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
        throw new Error('监控大盘动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '监控大盘操作失败')
    }
  }

  return (
    <section className="page" data-module="dashboard">
      <header className="page-head">
        <div>
          <h2>监控大盘管理</h2>
          <p className="page-desc">配置实时监控大盘，组合指标卡片和图表形成业务全景视图。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('大盘配置登记入口尚未接入审批流')}>
            登记大盘配置
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出监控大盘清单
          </button>
          <button className="btn" type="button" onClick={() => { void refreshComparison(); void refreshMetricSnapshots() }}>
            刷新对比快照
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
              <td className="empty-state" colSpan={columns.length + 1}>暂无监控大盘数据，可先登记大盘配置</td>
            </tr>
          )}
        </tbody>
      </table>

      <section className="panel">
        <div className="panel-head">
          <h3>同比环比对比快照（只读已发布）</h3>
          <span className="hint">{comparison.读取口径 ?? ''}{comparison.刷新时间 ? ` · 刷新于 ${comparison.刷新时间}` : ''}</span>
        </div>
        <p className="page-desc">
          可比 {comparison.可比数 ?? 0} 项，不可比 {comparison.不可比数 ?? 0} 项；
          缺基准或新鲜度越界时显示不可比原因，不沿用上次数字。
        </p>
        <table className="data-table compact">
          <thead>
            <tr><th>配置</th><th>指标</th><th>对比</th><th>基准 → 当期</th><th>增长率</th><th>来源快照</th><th>不可比原因</th></tr>
          </thead>
          <tbody>
            {(comparison.cards ?? []).map((card) => (
              <tr key={String(card['配置编号'])}>
                <td>{String(card['配置编号'])}</td>
                <td>{String(card['目标指标'])}</td>
                <td>{String(card['对比类型'])}</td>
                <td>{String(card['基准周期'] ?? '—')} → {String(card['当期周期'] ?? '—')}</td>
                <td><span className={card['可比'] ? 'badge ok' : 'badge bad'}>{String(card['增长率文本'])}</span></td>
                <td>{String(card['快照编号'] ?? '—')}</td>
                <td className="reason-cell">{String(card['不可比原因'] ?? '—')}</td>
              </tr>
            ))}
            {(comparison.cards ?? []).length === 0 && (
              <tr><td className="empty-state" colSpan={7}>还没有已发布的对比快照</td></tr>
            )}
          </tbody>
        </table>

        <h4 className="sub-title">随对比基准同步的指标快照</h4>
        <table className="data-table compact">
          <thead>
            <tr><th>指标</th><th>配置</th><th>基准 → 当期</th><th>版本</th><th>增长率</th><th>数据集</th><th>同步时间</th></tr>
          </thead>
          <tbody>
            {metricSnapshots.filter((item) => item['状态'] === '当前').map((item) => (
              <tr key={String(item.id)}>
                <td>{String(item['指标编号'])} {String(item['指标名称'] ?? '')}</td>
                <td>{String(item['配置编号'] ?? '—')}</td>
                <td>{String(item['基准周期'] ?? '—')} → {String(item['当期周期'] ?? '—')}</td>
                <td>v{String(item['指标版本'] ?? '—')}</td>
                <td>{String(item['增长率文本'] ?? '—')}</td>
                <td>{String(item['数据集编号'] ?? '—')}</td>
                <td>{String(item['同步时间'] ?? '')}</td>
              </tr>
            ))}
            {metricSnapshots.filter((item) => item['状态'] === '当前').length === 0 && (
              <tr><td className="empty-state" colSpan={7}>还没有同步的指标快照</td></tr>
            )}
          </tbody>
        </table>
      </section>

      <footer className="page-foot">
        <span>共 {total} 条监控大盘记录</span>
        <span>
          {notice && <span className="ok-text">{notice}</span>}
          {errorMessage && <span className="error-text">{errorMessage}</span>}
        </span>
      </footer>
    </section>
  )
}
