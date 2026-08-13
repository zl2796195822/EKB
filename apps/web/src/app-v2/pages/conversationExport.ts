import type { PageState } from '../types'

export interface ConversationExportMessage {
  readonly role: 'user' | 'assistant' | 'system'
  readonly content: string
  readonly transient?: boolean
}

const ROLE_LABELS: Record<ConversationExportMessage['role'], string> = {
  user: '你',
  assistant: 'AI 助手',
  system: '系统',
}

export function buildConversationMarkdown(messages: readonly ConversationExportMessage[], title: string): string {
  const heading = title.trim() || '未命名会话'
  const sections = messages
    .filter((message) => !message.transient)
    .map((message) => `## ${ROLE_LABELS[message.role]}\n\n${message.content}`)

  return `${[`# ${heading}`, ...sections].join('\n\n')}\n`
}

export function canExportConversation(
  state: PageState,
  messages: readonly ConversationExportMessage[],
  isStreaming: boolean,
): boolean {
  return state === 'ready' && !isStreaming && messages.some((message) => !message.transient)
}
