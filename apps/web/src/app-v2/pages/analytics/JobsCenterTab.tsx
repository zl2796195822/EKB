import {
  ArrowClockwise,
  CheckCircle,
  Pulse,
  WarningCircle,
} from '@phosphor-icons/react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { StatePanel } from '../../components/StatePanel'
import type { AdapterError, PageState, V2Services } from '../../types'
import type { JobView, JobsCleanupView, JobsRuntimeView } from '../../types/admin'

const JOB_STATES = [
  'QUEUED',
  'RUNNING',
  'RETRY_WAIT',
  'FAILED',
  'DEAD',
  'CANCELLED',
  'SUCCEEDED',
] as const

const CANCELLABLE_STATES = new Set(['QUEUED', 'RUNNING', 'RETRY_WAIT'])
const JOB_LIST_LIMIT = 100
const CLEANUP_RECENT_LIMIT = 10

function errorMeta(error?: AdapterError): string {
  if (!error) return ''
  return [
    error.code ? `code ${error.code}` : '',
    error.status ? `status ${error.status}` : '',
    error.requestId ? `request ${error.requestId}` : '',
  ].filter(Boolean).join(' · ')
}

function formatTime(value: string | null): string {
  if (!value) return '不可用'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN')
}

function safeLeaseOwner(value: string | null): string {
  if (!value) return '不可用'
  const owner = value.trim()
  // Worker labels are useful metadata; address-like lease owners stay hidden.
  if (
    /^https?:\/\//i.test(owner)
    || /^(?:10|127)\.\d{1,3}(?:\.\d{1,3}){2}$/.test(owner)
    || /^(?:169\.254|192\.168)\.\d{1,3}\.\d{1,3}$/.test(owner)
    || /^172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}$/.test(owner)
    || owner.includes(':')
  ) return '不可用（地址已隐藏）'
  return owner
}

function stateLabel(state: string): string {
  const labels: Record<string, string> = {
    QUEUED: '排队中',
    RUNNING: '运行中',
    RETRY_WAIT: '等待重试',
    FAILED: '失败',
    DEAD: 'Dead',
    CANCELLED: '已取消',
    SUCCEEDED: '成功',
  }
  return labels[state] ?? state
}

function retryableLabel(value: boolean | null): string {
  if (value === true) return '可重试'
  if (value === false) return '不可重试'
  return '未声明'
}

function canCancel(job: JobView): boolean {
  return CANCELLABLE_STATES.has(job.state)
}

function stateCounts(jobs: readonly JobView[]): Readonly<Record<string, number>> {
  return JOB_STATES.reduce<Record<string, number>>((counts, state) => {
    counts[state] = jobs.reduce((total, job) => total + (job.state === state ? 1 : 0), 0)
    return counts
  }, {})
}

function cleanupStateEntries(cleanup: JobsCleanupView): readonly [string, number][] {
  const knownOrder = new Map<string, number>(JOB_STATES.map((state, index) => [state, index]))
  return Object.entries(cleanup.byState).sort(([left], [right]) => {
    return (knownOrder.get(left) ?? JOB_STATES.length) - (knownOrder.get(right) ?? JOB_STATES.length)
  })
}

interface JobsCenterTabProps {
  readonly services: V2Services
}

