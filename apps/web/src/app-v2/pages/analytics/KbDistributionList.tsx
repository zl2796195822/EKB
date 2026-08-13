import type { CSSProperties } from 'react'
import type { AnalyticsDistributionView } from '../../types'

interface KbDistributionListProps {
  readonly data: AnalyticsDistributionView
}

export function KbDistributionList({ data }: KbDistributionListProps) {
  // 条宽按"相对最热空间"归一化，而不是按占比——否则头部空间独大时其余全是细线。
  const peak = data.items.reduce((max, item) => Math.max(max, item.accesses), 0)

  return (
    <ul className="v2-a3-dist-list">
      {data.items.map((item) => (
        <li key={item.kbId} className="v2-a3-dist-row">
          <div className="v2-a3-dist-head">
            <span className="v2-a3-dist-name" title={item.name}>{item.name}</span>
            <span className="v2-a3-dist-value">
              {item.accesses.toLocaleString('zh-CN')} 次 · {item.share.toFixed(1)}%
            </span>
          </div>
          <div
            className="v2-a3-dist-track"
            aria-hidden="true"
            // 只把"数据"以 CSS 变量形式传下去，宽度怎么画仍由样式表决定（M0 规则）。
            style={
              {
                '--v2-a3-fill': `${peak > 0 ? Math.max(2, (item.accesses / peak) * 100) : 0}%`,
              } as CSSProperties
            }
          >
            <span className="v2-a3-dist-fill" />
          </div>
          <small className="v2-a3-dist-meta">{item.documents.toLocaleString('zh-CN')} 篇文档</small>
        </li>
      ))}
    </ul>
  )
}
