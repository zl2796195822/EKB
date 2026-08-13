import { describe, expect, it } from 'vitest'
import {
  buildConversationMarkdown,
  canExportConversation,
  type ConversationExportMessage,
} from '../pages/conversationExport'

const messages: readonly ConversationExportMessage[] = [
  { role: 'user', content: '请总结当前知识库。' },
  { role: 'assistant', content: '这是服务端持久回答。' },
  { role: 'assistant', content: '这是流式临时回答。', transient: true },
]

const transientOnlyMessages: readonly ConversationExportMessage[] = [
  { role: 'assistant', content: '尚未持久化的流式回答。', transient: true },
]

describe('conversation markdown export', () => {
  it('exports the title and persistent messages while filtering transient messages', () => {
    expect(buildConversationMarkdown(messages, '知识库问答')).toBe(
      '# 知识库问答\n\n## 你\n\n请总结当前知识库。\n\n## AI 助手\n\n这是服务端持久回答。\n',
    )
  })

  it('enables export only when messages are ready, present, and not streaming', () => {
    expect(canExportConversation('ready', messages, false)).toBe(true)
    expect(canExportConversation('loading', messages, false)).toBe(false)
    expect(canExportConversation('ready', [], false)).toBe(false)
    expect(canExportConversation('ready', transientOnlyMessages, false)).toBe(false)
    expect(canExportConversation('ready', messages, true)).toBe(false)
  })
})
