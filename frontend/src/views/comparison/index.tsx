import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/comparison'
const columns = [
  '配置编号',
  '目标指标',
  '对比类型',
  '基准周期',
  '偏移量',
  '差异阈值',
  '显示格式',
  '对比维度',
]
const actions = ['提交配置评审', '启用配置', '停用配置']
const statuses = ['草稿', '配置评审中', '已启用', '已停用']

type Row = Record<string, string | number | boolean | null>
type Bundle = Record<string, unknown>

const emptyForm = {
  配置编号: '',
  目标指标: 'REAL-0001',
  对比类型: '同比',
  基准周期: '',
  偏移量: '12',
  差异阈值: '5',
  显示格式: '百分比(两位小数)',
  对比维度: '全部',
  新鲜度上限秒: '7200',
  指标版本: '',
}

type Card = Record<string, string | number | boolean | null>
type Snapshot = Row & {
  基准窗口记录?: Row[]
  当期窗口记录?: Row[]
  追溯?: Record<string, unknown>
  全部不可比原因?: string[]
  口径提示?: string[]
}

export default function ComparisonView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [form, setForm] = useState<Record<string, string>>(emptyForm)
  const [showForm, setShowForm] = useState(false)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const [datasets, setDatasets] = useState<Row[]>([])
  const [published, setPublished] = useState<Row | null>(null)
  const [journal, setJournal] = useState<Row[]>([])
  const [dashboard, setDashboard] = useState<Record<string, unknown> | null>(null)
  const [metricSnapshots, setMetricSnapshots] = useState<Row[]>([])
  const [options, setOptions] = useState<{ 对比类型: string[]; 显示格式: string[]; 指标: Row[] }>({
    对比类型: ['同比', '环比'],
    显示格式: ['百分比(两位小数)'],
    指标: [],
  })
  const [latestSnapshot, setLatestSnapshot] = useState<Snapshot | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const filterFields = useMemo(() => ['配置编号', '目标指标', '状态'], [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams()
    if (filters['配置编号']) query.set('keyword', filters['配置编号'])
    if (filters['目标指标']) query.set('metric', filters['目标指标'])
    if (filters['状态']) query.set('status', filters['状态'])
    try {
      const response = await request(`${ENDPOINT}?${query.toString()}`)
      if (!response.ok) {
        throw new Error('对比配置列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '同比环比列表读取失败')
    }
  }, [filters])

  const reloadSide = useCallback(async () => {
    try {
      const [dashboardResponse, metricResponse] = await Promise.all([
        request(`${ENDPOINT}/dashboard`),
        request(`${ENDPOINT}/metric-snapshots`),
      ])
      if (dashboardResponse.ok) {
        setDashboard(await dashboardResponse.json())
      }
      if (metricResponse.ok) {
        const payload = await metricResponse.json()
        setMetricSnapshots(payload.items ?? [])
      }
    } catch {
      /* 侧栏读取失败不覆盖主表错误 */
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  useEffect(() => {
    void reloadSide()
  }, [reloadSide])

  useEffect(() => {
    request(`${ENDPOINT}/options`)
      .then((response) => (response.ok ? response.json() : null))
      .then((payload) => {
        if (payload) {
          setOptions(payload)
        }
      })
      .catch(() => undefined)
  }, [])

  async function selectRow(row: Row) {
    const entryId = Number(row.id)
    setSelectedId(entryId)
    setLatestSnapshot(null)
    setForm({
      配置编号: String(row['配置编号'] ?? ''),
      目标指标: String(row['目标指标'] ?? ''),
      对比类型: String(row['对比类型'] ?? '同比'),
      基准周期: String(row['基准周期'] ?? ''),
      偏移量: String(row['偏移量'] ?? '1'),
      差异阈值: String(row['差异阈值'] ?? ''),
      显示格式: String(row['显示格式'] ?? '百分比(两位小数)'),
      对比维度: String(row['对比维度'] ?? '全部'),
      新鲜度上限秒: String(row['新鲜度上限秒'] ?? '7200'),
      指标版本: String(row['指标版本'] ?? ''),
    })
    try {
      const [detail, snapshotResponse, datasetResponse] = await Promise.all([
        request(`${ENDPOINT}/${entryId}`),
        request(`${ENDPOINT}/snapshots?entry_id=${entryId}`),
        request(`${ENDPOINT}/datasets?entry_id=${entryId}`),
      ])
      if (detail.ok) {
        const payload = await detail.json()
        setPublished((payload['已发布数据集'] as Row) ?? null)
      }
      if (snapshotResponse.ok) {
        const payload = await snapshotResponse.json()
        setSnapshots(payload.items ?? [])
      }
      if (datasetResponse.ok) {
        const payload = await datasetResponse.json()
        setDatasets(payload.items ?? [])
      }
      const businessKey = String(row['配置编号'] ?? '')
      const journalResponse = await request(`${ENDPOINT}/journal?business_key=${encodeURIComponent(businessKey)}`)
      if (journalResponse.ok) {
        const payload = await journalResponse.json()
        setJournal(payload.items ?? [])
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '对比明细读取失败')
    }
  }

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ values: { action } }),
      })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok || !payload.ok) {
        throw new Error(payload.message ?? '同比环比动作未生效，请稍后重试')
      }
      setNotice(payload.message)
      await reload()
      await selectRow(row)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '同比环比操作失败')
    }
  }

  async function createConfig() {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(ENDPOINT, {
        method: 'POST',
        body: JSON.stringify({ values: form }),
      })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok || !payload.ok) {
        throw new Error(payload.message ?? '对比配置登记失败')
      }
      setNotice(payload.message)
      setShowForm(false)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '对比配置登记失败')
    }
  }

  async function compute(row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/compute`, { method: 'POST' })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok || !payload.ok) {
        throw new Error(payload.message ?? '基准快照计算失败')
      }
      setNotice(payload.message)
      await reload()
      await selectRow(row)
      setLatestSnapshot(payload.entry?.snapshot ?? null)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '基准快照计算失败')
    }
  }

  async function publish(snapshot: Snapshot) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/snapshots/${snapshot.id}/publish`, { method: 'POST' })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok || !payload.ok) {
        throw new Error(payload.message ?? '快照发布失败')
      }
      setNotice(payload.message)
      await reload()
      await reloadSide()
      if (selectedId !== null) {
        const row = rows.find((item) => Number(item.id) === selectedId)
        if (row) {
          await selectRow(row)
        }
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '快照发布失败')
    }
  }

  async function adjust() {
    if (selectedId === null) {
      setErrorMessage('请先在列表中选择一条对比配置')
      return
    }
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${selectedId}/adjust`, {
        method: 'POST',
        body: JSON.stringify({ values: form }),
      })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok || !payload.ok) {
        throw new Error(payload.message ?? '配置调整失败')
      }
      setNotice(payload.message)
      await reload()
      const row = rows.find((item) => Number(item.id) === selectedId)
      if (row) {
        await selectRow(row)
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '配置调整失败')
    }
  }

  async function downloadBundle(row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/bundle`)
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) {
        throw new Error(payload.detail ?? '该配置还没有可导出的基准快照')
      }
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `comparison-baseline-${row['配置编号']}.json`
      anchor.click()
      URL.revokeObjectURL(url)
      setNotice(`已导出${row['配置编号']} 的基准快照 ${payload['快照']?.['快照编号'] ?? ''}`)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '基准快照导出失败')
    }
  }

  async function importBundle(file: File) {
    setErrorMessage('')
    setNotice('')
    try {
      const text = await file.text()
      const bundle = JSON.parse(text) as Bundle
      const response = await request(`${ENDPOINT}/import`, {
        method: 'POST',
        body: JSON.stringify(bundle),
      })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok || !payload.ok) {
        throw new Error(payload.message ?? '基准快照导入失败')
      }
      setNotice(payload.message)
      await reload()
      await reloadSide()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '基准快照导入失败')
    } finally {
      if (fileRef.current) {
        fileRef.current.value = ''
      }
    }
  }

  const cards = ((dashboard?.cards as Card[]) ?? [])
  const comparable = cards.filter((card) => card['可比'])
  const publishedCount = Number(dashboard?.['数据集总数'] ?? 0)
  const selectedRow = rows.find((row) => Number(row.id) === selectedId) ?? null

  return (
    <section className="page" data-module="comparison">
      <header className="page-head">
        <div>
          <h2>同比环比管理</h2>
          <p className="page-desc">
            保存目标指标、对比类型、基准周期、偏移量、差异阈值与显示格式；计算前先确认指标版本和数据新鲜度，
            输出可追溯的增长率、基准快照与不可比原因。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setShowForm((prev) => !prev)}>
            {showForm ? '收起登记表单' : '登记对比配置'}
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出同比环比清单
          </button>
          <button className="btn" type="button" onClick={() => fileRef.current?.click()}>
            导入对比基准
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="application/json,.json"
            style={{ display: 'none' }}
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) {
                void importBundle(file)
              }
            }}
          />
        </div>
      </header>

      <div className="stat-row">
        <article className="stat-card">
          <span className="stat-label">对比配置总数</span>
          <strong className="stat-value">{total}</strong>
        </article>
        <article className="stat-card">
          <span className="stat-label">已发布对比快照</span>
          <strong className="stat-value">{publishedCount}</strong>
        </article>
        <article className="stat-card">
          <span className="stat-label">大盘可比</span>
          <strong className="stat-value">{comparable.length}</strong>
        </article>
        <article className="stat-card">
          <span className="stat-label">大盘不可比</span>
          <strong className="stat-value">{cards.length - comparable.length}</strong>
        </article>
      </div>

      {showForm && (
        <section className="panel">
          <div className="panel-head">
            <h3>登记 / 调整对比配置</h3>
            <span className="hint">调整已有配置时，先在上方列表选中一行，再点“应用为调整”</span>
          </div>
          <div className="grid-form">
            {[
              ['配置编号', 'COMP-0006'],
              ['目标指标', 'REAL-0001'],
              ['对比类型', '同比'],
              ['基准周期', '2025-09 或 2025-09-01~2025-09-30'],
              ['偏移量', '12'],
              ['差异阈值', '5'],
              ['显示格式', '百分比(两位小数)'],
              ['对比维度', '全部'],
              ['新鲜度上限秒', '7200'],
              ['指标版本', '留空表示自动裁决'],
            ].map(([field, placeholder]) => (
              <label key={field} className="filter-item">
                <span>{field}</span>
                {field === '对比类型' ? (
                  <select
                    value={form[field]}
                    onChange={(event) => setForm((prev) => ({ ...prev, [field]: event.target.value }))}
                  >
                    {options.对比类型.map((item) => (
                      <option key={item} value={item}>{item}</option>
                    ))}
                  </select>
                ) : field === '显示格式' ? (
                  <select
                    value={form[field]}
                    onChange={(event) => setForm((prev) => ({ ...prev, [field]: event.target.value }))}
                  >
                    {options.显示格式.map((item) => (
                      <option key={item} value={item}>{item}</option>
                    ))}
                  </select>
                ) : field === '目标指标' ? (
                  <select
                    value={form[field]}
                    onChange={(event) => setForm((prev) => ({ ...prev, [field]: event.target.value }))}
                  >
                    {options.指标.map((item) => (
                      <option key={String(item['指标编号'])} value={String(item['指标编号'])}>
                        {String(item['指标编号'])} {String(item['指标名称'] ?? '')}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    value={form[field] ?? ''}
                    placeholder={placeholder}
                    onChange={(event) => setForm((prev) => ({ ...prev, [field]: event.target.value }))}
                  />
                )}
              </label>
            ))}
          </div>
          <div className="panel-actions">
            <button className="btn primary" type="button" onClick={() => void createConfig()}>
              登记为新配置
            </button>
            <button className="btn" type="button" onClick={() => void adjust()}>
              应用为调整{selectedRow ? `（${selectedRow['配置编号']}）` : ''}
            </button>
            <span className="hint">基准周期一旦调整，配置修订号 +1，旧快照必须重新计算后才能发布。</span>
          </div>
        </section>
      )}

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            {field === '状态' ? (
              <select
                value={filters[field] ?? ''}
                onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
              >
                <option value="">全部</option>
                {statuses.map((status) => (
                  <option key={status} value={status}>{status}</option>
                ))}
              </select>
            ) : (
              <input
                value={filters[field] ?? ''}
                placeholder={`按${field}检索`}
                onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
              />
            )}
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>状态</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={String(row.id)}
              className={Number(row.id) === selectedId ? 'row-selected' : undefined}
              onClick={() => void selectRow(row)}
            >
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td>{row.status}</td>
              <td className="row-actions" onClick={(event) => event.stopPropagation()}>
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
                <button className="link" type="button" onClick={() => void compute(row)}>计算基准快照</button>
                <button className="link" type="button" onClick={() => void downloadBundle(row)}>下载基准</button>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 2}>暂无同比环比数据，可先登记对比配置</td>
            </tr>
          )}
        </tbody>
      </table>

      {(notice || errorMessage) && (
        <footer className="page-foot">
          <span>{notice && <span className="ok-text">{notice}</span>}</span>
          <span>{errorMessage && <span className="error-text">{errorMessage}</span>}</span>
        </footer>
      )}

      {selectedRow && (
        <section className="panel">
          <div className="panel-head">
            <h3>基准快照与版本（{String(selectedRow['配置编号'])}）</h3>
            <span className="hint">
              当前发布数据集：{published ? `${String(published['数据集编号'])} v${String(published['版本号'])}` : '无'}
              ；同一业务键只允许一个草稿或已发布的当前数据集
            </span>
          </div>

          {latestSnapshot && <SnapshotDetail snapshot={latestSnapshot} onPublish={publish} />}

          <div className="split">
            <div>
              <h4 className="sub-title">基准快照</h4>
              <table className="data-table compact">
                <thead>
                  <tr>
                    <th>快照编号</th><th>状态</th><th>基准/当期</th><th>版本</th><th>增长率</th><th>不可比原因</th><th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {snapshots.map((snapshot) => (
                    <tr key={String(snapshot.id)}>
                      <td>{String(snapshot['快照编号'])}</td>
                      <td>{String(snapshot['状态'])}</td>
                      <td>{String(snapshot['基准周期'])} / {String(snapshot['当期周期'])}</td>
                      <td>v{String(snapshot['基准指标版本'] ?? '—')}{snapshot['版本冲突'] ? '（冲突）' : ''}</td>
                      <td>{String(snapshot['增长率文本'] ?? '—')}</td>
                      <td className="reason-cell">{String(snapshot['不可比原因'] ?? '—')}</td>
                      <td>
                        {snapshot['状态'] === '已计算' && (
                          <button className="link" type="button" onClick={() => void publish(snapshot)}>发布</button>
                        )}
                      </td>
                    </tr>
                  ))}
                  {snapshots.length === 0 && (
                    <tr><td className="empty-state" colSpan={7}>还没有基准快照，点“计算基准快照”生成</td></tr>
                  )}
                </tbody>
              </table>
            </div>
            <div>
              <h4 className="sub-title">对比数据集版本</h4>
              <table className="data-table compact">
                <thead>
                  <tr><th>数据集</th><th>版本</th><th>状态</th><th>快照</th><th>增长率</th><th>来源</th></tr>
                </thead>
                <tbody>
                  {datasets.map((dataset) => (
                    <tr key={String(dataset.id)}>
                      <td>{String(dataset['数据集编号'])}</td>
                      <td>v{String(dataset['版本号'])}</td>
                      <td>{String(dataset['状态'])}</td>
                      <td>{String(dataset['快照编号'])}</td>
                      <td>{String(dataset['增长率文本'] ?? '—')}</td>
                      <td>{String(dataset['来源'] ?? '')}</td>
                    </tr>
                  ))}
                  {datasets.length === 0 && (
                    <tr><td className="empty-state" colSpan={6}>还没有对比数据集</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <h4 className="sub-title">状态记录</h4>
          <table className="data-table compact">
            <thead>
              <tr><th>时间</th><th>事件</th><th>结果</th><th>快照</th><th>数据集</th><th>说明</th></tr>
            </thead>
            <tbody>
              {journal.map((item) => (
                <tr key={String(item.id)}>
                  <td>{String(item['时间'] ?? '')}</td>
                  <td>{String(item['事件'] ?? '')}</td>
                  <td>{String(item['结果'] ?? '')}</td>
                  <td>{String(item['快照编号'] ?? '—')}</td>
                  <td>{String(item['数据集编号'] ?? '—')}</td>
                  <td className="reason-cell">{String(item['说明'] ?? '')}</td>
                </tr>
              ))}
              {journal.length === 0 && (
                <tr><td className="empty-state" colSpan={6}>暂无状态记录</td></tr>
              )}
            </tbody>
          </table>
        </section>
      )}

      <section className="panel">
        <div className="panel-head">
          <h3>大盘刷新读取结果</h3>
          <span className="hint">{String(dashboard?.['读取口径'] ?? '')}</span>
        </div>
        <table className="data-table compact">
          <thead>
            <tr>
              <th>配置</th><th>指标</th><th>对比</th><th>基准 → 当期</th><th>版本</th>
              <th>基准值</th><th>当期值</th><th>增长率</th><th>来源快照</th><th>不可比原因</th>
            </tr>
          </thead>
          <tbody>
            {cards.map((card) => (
              <tr key={String(card['配置编号'])}>
                <td>{String(card['配置编号'])}</td>
                <td>{String(card['目标指标'])}</td>
                <td>{String(card['对比类型'])}</td>
                <td>{String(card['基准周期'] ?? '—')} → {String(card['当期周期'] ?? '—')}</td>
                <td>
                  v{String(card['指标版本'] ?? '—')}
                  {card['指标版本'] && card['当期指标版本'] ? ` / v${String(card['当期指标版本'])}` : ''}
                </td>
                <td>{fmt(card['基准值'])}</td>
                <td>{fmt(card['当期值'])}</td>
                <td>
                  <span className={card['可比'] ? 'badge ok' : 'badge bad'}>{String(card['增长率文本'])}</span>
                </td>
                <td>{String(card['快照编号'] ?? '—')}</td>
                <td className="reason-cell">{String(card['不可比原因'] ?? '—')}</td>
              </tr>
            ))}
            {cards.length === 0 && (
              <tr><td className="empty-state" colSpan={10}>暂无对比配置</td></tr>
            )}
          </tbody>
        </table>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h3>指标快照（随对比基准同步）</h3>
          <span className="hint">同一对比配置只保留一份“当前”快照，旧版本自动转历史</span>
        </div>
        <table className="data-table compact">
          <thead>
            <tr><th>指标</th><th>配置</th><th>对比</th><th>基准 → 当期</th><th>版本</th><th>增长率</th><th>数据集</th><th>状态</th><th>同步时间</th></tr>
          </thead>
          <tbody>
            {metricSnapshots.map((item) => (
              <tr key={String(item.id)}>
                <td>{String(item['指标编号'])} {String(item['指标名称'] ?? '')}</td>
                <td>{String(item['配置编号'] ?? '—')}</td>
                <td>{String(item['对比类型'] ?? '—')}</td>
                <td>{String(item['基准周期'] ?? '—')} → {String(item['当期周期'] ?? '—')}</td>
                <td>v{String(item['指标版本'] ?? '—')}</td>
                <td>{String(item['增长率文本'] ?? '—')}</td>
                <td>{String(item['数据集编号'] ?? '—')}</td>
                <td>{String(item['状态'])}</td>
                <td>{String(item['同步时间'] ?? '')}</td>
              </tr>
            ))}
            {metricSnapshots.length === 0 && (
              <tr><td className="empty-state" colSpan={9}>还没有同步的指标快照</td></tr>
            )}
          </tbody>
        </table>
      </section>
    </section>
  )
}

