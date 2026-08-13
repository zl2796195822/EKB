import {
  ArrowRight,
  CaretDown,
  ChartLineUp,
  ChartPieSlice,
  ChatCircleDots,
  ClockCounterClockwise,
  FilePlus,
  FolderOpen,
  FolderPlus,
  MagicWand,
  Pulse,
  Tag,
  UploadSimple,
  UsersThree,
} from '@phosphor-icons/react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  DashboardActivityFeed,
  DashboardDistributionBar,
  DashboardMetricCard,
  DashboardPanel,
  DashboardTrendChart,
  DashboardUnavailable,
  QuickAction,
  RecentAccessSection,
} from '../components/dashboard'
import { getLocalGreeting } from '../components/dashboard/greeting'
import { StatePanel } from '../components/StatePanel'
import { ModuleMapLink } from '../components/ui'
import type {
  AnalyticsActivityView,
  AnalyticsDistributionView,
  AnalyticsOverviewView,
  AnalyticsTrendView,
  PageState,
  V2PageProps,
} from '../types'

const TREND_DAYS = [
  { days: 7, label: '近 7 天' },
  { days: 14, label: '近 14 天' },
  { days: 30, label: '近 30 天' },
] as const

const DIST_DAYS = [
  { days: 7, label: '全部空间 · 7 天' },
  { days: 14, label: '全部空间 · 14 天' },
  { days: 30, label: '全部空间 · 30 天' },
] as const

const ACTIVITY_FILTERS = [
  { id: 'all', label: '全部' },
  { id: 'document', label: '文档' },
  { id: 'kb', label: '知识库' },
  { id: 'qa', label: 'AI 问答' },
] as const

function rangeCaption(days: number): string {
  const end = new Date()
  const start = new Date(end.getTime() - (days - 1) * 24 * 60 * 60 * 1000)
  const f = (d: Date) => d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
  return `${f(start)} — ${f(end)}`
}

interface DaySelectProps {
  readonly label: string
  readonly options: readonly { days: number; label: string }[]
  readonly value: number
  readonly onChange: (days: number) => void
  readonly loading?: boolean
  readonly caption?: string
}

