/**
 * AssistantMarkdown 安全测试 (PH4-7, spec 02 §11.3)
 *
 * 测试覆盖:
 * - XSS payload (script tags, event handlers, javascript: URLs)
 * - GFM 渲染 (table, task list, code blocks)
 * - 代码块复制按钮
 * - 流式光标
 * - 未闭合 code fence 不破坏 DOM
 */
// @vitest-environment jsdom
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { AssistantMarkdown } from '../components/assistant/AssistantMarkdown'

describe('AssistantMarkdown security', () => {
  it('strips script tags', () => {
    const { container } = render(
      <AssistantMarkdown content={'<script>alert("xss")</script>\n\ntext'} />
    )
    expect(container.querySelector('script')).toBeNull()
    expect(container.textContent).toContain('text')
  })

  it('strips event handlers', () => {
    const { container } = render(
      <AssistantMarkdown content={'<img src=x onerror="alert(1)">'} />
    )
    const img = container.querySelector('img')
    if (img) {
      expect(img.getAttribute('onerror')).toBeNull()
    }
  })

  it('blocks javascript: URLs', () => {
    const { container } = render(
      <AssistantMarkdown content={'[click](javascript:alert(1))'} />
    )
    const link = container.querySelector('a')
    expect(link).toBeTruthy()
    const href = link?.getAttribute('href') || ''
    expect(href).not.toMatch(/^javascript:/i)
  })

  it('renders external links with safe rel attributes', () => {
    const { container } = render(
      <AssistantMarkdown content={'[link](https://example.com)'} />
    )
    const link = container.querySelector('a')
    expect(link).toBeTruthy()
    expect(link?.getAttribute('rel')).toContain('noopener')
    expect(link?.getAttribute('rel')).toContain('nofollow')
    expect(link?.getAttribute('target')).toBe('_blank')
  })

  it('renders GFM tables', () => {
    const { container } = render(
      <AssistantMarkdown content={'| A | B |\n|---|---|\n| 1 | 2 |'} />
    )
    const table = container.querySelector('table')
    expect(table).toBeTruthy()
    expect(container.querySelectorAll('th').length).toBe(2)
    expect(container.querySelectorAll('td').length).toBe(2)
  })

  it('renders GFM task lists', () => {
    const { container } = render(
      <AssistantMarkdown content={'- [x] done\n- [ ] todo'} />
    )
    const checkboxes = container.querySelectorAll('input[type="checkbox"]')
    expect(checkboxes.length).toBe(2)
    expect(checkboxes[0].hasAttribute('checked')).toBe(true)
    expect(checkboxes[1].hasAttribute('checked')).toBe(false)
  })

  it('renders fenced code blocks with language label', () => {
    const { container } = render(
      <AssistantMarkdown content={'```python\nprint("hello")\n```'} />
    )
    const codeBlock = container.querySelector('.v2-md-code-block')
    expect(codeBlock).toBeTruthy()
    const langLabel = container.querySelector('.v2-md-code-lang')
    expect(langLabel?.textContent).toBe('PYTHON')
    const code = container.querySelector('code')
    expect(code?.textContent).toContain('print')
  })

  it('renders copy button on code blocks', () => {
    const { container } = render(
      <AssistantMarkdown content={'```js\nconst x = 1\n```'} />
    )
    const copyBtn = container.querySelector('.v2-md-code-copy')
    expect(copyBtn).toBeTruthy()
    expect(copyBtn?.textContent).toContain('复制')
  })

  it('does not crash on unterminated code fence', () => {
    const { container } = render(
      <AssistantMarkdown content={'```python\nprint("unfinished")'} streaming={true} />
    )
    // Should not throw; content should be visible
    expect(container.textContent).toContain('print')
  })

  it('renders streaming cursor when streaming', () => {
    const { container } = render(
      <AssistantMarkdown content={'Hello'} streaming={true} />
    )
    expect(container.querySelector('.v2-md-cursor')).toBeTruthy()
  })

  it('does not render streaming cursor when not streaming', () => {
    const { container } = render(
      <AssistantMarkdown content={'Hello'} streaming={false} />
    )
    expect(container.querySelector('.v2-md-cursor')).toBeNull()
  })

  it('renders inline code differently from code blocks', () => {
    const { container } = render(
      <AssistantMarkdown content={'This is `inline` code'} />
    )
    const inlineCode = container.querySelector('.v2-md-code-inline')
    expect(inlineCode).toBeTruthy()
    expect(inlineCode?.textContent).toBe('inline')
  })

  it('renders nested lists', () => {
    const { container } = render(
      <AssistantMarkdown content={'- item 1\n  - nested\n- item 2'} />
    )
    const lists = container.querySelectorAll('ul')
    expect(lists.length).toBeGreaterThanOrEqual(1)
    expect(container.textContent).toContain('nested')
  })

  it('renders blockquotes', () => {
    const { container } = render(
      <AssistantMarkdown content={'> This is a quote'} />
    )
    const blockquote = container.querySelector('blockquote')
    expect(blockquote).toBeTruthy()
    expect(blockquote?.textContent).toContain('quote')
  })

  it('returns null for empty content', () => {
    const { container } = render(
      <AssistantMarkdown content={''} />
    )
    const mdContainer = container.querySelector('.v2-md-container')
    expect(mdContainer).toBeNull()
  })

  it('handles oversized content without crash', () => {
    const big = 'x'.repeat(100000)
    const { container } = render(
      <AssistantMarkdown content={big} />
    )
    expect(container.textContent).toContain('x')
  })
})
