import type { AnalyticsDistributionView } from '../../types/analytics'

interface DashboardDistributionBarProps {
  readonly data: AnalyticsDistributionView
  readonly maxBars?: number
}

const KB_COLORS = ['#3B82F6', '#10B981', '#F59E0B', '#6366F1', '#EC4899', '#14B8A6']

export function DashboardDistributionBar({ data, maxBars = 5 }: DashboardDistributionBarProps) {
  const items = [...data.items].slice(0, maxBars)
  const maxAccess = Math.max(1, ...items.map((i) => i.accesses))

  if (items.length === 0) {
    return (
      <div className="v2-dashboard-dist-empty" style={{ padding: '24px 0', textAlign: 'center', fontSize: 12, color: '#94A3B8' }}>
        所选区间还没有任何知识空间访问。
      </div>
    )
  }

  return (
    <ul className="v2-dashboard-dist-list" style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 12 }}>
      {items.map((item, i) => {
        const widthPct = (item.accesses / maxAccess) * 100
        const share = item.share.toFixed(1)
        const color = KB_COLORS[i % KB_COLORS.length]
        return (
          <li key={item.kbId} style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', fontSize: 12 }}>
              <span
                style={{
                  color: '#0F172A',
                  fontWeight: 500,
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  maxWidth: '62%',
                }}
                title={item.name}
              >
                {item.name || '未命名知识空间'}
              </span>
              <span
                style={{
                  color: '#64748B',
                  fontFamily: 'ui-monospace, JetBrains Mono, Menlo, monospace',
                  fontSize: 11,
                  fontVariantNumeric: 'tabular-nums',
                }}
              >
                {item.accesses.toLocaleString('zh-CN')} 次 · {share}%
              </span>
            </div>
            <div
              style={{
                height: 8,
                background: '#F1F5F9',
                borderRadius: 4,
                overflow: 'hidden',
                position: 'relative',
              }}
              aria-hidden="true"
            >
              <div
                style={{
                  width: `${widthPct}%`,
                  height: '100%',
                  background: `linear-gradient(90deg, ${color}, ${color}BB)`,
                  borderRadius: 4,
                  transition: 'width .45s cubic-bezier(.2,.8,.2,1)',
                }}
              />
            </div>
            <div style={{ display: 'flex', gap: 12, fontSize: 10, color: '#94A3B8' }}>
              <span>{item.documents.toLocaleString('zh-CN')} 篇文档</span>
            </div>
          </li>
        )
      })}
    </ul>
  )
}
