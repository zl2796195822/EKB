import type {
  AnalyticsAccessKind,
  AnalyticsActivityView,
  AnalyticsResourceKind,
} from '../../types'

interface RecentActivityListProps {
  readonly data: AnalyticsActivityView
}

const KIND_LABEL: Readonly<Record<AnalyticsAccessKind, string>> = {
  VIEW: '查看',
  ASK: '提问',
  CREATE: '创建',
  UPDATE: '更新',
  DELETE: '删除',
  RESTORE: '还原',
  SHARE: '授权',
}

const RESOURCE_LABEL: Readonly<Record<AnalyticsResourceKind, string>> = {
  KB: '知识空间',
  DOCUMENT: '文档',
  CONVERSATION: '会话',
}

function formatMoment(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function RecentActivityList({ data }: RecentActivityListProps) {
  return (
    <ol className="v2-a3-activity-list">
      {data.items.map((item) => (
        <li key={item.id} className="v2-a3-activity-row">
          <span
            className="v2-a3-activity-kind"
            data-kind={item.accessKind}
          >
            {KIND_LABEL[item.accessKind]}
          </span>
          <div className="v2-a3-activity-body">
            <span className="v2-a3-activity-title" title={item.title}>{item.title}</span>
            <small>
              {RESOURCE_LABEL[item.resourceType]}
              {' · '}
              {/* 操作人可能已被移除，只留 actor_id；此时展示"未知操作人"而不是裸 UUID。 */}
              {item.actorName ?? (item.actorId ? '已注销成员' : '未知操作人')}
            </small>
          </div>
          <time className="v2-a3-activity-time" dateTime={item.occurredAt}>
            {formatMoment(item.occurredAt)}
          </time>
        </li>
      ))}
    </ol>
  )
}
