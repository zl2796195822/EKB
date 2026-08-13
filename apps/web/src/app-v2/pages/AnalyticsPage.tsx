import {
  CalendarBlank,
  ChartLine,
  ChartPieSlice,
  ChatCircleDots,
  Database,
  Eye,
  FileText,
  Pulse,
  ShieldCheck,
  Users,
} from '@phosphor-icons/react'
import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { StatePanel } from '../components/StatePanel'
import type {
  AdapterError,
  AnalyticsActivityView,
  AnalyticsDistributionView,
  AnalyticsOverviewView,
  AnalyticsTrendView,
  OpsDashboardView,
  PageState,
  V2PageProps,
} from '../types'
import { AccessTrendChart } from './analytics/AccessTrendChart'
import { GovernancePanel } from './analytics/GovernancePanel'
import { KbDistributionList } from './analytics/KbDistributionList'
import { RecentActivityList } from './analytics/RecentActivityList'

/** 与服务端 Query(ge=1, le=90) 一致的日期范围档位。 */
const DAY_RANGES = [
  { days: 7, label: '近 7 天' },
  { days: 30, label: '近 30 天' },
  { days: 90, label: '近 90 天' },
] as const

const ACTIVITY_LIMIT = 12

function errorMeta(error?: AdapterError): string {
  if (!error) return ''
  return [
    error.code ? `code ${error.code}` : '',
    error.requestId ? `request ${error.requestId}` : '',
  ].filter(Boolean).join(' · ')
}

function rangeCaption(days: number): string {
  const end = new Date()
  const start = new Date(end.getTime() - (days - 1) * 24 * 60 * 60 * 1000)
  const format = (value: Date) => value.toLocaleDateString('zh-CN')
  return `${format(start)} — ${format(end)}`
}

