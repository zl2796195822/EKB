import { ArrowRight, FileText, FolderOpen, ChatCircleDots } from '@phosphor-icons/react'
import { useEffect, useState, type ReactNode } from 'react'
import { StatePanel } from '../StatePanel'
import type {
  AnalyticsActivityItemView,
  AnalyticsActivityResult,
  AnalyticsServices,
  PageState,
} from '../../types'

const RECENT_TABS = [
  { id: 'recent', label: '最近访问' },
  { id: 'documents', label: '文档' },
  { id: 'knowledge', label: '知识库' },
  { id: 'assistant', label: 'AI 问答' },
] as const

type RecentTab = (typeof RECENT_TABS)[number]['id']

const TABLE_HEADERS = ['名称', '类型', '操作人', '最近访问', '操作']

const TYPE_LABEL: Record<string, { label: string; icon: ReactNode; className: string }> = {
  DOCUMENT: { label: '文档', icon: <FileText size={13} aria-hidden="true" />, className: 'v2-dash-type-doc' },
  KB: { label: '知识库', icon: <FolderOpen size={13} aria-hidden="true" />, className: 'v2-dash-type-kb' },
  CONVERSATION: { label: 'AI 问答', icon: <ChatCircleDots size={13} aria-hidden="true" />, className: 'v2-dash-type-qa' },
}

const KIND_LABEL: Record<string, string> = { VIEW: '查看', EDIT: '编辑', DELETE: '删除', CREATE: '创建', ASK: '提问' }

const ACTIVITY_LIMIT = 20

interface RecentAccessSectionProps {
  readonly analytics: Pick<AnalyticsServices, 'getActivity'>
}

function formatTime(occurredAt: string): string {
  const d = new Date(occurredAt)
  if (Number.isNaN(d.getTime())) return '—'
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  if (sameDay) return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(d)
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(d)
}

function typeFor(item: AnalyticsActivityItemView): keyof typeof TYPE_LABEL {
  if (item.resourceType === 'CONVERSATION') return 'CONVERSATION'
  if (item.resourceType === 'KB') return 'KB'
  return 'DOCUMENT'
}

export function RecentAccessSection({ analytics }: RecentAccessSectionProps) {
  const [activeTab, setActiveTab] = useState<RecentTab>('recent')
  const [result, setResult] = useState<AnalyticsActivityResult | null>(null)

  useEffect(() => {
    void analytics.getActivity(ACTIVITY_LIMIT).then(setResult)
  }, [analytics])

  const state: PageState = result?.state ?? 'loading'
  const allItems = result?.data?.items ?? []
  const visibleItems: readonly AnalyticsActivityItemView[] = activeTab === 'recent'
    ? allItems
    : allItems.filter((item) => {
        if (activeTab === 'documents') return item.resourceType === 'DOCUMENT'
        if (activeTab === 'knowledge') return item.resourceType === 'KB'
        if (activeTab === 'assistant') return item.resourceType === 'CONVERSATION'
        return false
      })

  return (
    <section className="v2-dashboard-panel v2-dashboard-recent" aria-labelledby="v2-dashboard-recent-title">
      <div className="v2-dashboard-tab-row">
        <div className="v2-dashboard-tabs" role="tablist" aria-label="最近内容分类">
          {RECENT_TABS.map((tab) => (
            <button
              type="button"
              className={activeTab === tab.id ? 'v2-dashboard-tab v2-dashboard-tab--active' : 'v2-dashboard-tab'}
              key={tab.id}
              id={`v2-dashboard-tab-${tab.id}`}
              role="tab"
              aria-selected={activeTab === tab.id}
              aria-controls={`v2-dashboard-tabpanel-${tab.id}`}
              onClick={() => setActiveTab(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>
        <a className="v2-dashboard-text-link" href="#/documents">
          查看全部文档
          <ArrowRight size={13} aria-hidden="true" />
        </a>
      </div>

      <div
        className="v2-dashboard-tabpanel"
        id={`v2-dashboard-tabpanel-${activeTab}`}
        role="tabpanel"
        aria-labelledby={`v2-dashboard-tab-${activeTab}`}
        tabIndex={0}
      >
        <div className="v2-dashboard-table-wrap">
          <table>
            <thead>
              <tr>
                {TABLE_HEADERS.map((header) => (
                  <th key={header} scope="col">{header}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {state === 'loading' ? (
                <tr>
                  <td colSpan={TABLE_HEADERS.length}>
                    <StatePanel state="loading" message="正在加载最近访问记录。" />
                  </td>
                </tr>
              ) : state === 'error' || state === 'permission-denied' ? (
                <tr>
                  <td colSpan={TABLE_HEADERS.length}>
                    <StatePanel state={state} message="最近活动加载失败，已自动回退为不渲染假数据。" />
                  </td>
                </tr>
              ) : visibleItems.length === 0 ? (
                <tr>
                  <td colSpan={TABLE_HEADERS.length}>
                    <StatePanel state="empty" message={allItems.length === 0 ? '当前访问日志还没有记录。先浏览几篇文档或做一次 AI 问答试试。' : '该分类暂无记录。'} />
                  </td>
                </tr>
              ) : (
                visibleItems.map((item) => {
                  const t = TYPE_LABEL[typeFor(item)] ?? TYPE_LABEL.DOCUMENT
                  return (
                    <tr key={item.id}>
                      <td className="v2-dash-cell-title">
                        <a href={item.resourceType === 'DOCUMENT' ? '#/documents' : item.resourceType === 'KB' ? '#/knowledge' : '#/assistant'} className="v2-dash-cell-title-link">
                          {item.title}
                        </a>
                      </td>
                      <td>
                        <span className={`v2-dash-type-pill ${t.className}`}>
                          {t.icon}
                          <span>{t.label}</span>
                        </span>
                      </td>
                      <td className="v2-dash-cell-muted">{item.actorName || '—'}</td>
                      <td className="v2-dash-cell-muted tabular-nums">
                        <span title={KIND_LABEL[item.accessKind] ?? item.accessKind}>{formatTime(item.occurredAt)}</span>
                      </td>
                      <td>
                        <a href={item.resourceType === 'DOCUMENT' ? '#/documents' : item.resourceType === 'KB' ? '#/knowledge' : '#/assistant'} className="v2-dashboard-text-link v2-dashboard-text-button">
                          跳转
                          <ArrowRight size={12} aria-hidden="true" />
                        </a>
                      </td>
                    </tr>
                  )
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      <p className="v2-sr-only" id="v2-dashboard-recent-title">最近访问</p>
    </section>
  )
}