function SnapshotDetail({ snapshot, onPublish }: { snapshot: Snapshot; onPublish: (snapshot: Snapshot) => Promise<void> }) {
  const [expanded, setExpanded] = useState(false)
  const reasons = snapshot['全部不可比原因'] ?? []
  return (
    <div className="snapshot-card">
      <div className="snapshot-head">
        <strong>{String(snapshot['快照编号'])}</strong>
        <span className={snapshot['不可比原因'] ? 'badge bad' : 'badge ok'}>
          {snapshot['不可比原因'] ? '不可比' : '可比'}
        </span>
        <span className="badge">指纹 {String(snapshot['快照指纹'])}</span>
        <span className="badge">来源 {String(snapshot['快照来源'] ?? '计算')}</span>
        {snapshot['状态'] === '已计算' && (
          <button className="btn primary" type="button" onClick={() => void onPublish(snapshot)}>发布基准快照</button>
        )}
      </div>
      <div className="detail-grid">
        <Detail label="对比类型" value={String(snapshot['对比类型'])} />
        <Detail label="基准周期" value={`${snapshot['基准周期']}（${snapshot['基准周期起']} ~ ${snapshot['基准周期止']}）`} />
        <Detail label="当期周期" value={`${snapshot['当期周期']}（${snapshot['当期周期起']} ~ ${snapshot['当期周期止']}）`} />
        <Detail label="指标版本" value={`v${snapshot['基准指标版本'] ?? '—'} / 当期 v${snapshot['当期指标版本'] ?? '—'}`} />
        <Detail label="基准值 / 当期值" value={`${fmt(snapshot['基准值'])} / ${fmt(snapshot['当期值'])}`} />
        <Detail label="差异 / 增长率" value={`${fmt(snapshot['差异'])} / ${snapshot['增长率文本'] ?? '—'}`} />
        <Detail label="差异阈值" value={`${snapshot['差异阈值'] ?? '—'}（${snapshot['是否超阈值'] ? '超阈值' : '未超阈值'}）`} />
        <Detail
          label="新鲜度"
          value={`${snapshot['新鲜度秒'] ?? '—'} 秒 / 上限 ${snapshot['新鲜度上限秒'] ?? '—'} 秒（数据时点 ${snapshot['最新数据时点'] ?? '—'}）`}
        />
        <Detail label="版本裁决" value={String(snapshot['基准版本裁决'] ?? '—')} wide />
        <Detail label="不可比原因" value={reasons.join('；') || '无'} wide danger={reasons.length > 0} />
        {(snapshot['口径提示'] ?? []).length > 0 && (
          <Detail label="口径提示" value={(snapshot['口径提示'] ?? []).join('；')} wide />
        )}
        <Detail
          label="基准窗口记录"
          value={`${(snapshot['基准窗口记录'] ?? []).length} 条 / 当期 ${(snapshot['当期窗口记录'] ?? []).length} 条`}
        />
      </div>
      <button className="link" type="button" onClick={() => setExpanded((prev) => !prev)}>
        {expanded ? '收起追溯' : '展开快照追溯'}
      </button>
      {expanded && (
        <pre className="trace-block">{JSON.stringify(snapshot['追溯'] ?? {}, null, 2)}</pre>
      )}
    </div>
  )
}

function Detail({ label, value, wide, danger }: { label: string; value: string; wide?: boolean; danger?: boolean }) {
  return (
    <div className={wide ? 'detail-item wide' : 'detail-item'}>
      <span>{label}</span>
      <strong className={danger ? 'error-text' : undefined}>{value}</strong>
    </div>
  )
}

function fmt(value: unknown): string {
  if (value === null || value === undefined || value === '') {
    return '—'
  }
  if (typeof value === 'number') {
    return value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
  }
  return String(value)
}
