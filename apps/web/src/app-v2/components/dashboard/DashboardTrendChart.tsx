import { useMemo } from 'react'
import type { AnalyticsTrendView } from '../../types/analytics'

interface DashboardTrendChartProps {
  readonly data: AnalyticsTrendView
}

const VB_W = 560
const VB_H = 180
const PAD = { top: 20, right: 12, bottom: 24, left: 32 }

function xIdx(index: number, count: number) {
  const use = VB_W - PAD.left - PAD.right
  if (count <= 1) return PAD.left + use / 2
  return PAD.left + (use * index) / (count - 1)
}
function yVal(value: number, max: number) {
  const use = VB_H - PAD.top - PAD.bottom
  if (max <= 0) return VB_H - PAD.bottom
  return PAD.top + use * (1 - value / max)
}
function niceMax(value: number) {
  if (value <= 5) return 5
  const mag = 10 ** Math.floor(Math.log10(value))
  for (const s of [1, 2, 2.5, 5, 10]) {
    const c = s * mag
    if (c >= value) return c
  }
  return 10 * mag
}
function pathLine(values: readonly number[], max: number) {
  return values
    .map((v, i) => `${i === 0 ? 'M' : 'L'}${xIdx(i, values.length).toFixed(1)} ${yVal(v, max).toFixed(1)}`)
    .join(' ')
}
function pathArea(values: readonly number[], max: number) {
  if (!values.length) return ''
  const base = VB_H - PAD.bottom
  const f = xIdx(0, values.length).toFixed(1)
  const l = xIdx(values.length - 1, values.length).toFixed(1)
  return `${pathLine(values, max)} L${l} ${base} L${f} ${base} Z`
}
function shortDay(d: string) {
  return d.length >= 10 ? `${d.slice(5, 7)}/${d.slice(8, 10)}` : d
}

export function DashboardTrendChart({ data }: DashboardTrendChartProps) {
  const { points, peak, total } = data
  const yMax = niceMax(peak)
  const accesses = points.map((p) => p.accesses)
  const visitors = points.map((p) => p.visitors)
  const step = Math.max(1, Math.ceil(points.length / 6))

  const gridYs = useMemo(() => {
    const count = 4
    return Array.from({ length: count + 1 }, (_, i) => ({
      y: yVal((yMax * i) / count, yMax),
      v: Math.round((yMax * i) / count),
    }))
  }, [yMax])

  return (
    <svg
      className="v2-dashboard-trend-chart"
      viewBox={`0 0 ${VB_W} ${VB_H}`}
      role="img"
      aria-label={`访问趋势图，近 ${points.length} 天合计 ${total.toLocaleString('zh-CN')} 次访问`}
      preserveAspectRatio="none"
    >
      <defs>
        <linearGradient id="dash-trend-area" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="rgba(59,130,246,0.26)" />
          <stop offset="100%" stopColor="rgba(59,130,246,0.02)" />
        </linearGradient>
      </defs>

      {gridYs.map((g, i) => (
        <line
          key={i}
          x1={PAD.left}
          x2={VB_W - PAD.right}
          y1={g.y}
          y2={g.y}
          stroke="#E2E8F0"
          strokeDasharray={i === gridYs.length - 1 ? '0' : '3 3'}
          aria-hidden="true"
        />
      ))}
      {gridYs.map((g, i) => (
        <text
          key={`t${i}`}
          x={PAD.left - 6}
          y={g.y + 3}
          textAnchor="end"
          fontSize="10"
          fill="#94A3B8"
          fontFamily="ui-monospace, JetBrains Mono, Menlo, monospace"
          aria-hidden="true"
        >
          {g.v}
        </text>
      ))}

      <path d={pathArea(accesses, yMax)} fill="url(#dash-trend-area)" aria-hidden="true" />
      <path
        d={pathLine(accesses, yMax)}
        fill="none"
        stroke="#3B82F6"
        strokeWidth="2"
        strokeLinejoin="round"
        strokeLinecap="round"
        aria-hidden="true"
      />
      <path
        d={pathLine(visitors, yMax)}
        fill="none"
        stroke="#10B981"
        strokeWidth="1.5"
        strokeDasharray="4 3"
        strokeLinejoin="round"
        strokeLinecap="round"
        aria-hidden="true"
      />

      {accesses.map((_, i) => (
        <circle
          key={`ac${i}`}
          cx={xIdx(i, points.length)}
          cy={yVal(accesses[i], yMax)}
          r="2.5"
          fill="#fff"
          stroke="#3B82F6"
          strokeWidth="1.2"
          aria-hidden="true"
        />
      ))}

      {points.map((p, i) =>
        i % step === 0 ? (
          <text
            key={`dx${i}`}
            x={xIdx(i, points.length)}
            y={VB_H - 8}
            textAnchor="middle"
            fontSize="10"
            fill="#64748B"
            fontFamily="ui-monospace, JetBrains Mono, Menlo, monospace"
            aria-hidden="true"
          >
            {shortDay(p.day)}
          </text>
        ) : null,
      )}
    </svg>
  )
}
