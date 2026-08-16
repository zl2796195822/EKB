// @vitest-environment jsdom
/**
 * 对话 UI 一致性回归（2026-08-16）：
 * - 流式消息使用骨架占位而非文本，body 带 is-streaming 类（最小高度防跳动）
 * - user/assistant 消息气泡类名与对称布局契约
 * - AssistantMarkdown 流式拆分：已闭合主体冻结 + 未闭合围栏尾部纯文本
 */

import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { AssistantMarkdown } from '../components/assistant/AssistantMarkdown'
import { AssistantMessageList, type AssistantDisplayMessage } from '../components/assistant/AssistantMessageList'

function makeMessage(overrides: Partial<AssistantDisplayMessage> = {}): AssistantDisplayMessage {
  return {
    id: 'm-1',
    role: 'assistant',
    content: '回答',
    createdAt: '2026-08-16T00:00:00Z',
    ...overrides,
  }
}

describe('AssistantMessageList streaming placeholder', () => {
  it('renders skeleton placeholder instead of text while waiting for first token', () => {
    const message = makeMessage({ content: '', streaming: true, transient: true })
    const { container } = render(
      <AssistantMessageList
        messages={[message]}
        streaming
        feedbackByMessage={{}}
        onFeedback={() => {}}
      />
    )
    const body = container.querySelector('.v2-m4-message-body')
    expect(body).toBeTruthy()
    expect(body?.classList.contains('is-streaming')).toBe(true)
    expect(container.querySelector('.v2-m4-stream-placeholder')).toBeTruthy()
    expect(container.textContent).not.toContain('正在等待首个回答片段')
  })

  it('marks assistant body with is-streaming and keeps user body plain', () => {
    const assistant = makeMessage({ streaming: true })
    const user = makeMessage({ role: 'user', content: '问题' })
    const { container } = render(
      <AssistantMessageList
        messages={[assistant, user]}
        streaming
        feedbackByMessage={{}}
        onFeedback={() => {}}
      />
    )
    const bodies = container.querySelectorAll('.v2-m4-message-body')
    expect(bodies).toHaveLength(2)
    expect(bodies[0].classList.contains('is-streaming')).toBe(true)
    expect(bodies[1].classList.contains('is-streaming')).toBe(false)
  })
})

describe('AssistantMarkdown streaming split', () => {
  it('renders unterminated fence tail as plain text with cursor', () => {
    const { container } = render(
      <AssistantMarkdown content={'```python\nprint("unfinished")'} streaming />
    )
    const tail = container.querySelector('.v2-md-stream-tail')
    expect(tail).toBeTruthy()
    expect(tail?.textContent).toContain('print')
    expect(container.querySelector('.v2-md-cursor')).toBeTruthy()
  })

  it('keeps closed content in the stable markdown body while streaming', () => {
    const { container } = render(
      <AssistantMarkdown content={'说明文字\n\n```python\nx = 1'} streaming />
    )
    expect(container.querySelector('.v2-md-stream-tail')).toBeTruthy()
    expect(container.textContent).toContain('说明文字')
    // 已闭合主体仍走 markdown 渲染（p 标签存在）
    expect(container.querySelector('.v2-md-container p')).toBeTruthy()
  })

  it('renders everything as markdown once the fence is closed', () => {
    const { container } = render(
      <AssistantMarkdown content={'```python\nx = 1\n```'} streaming />
    )
    expect(container.querySelector('.v2-md-stream-tail')).toBeNull()
    expect(container.querySelector('.v2-md-code-block')).toBeTruthy()
  })
})
