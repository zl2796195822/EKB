import { useMemo } from 'react'
import type { AnalyticsTrendView } from '../../types'

interface AccessTrendChartProps {
  readonly data: AnalyticsTrendView
}

const VIEWBOX_WIDTH = 720
const VIEWBOX_HEIGHT = 240
const PADDING = { top: 28, right: 16, bottom: 32, left: 44 }

// ---- 涨红跌绿调色板 (CN convention: red for rise, green for fall) ----
const PALETTE = {
  rise: {
    line: '#E03131',
    lineSoft: '#FF6B6B',
    areaFrom: 'rgba(224,49,49,0.28)',
    areaTo: 'rgba(224,49,49,0.02)',
    dot: '#E03131',
    badgeBg: '#FFF1F0',
    badgeFg: '#CF1322',
    badgePrefix: '+',
  },
  fall: {
    line: '#00A870',
    lineSoft: '#34C38F',
    areaFrom: 'rgba(0,168,112,0.26)',
    areaTo: 'rgba(0,168,112,0.02)',
    dot: '#00A870',
    badgeBg: '#E6FFF4',
    badgeFg: '#00824B',
    badgePrefix: '',
  },
  flat: {
    line: '#3B82F6',
    lineSoft: '#60A5FA',
    areaFrom: 'rgba(59,130,246,0.24)',
    areaTo: 'rgba(59,130,246,0.02)',
    dot: '#3B82F6',
    badgeBg: '#EFF6FF',
    badgeFg: '#1D4ED8',
    badgePrefix: '',
  },
  secondary: {
    line: '#94A3B8',
    lineSoft: '#CBD5E1',
    dot: '#94A3B8',
  },
  grid: '#E2E8F0',
  tick: '#64748B',
} as const

type TrendDirection = 'rise' | 'fall' | 'flat'

/** 把 1..n 个点均匀铺在绘图区上；单点时贴左边缘，避免除零。 */
function xFor(index: number, count: number): number {
  const usable = VIEWBOX_WIDTH - PADDING.left - PADDING.right
  if (count <= 1) return PADDING.left + usable / 2
  return PADDING.left + (usable * index) / (count - 1)
}

function yFor(value: number, max: number): number {
  const usable = VIEWBOX_HEIGHT - PADDING.top - PADDING.bottom
  if (max <= 0) return VIEWBOX_HEIGHT - PADDING.bottom
  return PADDING.top + usable * (1 - value / max)
}

/** 把峰值抬到一个整齐的刻度上界，纵轴标签才不会出现 37 这种数字。 */
function niceCeil(value: number): number {
  if (value <= 4) return 4
  const magnitude = 10 ** Math.floor(Math.log10(value))
  for (const step of [1, 2, 2.5, 5, 10]) {
    const candidate = step * magnitude
    if (candidate >= value) return candidate
  }
  return 10 * magnitude
}

function pathFor(values: readonly number[], max: number): string {
  return values
    .map((value, index) => {
      const command = index === 0 ? 'M' : 'L'
      return `${command}${xFor(index, values.length).toFixed(1)} ${yFor(value, max).toFixed(1)}`
    })
    .join(' ')
}

function areaFor(values: readonly number[], max: number): string {
  if (values.length === 0) return ''
  const baseline = VIEWBOX_HEIGHT - PADDING.bottom
  const first = xFor(0, values.length).toFixed(1)
  const last = xFor(values.length - 1, values.length).toFixed(1)
  return `${pathFor(values, max)} L${last} ${baseline} L${first} ${baseline} Z`
}

function shortDay(day: string): string {
  return day.length >= 10 ? `${day.slice(5, 7)}/${day.slice(8, 10)}` : day
}

/** 前后两半的总和差分 → 涨跌方向。delta/total 归一化用于 badge。 */
function classifyTrend(points: readonly { accesses: number }[]): {
  direction: TrendDirection
  deltaPct: number
  deltaAbs: number
  baseline: number
} {
  if (points.length === 0) {
    return { direction: 'flat', deltaPct: 0, deltaAbs: 0, baseline: 0 }
  }
  const mid = Math.max(1, Math.floor(points.length / 2))
  const firstHalf = points.slice(0, mid).reduce((sum, p) => sum + p.accesses, 0)
  const secondHalf = points.slice(mid).reduce((sum, p) => sum + p.accesses, 0)
  const baseline = firstHalf === 0 ? 1 : firstHalf
  const deltaAbs = secondHalf - firstHalf
  // 1% 以下的波动视为平稳，避免 2→3 渲染成 50% 暴涨。
  const ratio = deltaAbs / baseline
  if (ratio > 0.01) return { direction: 'rise', deltaPct: ratio, deltaAbs, baseline: firstHalf }
  if (ratio < -0.01) return { direction: 'fall', deltaPct: ratio, deltaAbs, baseline: firstHalf }
  return { direction: 'flat', deltaPct: ratio, deltaAbs, baseline: firstHalf }
}