export function JobsCenterTab({ services }: JobsCenterTabProps) {
  const [jobsState, setJobsState] = useState<PageState>('loading')
  const [jobs, setJobs] = useState<readonly JobView[]>([])
  const [jobsCount, setJobsCount] = useState(0)
  const [jobsError, setJobsError] = useState<AdapterError | undefined>()
  const [cleanupState, setCleanupState] = useState<PageState>('loading')
  const [cleanup, setCleanup] = useState<JobsCleanupView | null>(null)
  const [cleanupError, setCleanupError] = useState<AdapterError | undefined>()
  const [runtimeState, setRuntimeState] = useState<PageState>('loading')
  const [runtime, setRuntime] = useState<JobsRuntimeView | null>(null)
  const [runtimeError, setRuntimeError] = useState<AdapterError | undefined>()
  const [pendingJobId, setPendingJobId] = useState<string | null>(null)
  const [cancelNotice, setCancelNotice] = useState<string | null>(null)
  const [cancelError, setCancelError] = useState<AdapterError | undefined>()

  const load = useCallback(async () => {
    setJobsState('loading')
    setCleanupState('loading')
    setRuntimeState('loading')
    setJobs([])
    setJobsCount(0)
    setCleanup(null)
    setRuntime(null)
    setJobsError(undefined)
    setCleanupError(undefined)
    setRuntimeError(undefined)

    const [jobsResult, cleanupResult, runtimeResult] = await Promise.all([
      services.admin.listJobs({ states: JOB_STATES, limit: JOB_LIST_LIMIT }),
      services.admin.getJobsCleanup(CLEANUP_RECENT_LIMIT),
      services.admin.getJobsRuntime(),
    ])

    setJobsState(jobsResult.state)
    setJobs(jobsResult.data?.items ?? [])
    setJobsCount(jobsResult.data?.count ?? 0)
    setJobsError(jobsResult.error)

    setCleanupState(cleanupResult.state)
    setCleanup(cleanupResult.data ?? null)
    setCleanupError(cleanupResult.error)

    setRuntimeState(runtimeResult.state)
    setRuntime(runtimeResult.data ?? null)
    setRuntimeError(runtimeResult.error)
  }, [services.admin])

  useEffect(() => {
    void load()
  }, [load])

  const counts = useMemo(() => stateCounts(jobs), [jobs])
  const cleanupEntries = useMemo(
    () => cleanup ? cleanupStateEntries(cleanup) : [],
    [cleanup],
  )

  const cancel = useCallback(async (job: JobView) => {
    if (!canCancel(job)) return
    setPendingJobId(job.id)
    setCancelNotice(null)
    setCancelError(undefined)
    const result = await services.admin.cancelJob(job.id)
    if (result.state === 'ready' && result.data) {
      setCancelNotice(`作业 ${job.id} 已返回 ${result.data.state}，正在重新读取队列。`)
      await load()
    } else {
      setCancelError(result.error)
    }
    setPendingJobId(null)
  }, [load, services.admin])

  return (
    <div id="gov-panel-jobs" className="v2-m5-tab-panel v2-jobs-center" role="tabpanel" aria-labelledby="gov-tab-jobs">
      <div className="v2-m6-tab-toolbar">
        <span>租户作用域 Jobs Center：队列、租约、错误与清理投影均来自真实 API</span>
        <button type="button" className="v2-m5-secondary-button" onClick={() => void load()} disabled={jobsState === 'loading' || cleanupState === 'loading' || runtimeState === 'loading'}>
          <ArrowClockwise size={14} aria-hidden="true" />
          刷新
        </button>
      </div>

      {cancelNotice ? (
        <div className="v2-m5-notice v2-m5-notice--success" role="status">
          <CheckCircle size={15} aria-hidden="true" />
          <span>{cancelNotice}</span>
        </div>
      ) : null}
      {cancelError ? (
        <div className="v2-m5-notice v2-m5-notice--error" role="alert">
          <WarningCircle size={15} aria-hidden="true" />
          <span>取消作业失败。{errorMeta(cancelError)}</span>
        </div>
      ) : null}

      <section className="v2-jobs-runtime" aria-labelledby="jobs-runtime-title">
        <div className="v2-jobs-section-heading">
          <div>
            <h3 id="jobs-runtime-title">Worker / Scheduler 运行时</h3>
            <p>仅展示服务端真实心跳、租约和调度运行记录；空投影保持 unavailable。</p>
          </div>
          <Pulse size={20} aria-hidden="true" />
        </div>
        {runtimeState === 'loading' ? <StatePanel state="loading" message="正在读取真实 worker 与 scheduler 状态。" /> : null}
        {runtimeState === 'permission-denied' ? <StatePanel state="permission-denied" message="当前主体没有运行时状态读取能力。" reason={errorMeta(runtimeError)} /> : null}
        {runtimeState === 'error' ? <StatePanel state="error" message="运行时状态加载失败。" reason={errorMeta(runtimeError)} /> : null}
        {runtimeState === 'unavailable' || runtime?.status === 'unavailable' ? (
          <div className="v2-m5-notice" data-status="unavailable" role="status">
            <WarningCircle size={15} aria-hidden="true" />
            <span>运行时状态：unavailable。服务端当前没有 worker heartbeat、scheduler lease 或 scheduler run 记录。</span>
          </div>
        ) : null}
        {runtimeState === 'ready' && runtime?.status === 'available' ? (
          <>
            <div className="v2-jobs-state-grid" aria-label="运行时状态计数">
              <div className="v2-jobs-state-card" data-state="RUNNING"><span>Workers</span><strong>{runtime.workers.length}</strong><small>真实心跳</small></div>
              <div className="v2-jobs-state-card" data-state="QUEUED"><span>Leases</span><strong>{runtime.leases.length}</strong><small>Retention lease</small></div>
              <div className="v2-jobs-state-card" data-state="SUCCEEDED"><span>Scheduler runs</span><strong>{runtime.recentRuns.length}</strong><small>最近记录</small></div>
              <div className="v2-jobs-state-card" data-state="RETRY_WAIT"><span>Checked at</span><strong>{formatTime(runtime.checkedAt)}</strong><small>服务端时间</small></div>
            </div>
            <div className="v2-jobs-table-wrap">
              <table className="v2-jobs-table">
                <caption>运行时协调明细</caption>
                <thead>
                  <tr><th scope="col">类型</th><th scope="col">标识</th><th scope="col">状态 / 队列</th><th scope="col">时间</th></tr>
                </thead>
                <tbody>
                  {runtime.workers.map((worker) => (
                    <tr key={`worker-${worker.workerId}`}>
                      <td>Worker</td><td><strong>{safeLeaseOwner(worker.workerId)}</strong><small>{worker.workerType} · {worker.version}</small></td>
                      <td>{worker.queues.length > 0 ? worker.queues.join(', ') : '未声明队列'}</td><td><small>心跳：{formatTime(worker.heartbeatAt)}</small><small>启动：{formatTime(worker.startedAt)}</small></td>
                    </tr>
                  ))}
                  {runtime.leases.map((lease) => (
                    <tr key={`lease-${lease.scheduleName}`}>
                      <td>Lease</td><td><strong>{lease.scheduleName}</strong><small>owner：{safeLeaseOwner(lease.ownerId)}</small></td>
                      <td>fencing token：{lease.fencingToken}</td><td>到期：{formatTime(lease.leaseExpiresAt)}</td>
                    </tr>
                  ))}
                  {runtime.recentRuns.map((run) => (
                    <tr key={`run-${run.id}`}>
                      <td>Scheduler run</td><td><strong>{run.scheduleName}</strong><small>{run.scopeType} · {run.id}</small></td>
                      <td><span className="v2-jobs-state-pill" data-state={run.status}>{run.status}</span></td><td><small>开始：{formatTime(run.startedAt)}</small><small>结束：{formatTime(run.endedAt)}</small></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : null}
      </section>

      {jobsState !== 'loading' && jobsState !== 'error' && jobsState !== 'permission-denied' ? (
        <section className="v2-jobs-summary" aria-labelledby="jobs-summary-title">
          <div className="v2-jobs-section-heading">
            <div>
              <h3 id="jobs-summary-title">队列状态</h3>
              <p>当前请求返回 {jobsCount} 条，状态计数只基于服务端返回的真实作业。</p>
            </div>
            <Pulse size={20} aria-hidden="true" />
          </div>
          <div className="v2-jobs-state-grid">
            {JOB_STATES.map((state) => (
              <div className="v2-jobs-state-card" key={state} data-state={state}>
                <span>{stateLabel(state)}</span>
                <strong>{counts[state] ?? 0}</strong>
                <small>{state}</small>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      {jobsState === 'loading' ? <StatePanel state="loading" message="正在并行加载真实作业队列与清理投影。" /> : null}
      {jobsState === 'permission-denied' ? <StatePanel state="permission-denied" message="当前主体没有作业读取能力。" reason={errorMeta(jobsError)} /> : null}
      {jobsState === 'error' ? <StatePanel state="error" message="作业队列加载失败。" reason={errorMeta(jobsError)} /> : null}
      {jobsState === 'empty' ? <StatePanel state="empty" message="当前租户授权范围内没有作业记录。" /> : null}

      {jobsState === 'ready' ? (
        <div className="v2-jobs-table-wrap">
          <table className="v2-jobs-table">
            <thead>
              <tr>
                <th scope="col">作业类型</th>
                <th scope="col">状态</th>
                <th scope="col">创建 / 更新</th>
                <th scope="col">租约 / 心跳</th>
                <th scope="col">错误（安全元数据）</th>
                <th scope="col">Job ID</th>
                <th scope="col">操作</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <tr key={job.id}>
                  <td><strong>{job.jobType}</strong><small>可用时间：{formatTime(job.availableAt)}</small></td>
                  <td><span className="v2-jobs-state-pill" data-state={job.state}>{stateLabel(job.state)}</span></td>
                  <td><time dateTime={job.createdAt}>{formatTime(job.createdAt)}</time><small>{formatTime(job.updatedAt)}</small></td>
                  <td><strong>{safeLeaseOwner(job.leaseOwner)}</strong><small>心跳：{formatTime(job.heartbeatAt)} · 到期：{formatTime(job.leaseExpiresAt)}</small></td>
                  <td>
                    {job.sanitizedError ? (
                      <span className="v2-jobs-error-meta">
                        <strong>{job.errorCode ?? '未知错误码'}</strong>
                        <small>{job.sanitizedError.category ? `${job.sanitizedError.category} · ` : ''}{job.sanitizedError.message ?? '无安全消息'} · {retryableLabel(job.sanitizedError.retryable)}</small>
                      </span>
                    ) : '无'}
                  </td>
                  <td><code className="v2-jobs-id">{job.id}</code></td>
                  <td>
                    {canCancel(job) ? (
                      <button type="button" className="v2-m5-secondary-button" disabled={pendingJobId === job.id} onClick={() => void cancel(job)}>
                        {pendingJobId === job.id ? '取消中…' : '取消'}
                      </button>
                    ) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      <section className="v2-jobs-cleanup" aria-labelledby="jobs-cleanup-title">
        <div className="v2-jobs-section-heading">
          <div>
            <h3 id="jobs-cleanup-title">清理投影</h3>
            <p>job type：{cleanup?.jobType ?? '等待服务端响应'}；总量与分状态计数由服务端聚合。</p>
          </div>
        </div>
        {cleanupState === 'loading' ? <StatePanel state="loading" message="正在加载清理投影。" /> : null}
        {cleanupState === 'permission-denied' ? <StatePanel state="permission-denied" message="当前主体没有清理投影读取能力。" reason={errorMeta(cleanupError)} /> : null}
        {cleanupState === 'error' ? <StatePanel state="error" message="清理投影加载失败。" reason={errorMeta(cleanupError)} /> : null}
        {cleanupState === 'empty' ? <StatePanel state="empty" message="当前租户没有清理作业；仍显示服务端返回的零计数。" /> : null}
        {cleanup ? (
          <>
            <div className="v2-jobs-cleanup-total"><span>清理作业总量</span><strong>{cleanup.total}</strong></div>
            <div className="v2-jobs-cleanup-states">
              {cleanupEntries.length > 0 ? cleanupEntries.map(([state, count]) => (
                <span key={state}><b>{stateLabel(state)}</b>{count}</span>
              )) : <span>服务端未返回分状态记录</span>}
            </div>
            {cleanup.recent.length > 0 ? (
              <ul className="v2-jobs-cleanup-recent" aria-label="最近清理作业">
                {cleanup.recent.map((job) => <li key={job.id}><span>{stateLabel(job.state)}</span><code>{job.id}</code><time dateTime={job.createdAt}>{formatTime(job.createdAt)}</time></li>)}
              </ul>
            ) : null}
          </>
        ) : null}
      </section>
    </div>
  )
}
