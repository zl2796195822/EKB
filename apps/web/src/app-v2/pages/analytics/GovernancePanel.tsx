import {
  ArrowClockwise,
  CheckCircle,
  ClockCounterClockwise,
  CloudArrowUp,
  Database,
  WarningCircle,
} from '@phosphor-icons/react'
import { useCallback, useEffect, useState } from 'react'
import { StatePanel } from '../../components/StatePanel'
import type {
  AdapterError,
  AuditLogView,
  AuthSession,
  BackupView,
  PageState,
  ReviewItemView,
  SyncRunView,
  SyncSourceView,
  V2Services,
} from '../../types'
import { JobsCenterTab } from './JobsCenterTab'

type GovernanceTab = 'audit' | 'reviews' | 'sync' | 'backup' | 'jobs'

const GOVERNANCE_TABS: readonly { id: GovernanceTab; label: string }[] = [
  { id: 'audit', label: '审计日志' },
  { id: 'reviews', label: '低置信度审核' },
  { id: 'sync', label: '同步源' },
  { id: 'backup', label: '备份' },
  { id: 'jobs', label: '作业中心' },
]

const PAGE_SIZE = 20

function errorMeta(error?: AdapterError): string {
  if (!error) return ''
  return [
    error.code ? `code ${error.code}` : '',
    error.status ? `status ${error.status}` : '',
    error.requestId ? `request ${error.requestId}` : '',
  ].filter(Boolean).join(' · ')
}

function formatTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN')
}

interface GovernancePanelProps {
  readonly services: V2Services
  readonly session: AuthSession
}

/**
 * 运营与治理面板：接入既有 audit / reviews / sync / backup 端点。
 * 这是页面内容区的已批准运营入口，不新增全局侧栏或导航结构。
 */