function DaySelect({ label, options, value, onChange, loading, caption }: DaySelectProps) {
  const [open, setOpen] = useState(false)
  const current = options.find((o) => o.days === value) ?? options[0]
  return (
    <div className="v2-dashboard-select-wrap" style={{ position: 'relative' }}>
      <button
        type="button"
        className="v2-dashboard-select"
        onClick={() => setOpen((o) => !o)}
        disabled={loading}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={caption}
      >
        <span>{current?.label ?? label}</span>
        <CaretDown size={12} aria-hidden="true" />
      </button>
      {open ? (
        <ul
          role="listbox"
          style={{
            position: 'absolute',
            top: 'calc(100% + 4px)',
            right: 0,
            zIndex: 40,
            margin: 0,
            padding: 4,
            background: 'var(--color-surface)',
            border: '1px solid var(--color-border)',
            borderRadius: 8,
            boxShadow: 'var(--shadow-popover)',
            listStyle: 'none',
            minWidth: 140,
          }}
          onMouseLeave={() => setOpen(false)}
        >
          {options.map((opt) => (
            <li key={opt.days}>
              <button
                type="button"
                role="option"
                aria-selected={opt.days === value}
                onClick={() => {
                  onChange(opt.days)
                  setOpen(false)
                }}
                style={{
                  width: '100%',
                  textAlign: 'left',
                  padding: '6px 10px',
                  background: opt.days === value ? 'var(--color-selected)' : 'transparent',
                  color: opt.days === value ? 'var(--color-info-text)' : 'var(--color-text-primary)',
                  borderRadius: 6,
                  border: 'none',
                  fontSize: 12,
                  cursor: 'pointer',
                  fontWeight: opt.days === value ? 500 : 400,
                }}
              >
                {opt.label}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

interface FilterPillProps {
  readonly options: readonly { id: string; label: string }[]
  readonly value: string
  readonly onChange: (id: string) => void
}

function FilterPills({ options, value, onChange }: FilterPillProps) {
  return (
    <div style={{ display: 'flex', gap: 6 }}>
      {options.map((opt) => (
        <button
          type="button"
          key={opt.id}
          onClick={() => onChange(opt.id)}
          aria-pressed={opt.id === value}
          style={{
            padding: '3px 10px',
            borderRadius: 999,
            border: `1px solid ${opt.id === value ? 'var(--color-info)' : 'var(--color-border)'}`,
            background: opt.id === value ? 'var(--color-selected)' : 'var(--color-surface)',
            color: opt.id === value ? 'var(--color-info-text)' : 'var(--color-text-secondary)',
            fontSize: 11,
            fontWeight: 500,
            cursor: 'pointer',
            lineHeight: 1.6,
          }}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}

export function DashboardPage({ services, session }: V2PageProps) {
  const greeting = useMemo(() => getLocalGreeting(), [])
  const userName = session.user.name

  const [trendDays, setTrendDays] = useState(14)
  const [distDays, setDistDays] = useState(7)
  const [activityFilter, setActivityFilter] = useState('all')

  const [overviewState, setOverviewState] = useState<PageState>('loading')
  const [overview, setOverview] = useState<AnalyticsOverviewView | null>(null)
  const [overviewError, setOverviewError] = useState<string>('')

  const [trendState, setTrendState] = useState<PageState>('loading')
  const [trend, setTrend] = useState<AnalyticsTrendView | null>(null)
  const [trendError, setTrendError] = useState<string>('')

  const [distState, setDistState] = useState<PageState>('loading')
  const [distribution, setDistribution] = useState<AnalyticsDistributionView | null>(null)
  const [distError, setDistError] = useState<string>('')

  const [activityState, setActivityState] = useState<PageState>('loading')
  const [activity, setActivity] = useState<AnalyticsActivityView | null>(null)
  const [activityError, setActivityError] = useState<string>('')

  const loadAnalytics = useCallback(
    async (scope: { trendDays: number; distDays: number }) => {
      const [ov, tr, di, ac] = await Promise.all([
        services.analytics.getOverview(Math.max(scope.trendDays, scope.distDays)),
        services.analytics.getTrend(scope.trendDays),
        services.analytics.getDistribution(scope.distDays),
        services.analytics.getActivity(20),
      ])
      setOverviewState(ov.state)
      setOverview(ov.data ?? null)
      setOverviewError(ov.error?.message ?? '')
      setTrendState(tr.state)
      setTrend(tr.data ?? null)
      setTrendError(tr.error?.message ?? '')
      setDistState(di.state)
      setDistribution(di.data ?? null)
      setDistError(di.error?.message ?? '')
      setActivityState(ac.state)
      setActivity(ac.data ?? null)
      setActivityError(ac.error?.message ?? '')
    },
    [services.analytics],
  )

  useEffect(() => {
    void loadAnalytics({ trendDays, distDays })
  }, [trendDays, distDays, loadAnalytics])

  const anyLoading =
    overviewState === 'loading' ||
    trendState === 'loading' ||
    distState === 'loading' ||
    activityState === 'loading'

  const filteredActivity: AnalyticsActivityView | null = useMemo(() => {
    if (!activity) return null
    if (activityFilter === 'all') return activity
    const mapType: Record<string, string> = {
      document: 'DOCUMENT',
      kb: 'KB',
      qa: 'CONVERSATION',
    }
    const want = mapType[activityFilter]
    return {
      ...activity,
      items: activity.items.filter((i) => i.resourceType === want),
    }
  }, [activity, activityFilter])

  return (
    <div className="v2-dashboard" data-route-id="dashboard">
      <ModuleMapLink />

      <section className="v2-dashboard-welcome" aria-labelledby="v2-dashboard-welcome-title">
        <div>
          <h1 id="v2-dashboard-welcome-title">
            {greeting}，{userName}
          </h1>
          <p>欢迎回到团队 AI 知识库，工作台数据来自 <code>resource_access_events</code> 真实投影。</p>
        </div>
        <div className="v2-dashboard-actions" aria-label="快捷操作">
          <QuickAction label="新建文档" href="#/documents" icon={<FilePlus size={15} />} />
          <QuickAction label="上传文档" href="#/documents" icon={<UploadSimple size={15} />} />
          <QuickAction label="创建知识空间" href="#/knowledge" icon={<FolderPlus size={15} />} />
          <QuickAction
            label="智能导入"
            icon={<MagicWand size={15} />}
            disabled
            reason="v4 P1 对象存储与后台 ingest 任务落地后开放（规划中：Onyx/Notion/GDrive 连接器）。"
          />
        </div>
      </section>

      <section className="v2-dashboard-metrics" aria-label="工作台指标">
        <DashboardMetricCard
          label="知识库文档"
          tone="blue"
          icon={<FolderOpen size={17} />}
          value={overviewState === 'ready' && overview ? overview.docCount : undefined}
          hint={overviewState === 'ready' && overview ? `分布在 ${overview.kbCount.toLocaleString('zh-CN')} 个知识空间` : undefined}
          loading={overviewState === 'loading'}
        />
        <DashboardMetricCard
          label="知识标签"
          tone="violet"
          icon={<Tag size={17} />}
          value={overviewState === 'ready' && overview ? overview.kbCount : undefined}
          hint={overviewState === 'ready' ? '当前以知识空间数量近似（标签服务·v4 P2 内容治理）' : undefined}
          loading={overviewState === 'loading'}
        />
        <DashboardMetricCard
          label="团队成员"
          tone="green"
          icon={<UsersThree size={17} />}
          value={overviewState === 'ready' && overview ? overview.memberCount : undefined}
          hint={overviewState === 'ready' && overview ? `近 ${overview.days} 天活跃访客 ${overview.visitors.toLocaleString('zh-CN')} 人` : undefined}
          loading={overviewState === 'loading'}
        />
        <DashboardMetricCard
          label="访问量（今日）"
          tone="orange"
          icon={<Pulse size={17} />}
          value={overviewState === 'ready' && overview ? overview.accessesToday : undefined}
          hint={overviewState === 'ready' && overview ? `${overview.days} 天合计 ${overview.accesses.toLocaleString('zh-CN')} 次` : undefined}
          loading={overviewState === 'loading'}
        />
        <DashboardMetricCard
          label="AI 问答（今日）"
          tone="blue"
          icon={<ChatCircleDots size={17} />}
          value={overviewState === 'ready' && overview ? overview.qaToday : undefined}
          hint={overviewState === 'ready' && overview ? `${overview.days} 天合计 ${overview.qaVolume.toLocaleString('zh-CN')} 次` : undefined}
          loading={overviewState === 'loading'}
        />
      </section>

      <div className="v2-dashboard-grid v2-dashboard-grid--top">
        <DashboardPanel
          id="v2-dashboard-trend-title"
          title="知识库访问趋势"
          icon={<ChartLineUp size={16} />}
          action={
            <DaySelect
              label="近 7 天"
              options={TREND_DAYS}
              value={trendDays}
              onChange={setTrendDays}
              loading={trendState === 'loading'}
              caption={`覆盖：${rangeCaption(trendDays)}`}
            />
          }
          className="v2-dashboard-trend"
        >
          <div className="v2-dashboard-legend" aria-label="图表图例">
            <span>
              <i className="v2-dashboard-legend-dot v2-dashboard-legend-dot--blue" aria-hidden="true" />
              访问量（次）
            </span>
            <span>
              <i className="v2-dashboard-legend-dot v2-dashboard-legend-dot--green" aria-hidden="true" />
              独立访客（人）
            </span>
          </div>
          {trendState === 'loading' ? (
            <div className="v2-dashboard-chart-state">
              <StatePanel state="loading" message={`按近 ${trendDays} 天加载访问趋势。`} />
            </div>
          ) : null}
          {trendState === 'empty' ? (
            <div className="v2-dashboard-chart-state">
              <StatePanel state="empty" message="所选区间还没有任何知识资源访问记录。" />
            </div>
          ) : null}
          {trendState === 'error' || trendState === 'permission-denied' ? (
            <div className="v2-dashboard-chart-state">
              <StatePanel state={trendState} message="访问趋势加载失败。" reason={trendError} />
            </div>
          ) : null}
          {trendState === 'ready' && trend ? <DashboardTrendChart data={trend} /> : null}
        </DashboardPanel>

        <DashboardPanel
          id="v2-dashboard-distribution-title"
          title="知识库使用分布"
          icon={<ChartPieSlice size={16} />}
          action={
            <DaySelect
              label="全部空间"
              options={DIST_DAYS}
              value={distDays}
              onChange={setDistDays}
              loading={distState === 'loading'}
              caption={`覆盖：${rangeCaption(distDays)}`}
            />
          }
          className="v2-dashboard-distribution"
        >
          {distState === 'loading' ? (
            <div className="v2-dashboard-chart-state">
              <StatePanel state="loading" message={`按近 ${distDays} 天聚合各知识空间使用分布。`} />
            </div>
          ) : null}
          {distState === 'empty' ? (
            <div className="v2-dashboard-chart-state">
              <StatePanel state="empty" message="所选区间还没有可归属的访问事件。" />
            </div>
          ) : null}
          {distState === 'error' || distState === 'permission-denied' ? (
            <div className="v2-dashboard-chart-state">
              <StatePanel state={distState} message="知识库使用分布加载失败。" reason={distError} />
            </div>
          ) : null}
          {distState === 'ready' && distribution ? <DashboardDistributionBar data={distribution} /> : null}
        </DashboardPanel>

        <DashboardPanel
          id="v2-dashboard-activity-title"
          title="最近动态"
          icon={<ClockCounterClockwise size={16} />}
          action={<FilterPills options={ACTIVITY_FILTERS} value={activityFilter} onChange={setActivityFilter} />}
          className="v2-dashboard-activity"
        >
          {activityState === 'loading' ? (
            <div className="v2-dashboard-activity-state">
              <StatePanel state="loading" message="正在获取最近动态（访问 /analytics/activity）。" />
            </div>
          ) : null}
          {activityState === 'empty' ? (
            <div className="v2-dashboard-activity-state">
              <StatePanel state="empty" message="最近还没有任何知识资源动态。" />
            </div>
          ) : null}
          {activityState === 'error' || activityState === 'permission-denied' ? (
            <div className="v2-dashboard-activity-state">
              <StatePanel state={activityState} message="最近动态加载失败。" reason={activityError} />
            </div>
          ) : null}
          {activityState === 'ready' && filteredActivity ? (
            filteredActivity.items.length === 0 ? (
              <div className="v2-dashboard-activity-state">
                <StatePanel state="empty" message="当前筛选条件下没有动态记录。" />
              </div>
            ) : (
              <DashboardActivityFeed data={filteredActivity} />
            )
          ) : null}
        </DashboardPanel>
      </div>

      <div className="v2-dashboard-grid v2-dashboard-grid--bottom">
        <RecentAccessSection analytics={services.analytics} />
        <div className="v2-dashboard-side-stack">
          <DashboardPanel
            id="v2-dashboard-tags-title"
            title="热门标签"
            icon={<Tag size={16} />}
            action={
              <button
                type="button"
                className="v2-dashboard-text-link v2-dashboard-text-button"
                disabled
                title="文件夹/标签独立端点：v4 P2 内容治理里程碑开放"
              >
                全部标签
                <ArrowRight size={13} aria-hidden="true" />
              </button>
            }
            className="v2-dashboard-tags"
          >
            {overviewState === 'loading' ? (
              <StatePanel state="loading" message="正在读取工作台概览。" />
            ) : overviewState === 'ready' && overview ? (
              <div className="v2-dashboard-tags-proxy" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                <div
                  style={{
                    padding: '10px 12px',
                    borderRadius: 8,
                    border: '1px dashed var(--color-border)',
                    background: 'var(--color-bg-subtle)',
                    fontSize: 11,
                    color: 'var(--color-text-muted)',
                    lineHeight: 1.6,
                  }}
                >
                  <strong style={{ color: 'var(--color-text-primary)' }}>v4 P2 · 内容治理阶段</strong>
                  <br />
                  文件夹 / 标签 / 收藏 / 分享等独立端点落地后，此处切换为真实 Top Tags。
                  当前用 <code>knowledge_bases</code> 概览做近似占位：
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  <span className="v2-dashboard-tag-chip" style={{ padding: '3px 10px', borderRadius: 999, background: 'var(--color-info-surface)', color: 'var(--color-info-text)', fontSize: 11 }}>
                    知识空间 {overview.kbCount.toLocaleString('zh-CN')} 个
                  </span>
                  <span className="v2-dashboard-tag-chip" style={{ padding: '3px 10px', borderRadius: 999, background: 'var(--color-success-surface)', color: 'var(--color-success-text)', fontSize: 11 }}>
                    文档总数 {overview.docCount.toLocaleString('zh-CN')} 篇
                  </span>
                  <span className="v2-dashboard-tag-chip" style={{ padding: '3px 10px', borderRadius: 999, background: 'var(--color-warning-surface)', color: 'var(--color-warning-text)', fontSize: 11 }}>
                    团队成员 {overview.memberCount.toLocaleString('zh-CN')} 人
                  </span>
                  <span className="v2-dashboard-tag-chip" style={{ padding: '3px 10px', borderRadius: 999, background: 'var(--color-hover)', color: 'var(--color-text-secondary)', fontSize: 11 }}>
                    近 {overview.days} 天访问 {overview.accesses.toLocaleString('zh-CN')}
                  </span>
                </div>
              </div>
            ) : (
              <DashboardUnavailable message="当前后端未提供独立的标签统计端点（v4 P2 规划）。" />
            )}
          </DashboardPanel>
          <DashboardPanel
            id="v2-dashboard-health-title"
            title="知识库健康度"
            icon={<Pulse size={16} />}
            action={
              <button
                type="button"
                className="v2-dashboard-text-link v2-dashboard-text-button"
                disabled
                title="健康度分： ingest 成功率 · embedding 覆盖率 · 过期文档比 · REVIEW 积压 — v4 P2 治理里程碑开放"
              >
                查看详情
                <ArrowRight size={13} aria-hidden="true" />
              </button>
            }
            className="v2-dashboard-health"
          >
            {overviewState === 'loading' ? (
              <StatePanel state="loading" message="正在读取工作台概览。" />
            ) : overviewState === 'ready' && overview ? (
              <div className="v2-dashboard-health-proxy" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                <div
                  style={{
                    padding: '10px 12px',
                    borderRadius: 8,
                    border: '1px dashed var(--color-border)',
                    background: 'var(--color-bg-subtle)',
                    fontSize: 11,
                    color: 'var(--color-text-muted)',
                    lineHeight: 1.6,
                  }}
                >
                  <strong style={{ color: 'var(--color-text-primary)' }}>v4 P2 · 治理指标</strong>
                  <br />
                  专用健康度端点尚未提供，以下为 QA 规模与空间文档密度的近似卡，不作为正式治理依据。
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                  <div
                    style={{
                      padding: '8px 10px',
                      borderRadius: 6,
                      background: 'linear-gradient(135deg, var(--color-success-surface) 0%, var(--color-success-surface) 100%)',
                      color: 'var(--color-success-text)',
                      fontSize: 11,
                    }}
                  >
                    <div style={{ fontFamily: 'ui-monospace, JetBrains Mono, Menlo, monospace', fontSize: 18, fontWeight: 600 }}>
                      {overview.qaVolume === 0 || overview.accesses === 0
                        ? '—'
                        : `${Math.round((overview.qaVolume / overview.accesses) * 100)}%`}
                    </div>
                    <div style={{ opacity: 0.85 }}>问答占总访问比</div>
                  </div>
                  <div
                    style={{
                      padding: '8px 10px',
                      borderRadius: 6,
                      background: 'linear-gradient(135deg, var(--color-info-surface) 0%, var(--color-info-surface) 100%)',
                      color: 'var(--color-info-text)',
                      fontSize: 11,
                    }}
                  >
                    <div style={{ fontFamily: 'ui-monospace, JetBrains Mono, Menlo, monospace', fontSize: 18, fontWeight: 600 }}>
                      {overview.kbCount === 0 ? '—' : Math.round(overview.docCount / overview.kbCount)}
                    </div>
                    <div style={{ opacity: 0.85 }}>文档 / 空间（密度）</div>
                  </div>
                </div>
              </div>
            ) : (
              <DashboardUnavailable message="当前后端未提供知识库健康度专用端点（v4 P2 规划）。" />
            )}
          </DashboardPanel>
        </div>
      </div>

      <footer className="v2-dashboard-footer">
        <span>© {new Date().getFullYear()} 团队 AI 知识库</span>
        <span>使用条款</span>
        <span>隐私政策</span>
        <span>帮助中心</span>
        <span>联系我们</span>
        <span style={{ marginLeft: 'auto', fontFamily: 'ui-monospace, JetBrains Mono, Menlo, monospace', fontSize: 10, color: 'var(--color-text-muted)' }}>
          {anyLoading ? '同步中…' : `已同步 · ${new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`}
        </span>
      </footer>
    </div>
  )
}
