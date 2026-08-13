import type { AnalyticsActivityView } from '../../types/analytics'

interface DashboardActivityFeedProps {
  readonly data: AnalyticsActivityView
  readonly maxItems?: number
}

const TYPE_META: Record<string, { label: string; cls: string; dot: string }> = {
  DOCUMENT: { label: '文档', cls: 'v2-dash-act-doc', dot: '#3B82F6' },
  KB: { label: '知识库', cls: 'v2-dash-act-kb', dot: '#10B981' },
  CONVERSATION: { label: 'AI 问答', cls: 'v2-dash-act-qa', dot: '#F59E0B' },
}

const KIND_LABEL: Record<string, string> = {
  VIEW: '查看',
  EDIT: '编辑',
  UPDATE: '更新',
  DELETE: '删除',
  CREATE: '创建',
  RESTORE: '恢复',
  ASK: '提问',
  SHARE: '分享',
}

function fmtTime(at: string): string {
  const d = new Date(at)
  if (Number.isNaN(d.getTime())) return '—'
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  const sameY = d.toDateString() === yesterday.toDateString()
  const fmtHM = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(d)
  if (sameDay) return `今天 ${fmtHM}`
  if (sameY) return `昨天 ${fmtHM}`
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(d)
}

export function DashboardActivityFeed({ data, maxItems = 6 }: DashboardActivityFeedProps) {
  const items = data.items.slice(0, maxItems)
  if (items.length === 0) {
    return (
      <div style={{ padding: '24px 0', textAlign: 'center', fontSize: 12, color: '#94A3B8' }}>
        最近还没有任何知识资源动态。
      </div>
    )
  }
  return (
    <ol className="v2-dashboard-activity-list" style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
      {items.map((item) => {
        const meta = TYPE_META[item.resourceType] ?? TYPE_META.DOCUMENT
        const kind = KIND_LABEL[item.accessKind] ?? item.accessKind
        return (
          <li
            key={item.id}
            style={{
              display: 'flex',
              alignItems: 'flex-start',
              gap: 10,
              padding: '6px 4px',
              borderRadius: 6,
            }}
          >
            <span
              aria-hidden="true"
              style={{
                width: 6,
                height: 6,
                borderRadius: 999,
                marginTop: 7,
                background: meta.dot,
                flexShrink: 0,
                boxShadow: `0 0 0 3px ${meta.dot}22`,
              }}
            />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'baseline',
                  gap: 8,
                }}
              >
                <span
                  style={{
                    color: '#0F172A',
                    fontSize: 13,
                    fontWeight: 500,
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    maxWidth: '70%',
                  }}
                  title={item.title}
                >
                  {item.title || '（已删除资源）'}
                </span>
                <time
                  dateTime={item.occurredAt}
                  style={{
                    fontSize: 10,
                    color: '#94A3B8',
                    fontFamily: 'ui-monospace, JetBrains Mono, Menlo, monospace',
                    fontVariantNumeric: 'tabular-nums',
                    flexShrink: 0,
                  }}
                >
                  {fmtTime(item.occurredAt)}
                </time>
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 2, fontSize: 11, color: '#64748B' }}>
                <span
                  style={{
                    padding: '1px 6px',
                    borderRadius: 4,
                    background: `${meta.dot}14`,
                    color: meta.dot,
                    fontWeight: 500,
                    fontSize: 10,
                  }}
                >
                  {meta.label}
                </span>
                <span>{kind}</span>
                {item.actorName ? <span>· {item.actorName}</span> : null}
              </div>
            </div>
          </li>
        )
      })}
    </ol>
  )
}