function formatPct(ratio: number): string {
  if (!Number.isFinite(ratio)) return '0%'
  const value = Math.abs(ratio) * 100
  if (value >= 1000) return `${Math.round(value).toLocaleString()}%`
  if (value >= 10) return `${value.toFixed(0)}%`
  return `${value.toFixed(1)}%`
}

/** 渐变色 id 在同页多实例时必须唯一；用 data.peak/days 作非加密散列。 */
function gradientId(prefix: string, data: AnalyticsTrendView): string {
  let seed = prefix.length + data.points.length + data.peak + data.days
  for (const point of data.points.slice(-5)) {
    seed += point.accesses + point.visitors
  }
  return `${prefix}-${Math.abs(seed % 999991)}`
}

export function AccessTrendChart({ data }: AccessTrendChartProps) {
  const chart = useMemo(() => {
    const accesses = data.points.map((point) => point.accesses)
    const visitors = data.points.map((point) => point.visitors)
    const max = niceCeil(Math.max(data.peak, ...visitors, 1))
    const step = Math.max(1, Math.ceil(data.points.length / 7))
    const trend = classifyTrend(data.points)
    const palette = PALETTE[trend.direction]
    // 标注峰值点（首遇的最大值）与谷值点（首遇的最小值；0 忽略）
    const peakIndex = accesses.indexOf(Math.max(...accesses, 0))
    const nonZero = accesses.map((v, i) => ({ v, i })).filter((x) => x.v > 0)
    const valleyIndex = nonZero.length
      ? nonZero.reduce((a, b) => (a.v <= b.v ? a : b)).i
      : -1
    return {
      max,
      accessPath: pathFor(accesses, max),
      accessArea: areaFor(accesses, max),
      visitorPath: pathFor(visitors, max),
      ticks: [0, 0.5, 1].map((ratio) => ({
        value: Math.round(max * ratio),
        y: yFor(max * ratio, max),
      })),
      labels: data.points
        .map((point, index) => ({ point, index }))
        .filter(({ index }) => index % step === 0 || index === data.points.length - 1),
      trend,
      palette,
      peakIndex,
      valleyIndex,
      areaGradientId: gradientId('a3-area', data),
      peakBadgeId: gradientId('a3-peak', data),
      dotR: data.points.length > 30 ? 1.6 : 3,
    }
  }, [data])

  const { trend, palette } = chart

  return (
    <div className="v2-a3-chart">
      <svg
        viewBox={`0 0 ${VIEWBOX_WIDTH} ${VIEWBOX_HEIGHT}`}
        role="img"
        aria-label={`最近 ${data.days} 天访问量与独立访客趋势，合计 ${data.total} 次访问`}
        preserveAspectRatio="none"
      >
        <defs>
          <linearGradient id={chart.areaGradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={palette.areaFrom} />
            <stop offset="100%" stopColor={palette.areaTo} />
          </linearGradient>
          <filter id={`${chart.peakBadgeId}-shadow`} x="-30%" y="-40%" width="160%" height="180%">
            <feDropShadow dx="0" dy="1" stdDeviation="1.2" floodOpacity="0.14" />
          </filter>
        </defs>

        {/* 网格 + 纵轴 */}
        {chart.ticks.map((tick) => (
          <g key={tick.value}>
            <line
              x1={PADDING.left}
              x2={VIEWBOX_WIDTH - PADDING.right}
              y1={tick.y}
              y2={tick.y}
              stroke={PALETTE.grid}
              strokeDasharray={tick.value === 0 ? '0' : '3 4'}
              strokeWidth={tick.value === 0 ? 1 : 0.75}
            />
            <text
              x={PADDING.left - 8}
              y={tick.y + 4}
              textAnchor="end"
              fontSize="10"
              fill={PALETTE.tick}
            >
              {tick.value}
            </text>
          </g>
        ))}

        {/* 面积 + 双折线 */}
        <path d={chart.accessArea} fill={`url(#${chart.areaGradientId})`} />
        <path
          d={chart.visitorPath}
          fill="none"
          stroke={PALETTE.secondary.line}
          strokeDasharray="4 3"
          strokeWidth="1.4"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <path
          d={chart.accessPath}
          fill="none"
          stroke={palette.line}
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />

        {/* 涨跌徽章 — 贴在左上角绘图区内部 */}
        <g transform={`translate(${PADDING.left + 6}, ${PADDING.top + 4})`}>
          <rect
            x="0"
            y="0"
            rx="6"
            ry="6"
            width="86"
            height="22"
            fill={palette.badgeBg}
            stroke={palette.badgeFg}
            strokeOpacity="0.18"
            filter={`url(#${chart.peakBadgeId}-shadow)`}
          />
          <text
            x="10"
            y="15"
            fontSize="11"
            fontWeight="600"
            fill={palette.badgeFg}
          >
            {trend.direction === 'rise' ? '▲ ' : trend.direction === 'fall' ? '▼ ' : '● '}
            {palette.badgePrefix}
            {formatPct(trend.deltaPct)}
          </text>
        </g>

        {/* 峰值/谷值小圆标记 + 竖线 */}
        {chart.peakIndex >= 0 && data.points[chart.peakIndex] && (
          <g>
            <line
              x1={xFor(chart.peakIndex, data.points.length)}
              x2={xFor(chart.peakIndex, data.points.length)}
              y1={yFor(data.points[chart.peakIndex].accesses, chart.max) - 10}
              y2={VIEWBOX_HEIGHT - PADDING.bottom}
              stroke={palette.line}
              strokeOpacity="0.22"
              strokeDasharray="2 3"
            />
            <circle
              cx={xFor(chart.peakIndex, data.points.length)}
              cy={yFor(data.points[chart.peakIndex].accesses, chart.max)}
              r={chart.dotR + 2.4}
              fill="white"
              stroke={palette.line}
              strokeWidth="1.6"
            />
          </g>
        )}
        {chart.valleyIndex >= 0 &&
          chart.valleyIndex !== chart.peakIndex &&
          data.points[chart.valleyIndex] && (
            <circle
              cx={xFor(chart.valleyIndex, data.points.length)}
              cy={yFor(data.points[chart.valleyIndex].accesses, chart.max)}
              r={chart.dotR + 1.2}
              fill="none"
              stroke={palette.lineSoft}
              strokeWidth="1.2"
              strokeDasharray="2 2"
            />
          )}

        {/* 数据点 + tooltip */}
        {data.points.map((point, index) => (
          <circle
            key={point.day}
            cx={xFor(index, data.points.length)}
            cy={yFor(point.accesses, chart.max)}
            r={chart.dotR}
            fill={index === chart.peakIndex ? palette.dot : palette.lineSoft}
            stroke="white"
            strokeWidth="0.8"
          >
            <title>
              {`${point.day} · 访问 ${point.accesses.toLocaleString()} · 访客 ${point.visitors.toLocaleString()}`}
            </title>
          </circle>
        ))}

        {/* x 轴日期 */}
        {chart.labels.map(({ point, index }) => (
          <text
            key={`label-${point.day}`}
            x={xFor(index, data.points.length)}
            y={VIEWBOX_HEIGHT - 10}
            textAnchor="middle"
            fontSize="10"
            fill={PALETTE.tick}
          >
            {shortDay(point.day)}
          </text>
        ))}

        {/* 图例：访问量/访客 两条线，放在图表右上 */}
        <g transform={`translate(${VIEWBOX_WIDTH - PADDING.right - 162}, ${PADDING.top + 6})`}>
          <line x1="0" y1="6" x2="16" y2="6" stroke={palette.line} strokeWidth="2.2" strokeLinecap="round" />
          <text x="22" y="10" fontSize="11" fill={PALETTE.tick}>
            访问量
          </text>
          <line x1="88" y1="6" x2="104" y2="6" stroke={PALETTE.secondary.line} strokeWidth="1.4" strokeDasharray="4 3" />
          <text x="110" y="10" fontSize="11" fill={PALETTE.tick}>
            独立访客
          </text>
        </g>
      </svg>
    </div>
  )
}