export function GovernancePanel({ services, session }: GovernancePanelProps) {
  const [activeTab, setActiveTab] = useState<GovernanceTab>('audit')

  return (
    <section className="v2-m6-governance" aria-labelledby="m6-governance-title">
      <div className="v2-m5-panel-intro">
        <div>
          <h2 id="m6-governance-title">运营与治理</h2>
          <p>
            按当前主体授权调用既有 <code>/admin/audit</code>、<code>/admin/reviews</code>、
            <code>/admin/sync/sources</code> 和 <code>/admin/backup</code>，以及租户作用域的真实端点
            <code>/jobs</code> 和 <code>/jobs/cleanup</code>；无权限时显式拒绝，不伪造结果。
          </p>
        </div>
        <Database size={20} aria-hidden="true" />
      </div>

      <div className="v2-m5-tabs" role="tablist" aria-label="运营与治理视图">
        {GOVERNANCE_TABS.map((tab) => (
          <button
            type="button"
            key={tab.id}
            id={`gov-tab-${tab.id}`}
            role="tab"
            aria-selected={activeTab === tab.id}
            aria-controls={`gov-panel-${tab.id}`}
            className={activeTab === tab.id ? 'is-active' : ''}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === 'audit' ? <AuditTab services={services} /> : null}
      {activeTab === 'reviews' ? <ReviewsTab services={services} /> : null}
      {activeTab === 'sync' ? <SyncTab services={services} /> : null}
      {activeTab === 'backup' ? <BackupTab services={services} session={session} /> : null}
      {activeTab === 'jobs' ? <JobsCenterTab services={services} /> : null}
    </section>
  )
}

function AuditTab({ services }: { readonly services: V2Services }) {
  const [state, setState] = useState<PageState>('loading')
  const [items, setItems] = useState<readonly AuditLogView[]>([])
  const [traceId, setTraceId] = useState<string | null>(null)
  const [error, setError] = useState<AdapterError | undefined>()

  const load = useCallback(async () => {
    setState('loading')
    setError(undefined)
    const result = await services.admin.listAuditLogs({ pageSize: PAGE_SIZE })
    setState(result.state)
    setItems(result.data?.items ?? [])
    setTraceId(result.data?.traceId ?? null)
    setError(result.error)
  }, [services.admin])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <div id="gov-panel-audit" className="v2-m5-tab-panel" role="tabpanel" aria-labelledby="gov-tab-audit">
      <div className="v2-m6-tab-toolbar">
        <span>最近 {PAGE_SIZE} 条真实审计记录{traceId ? ` · trace ${traceId}` : ''}</span>
        <button type="button" className="v2-m5-secondary-button" onClick={() => void load()} disabled={state === 'loading'}>
          <ArrowClockwise size={14} aria-hidden="true" />
          重新加载
        </button>
      </div>

      {state === 'loading' ? <StatePanel state="loading" message="正在加载真实审计日志。" /> : null}
      {state === 'permission-denied' ? <StatePanel state="permission-denied" message="当前主体没有审计读取能力。" reason={errorMeta(error)} /> : null}
      {state === 'error' ? <StatePanel state="error" message="审计日志加载失败。" reason={errorMeta(error)} /> : null}
      {state === 'empty' ? <StatePanel state="empty" message="当前授权范围内没有审计记录。" /> : null}
      {state === 'ready' ? (
        <div className="v2-m5-table-wrap">
          <table className="v2-m5-table">
            <thead>
              <tr>
                <th scope="col">时间</th>
                <th scope="col">动作</th>
                <th scope="col">主体</th>
                <th scope="col">目标</th>
                <th scope="col">结果</th>
                <th scope="col">trace</th>
              </tr>
            </thead>
            <tbody>
              {items.map((log) => (
                <tr key={log.id}>
                  <td><time dateTime={log.createdAt}>{formatTime(log.createdAt)}</time></td>
                  <td><code>{log.action}</code></td>
                  <td>{log.actorId ?? '—'}</td>
                  <td>{log.targetType ? `${log.targetType}${log.targetId ? ` · ${log.targetId}` : ''}` : '—'}</td>
                  <td><span className="v2-m5-role-pill">{log.result}</span></td>
                  <td><code>{log.traceId ?? '—'}</code></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  )
}

function ReviewsTab({ services }: { readonly services: V2Services }) {
  const [state, setState] = useState<PageState>('loading')
  const [items, setItems] = useState<readonly ReviewItemView[]>([])
  const [error, setError] = useState<AdapterError | undefined>()
  const [pendingId, setPendingId] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(async () => {
    setState('loading')
    setError(undefined)
    const result = await services.admin.listReviewItems({ pageSize: PAGE_SIZE })
    setState(result.state)
    setItems(result.data?.items ?? [])
    setError(result.error)
  }, [services.admin])

  useEffect(() => {
    void load()
  }, [load])

  const advance = useCallback(async (item: ReviewItemView, status: 'REVIEWED' | 'RESOLVED') => {
    setPendingId(item.id)
    setNotice(null)
    const result = await services.admin.updateReviewItem(item.id, { status })
    setPendingId(null)
    if (result.state === 'ready' && result.data) {
      setItems((current) => current.map((entry) => entry.id === item.id ? result.data! : entry))
      setNotice(`审核项 ${item.id} 已按服务端响应更新为 ${result.data.status}。`)
    } else {
      setNotice(`审核项 ${item.id} 更新失败：${result.error?.message ?? '未知错误'}`)
    }
  }, [services.admin])

  return (
    <div id="gov-panel-reviews" className="v2-m5-tab-panel" role="tabpanel" aria-labelledby="gov-tab-reviews">
      <div className="v2-m6-tab-toolbar">
        <span>低置信度审核队列，状态流转以服务端响应为准</span>
        <button type="button" className="v2-m5-secondary-button" onClick={() => void load()} disabled={state === 'loading'}>
          <ArrowClockwise size={14} aria-hidden="true" />
          重新加载
        </button>
      </div>

      {notice ? <div className="v2-m5-notice v2-m5-notice--info" role="status"><CheckCircle size={15} aria-hidden="true" /><span>{notice}</span></div> : null}
      {state === 'loading' ? <StatePanel state="loading" message="正在加载真实审核队列。" /> : null}
      {state === 'permission-denied' ? <StatePanel state="permission-denied" message="当前主体没有审计读取能力，无法查看审核队列。" reason={errorMeta(error)} /> : null}
      {state === 'error' ? <StatePanel state="error" message="审核队列加载失败。" reason={errorMeta(error)} /> : null}
      {state === 'empty' ? <StatePanel state="empty" message="当前授权范围内没有待审核条目。" /> : null}
      {state === 'ready' ? (
        <div className="v2-m5-table-wrap">
          <table className="v2-m5-table">
            <thead>
              <tr>
                <th scope="col">问题</th>
                <th scope="col">置信度</th>
                <th scope="col">状态</th>
                <th scope="col">更新时间</th>
                <th scope="col">操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td><span><strong>{item.questionPreview}</strong><small>{item.answerPreview}</small></span></td>
                  <td><span className="v2-m5-role-pill">{item.confidence}</span></td>
                  <td><span className="v2-m5-role-pill">{item.status}</span></td>
                  <td><time dateTime={item.updatedAt}>{formatTime(item.updatedAt)}</time></td>
                  <td>
                    <div className="v2-m6-row-actions">
                      <button type="button" className="v2-m5-secondary-button" disabled={pendingId === item.id || item.status === 'REVIEWED'} onClick={() => void advance(item, 'REVIEWED')}>标记已审</button>
                      <button type="button" className="v2-m5-secondary-button" disabled={pendingId === item.id || item.status === 'RESOLVED'} onClick={() => void advance(item, 'RESOLVED')}>标记已解决</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  )
}

function SyncTab({ services }: { readonly services: V2Services }) {
  const [state, setState] = useState<PageState>('loading')
  const [sources, setSources] = useState<readonly SyncSourceView[]>([])
  const [error, setError] = useState<AdapterError | undefined>()
  const [runningId, setRunningId] = useState<string | null>(null)
  const [lastRun, setLastRun] = useState<SyncRunView | null>(null)
  const [runError, setRunError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setState('loading')
    setError(undefined)
    const result = await services.admin.listSyncSources()
    setState(result.state)
    setSources(result.data ?? [])
    setError(result.error)
  }, [services.admin])

  useEffect(() => {
    void load()
  }, [load])

  const run = useCallback(async (source: SyncSourceView) => {
    setRunningId(source.id)
    setLastRun(null)
    setRunError(null)
    const result = await services.admin.runSyncSource(source.id)
    setRunningId(null)
    if (result.state === 'ready' && result.data) {
      setLastRun(result.data)
      await load()
    } else {
      setRunError(`同步源 ${source.name} 运行失败：${result.error?.message ?? '未知错误'}`)
    }
  }, [load, services.admin])

  return (
    <div id="gov-panel-sync" className="v2-m5-tab-panel" role="tabpanel" aria-labelledby="gov-tab-sync">
      <div className="v2-m6-tab-toolbar">
        <span>只读增量同步源；同步是治理能力，不等于应用安装</span>
        <button type="button" className="v2-m5-secondary-button" onClick={() => void load()} disabled={state === 'loading'}>
          <ArrowClockwise size={14} aria-hidden="true" />
          重新加载
        </button>
      </div>

      {lastRun ? (
        <div className="v2-m5-notice v2-m5-notice--success" role="status">
          <CheckCircle size={15} aria-hidden="true" />
          <span>
            同步源 {lastRun.sourceId} 返回 status={lastRun.status} · synced_count={lastRun.syncedCount}
            {lastRun.errorMessage ? ` · ${lastRun.errorMessage}` : ''}（同步游标推进，非应用安装）
          </span>
        </div>
      ) : null}
      {runError ? <div className="v2-m5-notice v2-m5-notice--error" role="alert"><WarningCircle size={15} aria-hidden="true" /><span>{runError}</span></div> : null}

      {state === 'loading' ? <StatePanel state="loading" message="正在加载真实同步源列表。" /> : null}
      {state === 'permission-denied' ? <StatePanel state="permission-denied" message="当前主体没有知识库写入能力，无法管理同步源。" reason={errorMeta(error)} /> : null}
      {state === 'error' ? <StatePanel state="error" message="同步源列表加载失败。" reason={errorMeta(error)} /> : null}
      {state === 'empty' ? <StatePanel state="empty" message="当前租户还没有创建任何同步源。" /> : null}
      {state === 'ready' ? (
        <div className="v2-m5-table-wrap">
          <table className="v2-m5-table">
            <thead>
              <tr>
                <th scope="col">名称</th>
                <th scope="col">类型</th>
                <th scope="col">状态</th>
                <th scope="col">游标</th>
                <th scope="col">上次同步</th>
                <th scope="col">操作</th>
              </tr>
            </thead>
            <tbody>
              {sources.map((source) => (
                <tr key={source.id}>
                  <td><span><strong>{source.name}</strong><small>{source.kbId}</small></span></td>
                  <td>{source.sourceType}</td>
                  <td><span className="v2-m5-role-pill">{source.status}</span></td>
                  <td><code>{source.cursor ?? '—'}</code></td>
                  <td>{source.lastSyncAt ? formatTime(source.lastSyncAt) : '—'}</td>
                  <td>
                    <button type="button" className="v2-m5-secondary-button" disabled={runningId === source.id} onClick={() => void run(source)}>
                      {runningId === source.id ? '同步中…' : '运行同步'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  )
}

function BackupTab({ services, session }: { readonly services: V2Services; readonly session: AuthSession }) {
  const [confirming, setConfirming] = useState(false)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<BackupView | null>(null)
  const [error, setError] = useState<AdapterError | undefined>()
  const [denied, setDenied] = useState(false)

  const trigger = useCallback(async () => {
    setRunning(true)
    setError(undefined)
    setDenied(false)
    setResult(null)
    const response = await services.admin.triggerBackup()
    setRunning(false)
    setConfirming(false)
    if (response.state === 'ready' && response.data) {
      setResult(response.data)
      return
    }
    setError(response.error)
    setDenied(response.state === 'permission-denied')
  }, [services.admin])

  return (
    <div id="gov-panel-backup" className="v2-m5-tab-panel" role="tabpanel" aria-labelledby="gov-tab-backup">
      <div className="v2-m6-tab-toolbar">
        <span>平台管理员能力：触发一致性快照备份，结果写入审计日志</span>
        <span className="v2-m5-record-count">{session.capabilities.length} 项真实能力</span>
      </div>

      <div className="v2-m5-notice v2-m5-notice--info" role="status">
        <CloudArrowUp size={15} aria-hidden="true" />
        <span>备份是不可逆的运维动作，必须二次确认后才会调用 <code>POST /admin/backup</code>。</span>
      </div>

      {confirming ? (
        <div className="v2-m6-confirm-card" role="alertdialog" aria-labelledby="m6-backup-confirm">
          <strong id="m6-backup-confirm">确认触发数据库备份？</strong>
          <p>将对当前数据库执行一致性快照，耗时取决于数据规模，操作会被审计记录。</p>
          <div className="v2-m6-row-actions">
            <button type="button" className="v2-m5-primary-button" onClick={() => void trigger()} disabled={running}>
              {running ? '备份进行中…' : '确认备份'}
            </button>
            <button type="button" className="v2-m5-secondary-button" onClick={() => setConfirming(false)} disabled={running}>取消</button>
          </div>
        </div>
      ) : (
        <button type="button" className="v2-m5-primary-button" onClick={() => setConfirming(true)} disabled={running}>
          <ClockCounterClockwise size={15} aria-hidden="true" />
          触发备份
        </button>
      )}

      {running ? <StatePanel state="loading" message="正在执行真实备份请求，请勿离开页面。" /> : null}
      {denied ? <StatePanel state="permission-denied" message="当前主体不是平台管理员，无法触发备份。" reason={errorMeta(error)} /> : null}
      {error && !denied ? <StatePanel state="error" message="备份失败，服务端已记录失败审计。" reason={errorMeta(error)} /> : null}

      {result ? (
        <div className="v2-m5-result-card">
          <strong>备份完成（服务端真实响应）</strong>
          <dl className="v2-m6-quality-grid">
            <div className="v2-m6-quality-item"><dt>路径</dt><dd><code>{result.backupPath}</code></dd></div>
            <div className="v2-m6-quality-item"><dt>sha256</dt><dd><code>{result.backupSha256.slice(0, 16)}…</code></dd></div>
            <div className="v2-m6-quality-item"><dt>大小</dt><dd>{result.backupSizeBytes.toLocaleString('zh-CN')} bytes</dd></div>
            <div className="v2-m6-quality-item"><dt>耗时</dt><dd>{result.elapsedSeconds} 秒</dd></div>
            <div className="v2-m6-quality-item"><dt>表数量</dt><dd>{result.tableCount}</dd></div>
          </dl>
        </div>
      ) : null}
    </div>
  )
}