export function AnalyticsPage({ services, session }: V2PageProps) {
  const [days, setDays] = useState(7)

  const [overviewState, setOverviewState] = useState<PageState>('loading')
  const [overview, setOverview] = useState<AnalyticsOverviewView | null>(null)
  const [overviewError, setOverviewError] = useState<AdapterError | undefined>()

  const [trendState, setTrendState] = useState<PageState>('loading')
  const [trend, setTrend] = useState<AnalyticsTrendView | null>(null)
  const [trendError, setTrendError] = useState<AdapterError | undefined>()

  const [distState, setDistState] = useState<PageState>('loading')
  const [distribution, setDistribution] = useState<AnalyticsDistributionView | null>(null)
  const [distError, setDistError] = useState<AdapterError | undefined>()

  const [activityState, setActivityState] = useState<PageState>('loading')
  const [activity, setActivity] = useState<AnalyticsActivityView | null>(null)
  const [activityError, setActivityError] = useState<AdapterError | undefined>()

  const [dashState, setDashState] = useState<PageState>('loading')
  const [dashboard, setDashboard] = useState<OpsDashboardView | null>(null)
  const [dashError, setDashError] = useState<AdapterError | undefined>()

  // 四张卡各自独立请求：分布查询慢不该把趋势图一起卡住，任一失败也只降级自己那块。
  const load = useCallback(async (nextDays: number) => {
    setOverviewState('loading')
    setTrendState('loading')
    setDistState('loading')
    setActivityState('loading')
    setDashState('loading')

    const [overviewResult, trendResult, distResult, activityResult, dashResult] = await Promise.all([
      services.analytics.getOverview(nextDays),
      services.analytics.getTrend(nextDays),
      services.analytics.getDistribution(nextDays),
      services.analytics.getActivity(ACTIVITY_LIMIT),
      services.admin.getOpsDashboard(nextDays),
    ])

    setOverviewState(overviewResult.state)
    setOverview(overviewResult.data ?? null)
    setOverviewError(overviewResult.error)

    setTrendState(trendResult.state)
    setTrend(trendResult.data ?? null)
    setTrendError(trendResult.error)

    setDistState(distResult.state)
    setDistribution(distResult.data ?? null)
    setDistError(distResult.error)

    setActivityState(activityResult.state)
    setActivity(activityResult.data ?? null)
    setActivityError(activityResult.error)

    setDashState(dashResult.state)
    setDashboard(dashResult.data ?? null)
    setDashError(dashResult.error)
  }, [services.admin, services.analytics])

  useEffect(() => {
    void load(days)
  }, [days, load])

  const overviewReady = overviewState === 'ready' && overview !== null
  const dashReady = dashState === 'ready' && dashboard !== null
  const anyLoading = overviewState === 'loading' || trendState === 'loading'

  return (
    <div className="v2-m5-page v2-m6-analytics-page">
      <header className="v2-m5-page-heading">
        <div>
          <p className="v2-eyebrow">KNOWLEDGE BASE / WORKSPACE</p>
          <h1>数据看板</h1>
          <p>查看知识在团队中的流动情况与问答质量表现。</p>
        </div>
        <div className="v2-m6-range-control" role="group" aria-label="统计日期范围">
          <CalendarBlank size={15} aria-hidden="true" />
          {DAY_RANGES.map((range) => (
            <button
              type="button"
              key={range.days}
              className={days === range.days ? 'is-active' : ''}
              aria-pressed={days === range.days}
              disabled={anyLoading}
              onClick={() => setDays(range.days)}
            >
              {range.label}
            </button>
          ))}
          <span className="v2-m6-range-caption">{rangeCaption(days)}</span>
        </div>
      </header>

      <div className="v2-m5-notice v2-m5-notice--info" role="status">
        <ShieldCheck size={15} aria-hidden="true" />
        <span>
          访问指标来自 <code>resource_access_events</code> 投影（<code>/analytics/*</code>），
          问答质量来自 <code>/admin/ops/dashboard</code>；两条链路独立失败、独立重试，不使用设计稿静态数字。
        </span>
      </div>

      {overviewState === 'permission-denied' ? (
        <StatePanel
          state="permission-denied"
          message="当前主体没有知识库读取能力，无法查看访问统计。"
          reason={errorMeta(overviewError)}
        />
      ) : null}
      {overviewState === 'error' ? (
        <StatePanel
          state="error"
          message="访问概览加载失败，未使用设计稿静态数字替代。"
          reason={errorMeta(overviewError)}
        />
      ) : null}

      <section className="v2-m6-metric-grid" aria-label="核心指标">
        <MetricCard
          icon={<Eye size={18} aria-hidden="true" />}
          label="访问总量"
          value={overviewReady ? overview.accesses : null}
          available={overviewReady}
          unavailableReason="等待 /analytics/overview 返回。"
          hint={overviewReady ? `今日 ${overview.accessesToday.toLocaleString('zh-CN')} 次` : ''}
        />
        <MetricCard
          icon={<Users size={18} aria-hidden="true" />}
          label="独立访客"
          value={overviewReady ? overview.visitors : null}
          available={overviewReady}
          unavailableReason="等待 /analytics/overview 返回。"
          hint={overviewReady ? `租户成员 ${overview.memberCount.toLocaleString('zh-CN')} 人` : ''}
        />
        <MetricCard
          icon={<ChatCircleDots size={18} aria-hidden="true" />}
          label="AI 问答次数"
          value={overviewReady ? overview.qaVolume : null}
          available={overviewReady}
          unavailableReason="等待 /analytics/overview 返回。"
          hint={overviewReady ? `今日 ${overview.qaToday.toLocaleString('zh-CN')} 次` : ''}
        />
        <MetricCard
          icon={<FileText size={18} aria-hidden="true" />}
          label="文档总数"
          value={overviewReady ? overview.docCount : null}
          available={overviewReady}
          unavailableReason="等待 /analytics/overview 返回。"
          hint={overviewReady ? `分布在 ${overview.kbCount.toLocaleString('zh-CN')} 个知识空间` : ''}
        />
      </section>

      <div className="v2-m6-chart-grid">
        <section className="v2-m6-chart-card" aria-labelledby="m6-trend-title">
          <div className="v2-m5-panel-intro">
            <div>
              <h2 id="m6-trend-title">知识库访问趋势</h2>
              <p>
                按日期展示访问量与独立访客
                {trendState === 'ready' && trend ? ` · 区间合计 ${trend.total.toLocaleString('zh-CN')} 次` : ''}
              </p>
            </div>
            <ChartLine size={20} aria-hidden="true" />
          </div>
          <ul className="v2-m6-legend">
            <li><span className="v2-m6-legend-dot v2-m6-legend-dot--primary" aria-hidden="true" />访问量</li>
            <li><span className="v2-m6-legend-dot v2-m6-legend-dot--secondary" aria-hidden="true" />独立访客</li>
          </ul>
          {trendState === 'loading' ? (
            <StatePanel state="loading" message={`正在按 days=${days} 加载访问趋势。`} />
          ) : null}
          {trendState === 'empty' ? (
            <StatePanel state="empty" message="所选区间内没有任何知识资源访问记录。" />
          ) : null}
          {trendState === 'error' || trendState === 'permission-denied' ? (
            <StatePanel
              state={trendState}
              message="访问趋势加载失败。"
              reason={errorMeta(trendError)}
            />
          ) : null}
          {trendState === 'ready' && trend ? <AccessTrendChart data={trend} /> : null}
        </section>

        <section className="v2-m6-chart-card" aria-labelledby="m6-distribution-title">
          <div className="v2-m5-panel-intro">
            <div>
              <h2 id="m6-distribution-title">内容使用分布</h2>
              <p>
                知识空间与文档事件的归属统计
                {distState === 'ready' && distribution
                  ? ` · 可归属 ${distribution.totalAccesses.toLocaleString('zh-CN')} 次`
                  : ''}
              </p>
            </div>
            <ChartPieSlice size={20} aria-hidden="true" />
          </div>
          <div className="v2-m6-distribution-total">
            <span>总文档数</span>
            <strong>
              {overviewReady
                ? overview.docCount.toLocaleString('zh-CN')
                : <em className="v2-m5-unavailable">unavailable</em>}
            </strong>
            <span>知识空间数</span>
            <strong>
              {overviewReady
                ? overview.kbCount.toLocaleString('zh-CN')
                : <em className="v2-m5-unavailable">unavailable</em>}
            </strong>
          </div>
          {distState === 'loading' ? (
            <StatePanel state="loading" message="正在加载知识空间访问分布。" />
          ) : null}
          {distState === 'empty' ? (
            <StatePanel state="empty" message="当前租户还没有知识空间，无法计算分布。" />
          ) : null}
          {distState === 'error' || distState === 'permission-denied' ? (
            <StatePanel
              state={distState}
              message="内容使用分布加载失败。"
              reason={errorMeta(distError)}
            />
          ) : null}
          {distState === 'ready' && distribution ? (
            <>
              <KbDistributionList data={distribution} />
              <p className="v2-a3-dist-footnote">
                会话表没有知识空间外键，问答会话事件无法归属到具体空间，因此不计入本卡片；
                总访问量请以上方「访问总量」为准。
              </p>
            </>
          ) : null}
        </section>
      </div>

      <section className="v2-a3-activity-card" aria-labelledby="m6-activity-title">
        <div className="v2-m5-panel-intro">
          <div>
            <h2 id="m6-activity-title">最近活动</h2>
            <p>来自访问事件投影的最新 {ACTIVITY_LIMIT} 条知识资源操作。</p>
          </div>
          <Pulse size={20} aria-hidden="true" />
        </div>
        {activityState === 'loading' ? (
          <StatePanel state="loading" message="正在加载最近活动。" />
        ) : null}
        {activityState === 'empty' ? (
          <StatePanel state="empty" message="还没有任何知识资源访问记录。" />
        ) : null}
        {activityState === 'error' || activityState === 'permission-denied' ? (
          <StatePanel
            state={activityState}
            message="最近活动加载失败。"
            reason={errorMeta(activityError)}
          />
        ) : null}
        {activityState === 'ready' && activity ? <RecentActivityList data={activity} /> : null}
      </section>

      <section className="v2-m6-quality-card" aria-labelledby="m6-quality-title">
        <div className="v2-m5-panel-intro">
          <div>
            <h2 id="m6-quality-title">问答质量</h2>
            <p>ops dashboard 已返回的真实质量字段；未返回的字段保持 unavailable。</p>
          </div>
          <Database size={20} aria-hidden="true" />
        </div>
        {dashState === 'error' || dashState === 'permission-denied' ? (
          <StatePanel
            state={dashState}
            message="运营看板加载失败，未使用设计稿静态数字替代。"
            reason={errorMeta(dashError)}
          />
        ) : null}
        <dl className="v2-m6-quality-grid">
          <QualityItem label="已回答" value={dashReady ? dashboard.answered : null} />
          <QualityItem label="拒答" value={dashReady ? dashboard.refused : null} />
          <QualityItem label="超时" value={dashReady ? dashboard.timeout : null} />
          <QualityItem label="错误" value={dashReady ? dashboard.error : null} />
          <QualityItem label="准确率" value={dashReady ? dashboard.accuracy : null} format="ratio" />
          <QualityItem label="拒答率" value={dashReady ? dashboard.refusalRate : null} format="ratio" />
          <QualityItem label="满意度" value={dashReady ? dashboard.satisfaction : null} format="ratio" />
          <QualityItem label="正向反馈" value={dashReady ? dashboard.feedbackUp : null} />
          <QualityItem label="负向反馈" value={dashReady ? dashboard.feedbackDown : null} />
          <QualityItem label="待审核" value={dashReady ? dashboard.pendingReviews : null} />
        </dl>
      </section>

      <GovernancePanel services={services} session={session} />
    </div>
  )
}

interface MetricCardProps {
  readonly icon: ReactNode
  readonly label: string
  readonly value: number | null
  readonly available: boolean
  readonly unavailableReason: string
  readonly hint: string
}

function MetricCard({ icon, label, value, available, unavailableReason, hint }: MetricCardProps) {
  return (
    <article className="v2-m6-metric-card" data-available={available ? 'true' : 'false'}>
      <div className="v2-m6-metric-head">
        <span className="v2-m6-metric-icon">{icon}</span>
        <span className="v2-m6-metric-label">{label}</span>
      </div>
      {available && value !== null ? (
        <strong className="v2-m6-metric-value">{value.toLocaleString('zh-CN')}</strong>
      ) : (
        <strong className="v2-m6-metric-value v2-m5-unavailable">unavailable</strong>
      )}
      <small>{available ? hint : unavailableReason}</small>
    </article>
  )
}

function QualityItem({
  label,
  value,
  format,
}: {
  readonly label: string
  readonly value: number | null
  readonly format?: 'ratio'
}) {
  const display = value === null
    ? null
    : format === 'ratio'
      ? `${(value * 100).toFixed(1)}%`
      : value.toLocaleString('zh-CN')

  return (
    <div className="v2-m6-quality-item">
      <dt>{label}</dt>
      <dd>{display ?? <span className="v2-m5-unavailable">unavailable</span>}</dd>
    </div>
  )
}
