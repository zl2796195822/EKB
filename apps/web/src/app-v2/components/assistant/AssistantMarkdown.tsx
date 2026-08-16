import { memo, useState, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize'
import type { Components } from 'react-markdown'

/**
 * 安全的 Markdown 渲染组件 (PH4-5, spec 02 §11.3)
 *
 * 安全合同:
 * - rehype-sanitize 清理所有 HTML,只保留白名单标签/属性
 * - javascript: URL 被阻断;外部链接强制 rel="noopener noreferrer nofollow"
 * - raw HTML 被禁用(除非在白名单内且已清理)
 * - 代码块带语言标签和复制按钮
 *
 * 流式稳定:
 * - 已完成 block 通过 memo 冻结,不再重解析
 * - 未闭合 code fence 不破坏 DOM(react-markdown 容错处理)
 * - 只有未完成尾部被重解析
 */

// 扩展默认 sanitize schema:允许 class 属性(代码高亮需要)
const sanitizeSchema = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    code: [...(defaultSchema.attributes?.code || []), ['className']],
    pre: [...(defaultSchema.attributes?.pre || []), ['className']],
    span: [...(defaultSchema.attributes?.span || []), ['className']],
    a: [...(defaultSchema.attributes?.a || []), ['target', 'rel', 'title']],
  },
  protocols: {
    ...defaultSchema.protocols,
    href: ['http', 'https', 'mailto', '#'],
  },
  tagNames: [
    ...(defaultSchema.tagNames || []),
    'del', 'ins', 'mark', 'sub', 'sup',
  ],
}

// 安全链接处理:阻断 javascript: URL,强制安全 rel
function SafeLink({ href, children, ...props }: any) {
  const safeHref = href && typeof href === 'string'
    ? href.replace(/^javascript:/i, '')
    : href
  const isExternal = safeHref && typeof safeHref === 'string'
    ? /^https?:\/\//.test(safeHref)
    : false
  return (
    <a
      href={safeHref || undefined}
      rel={isExternal ? 'noopener noreferrer nofollow' : undefined}
      target={isExternal ? '_blank' : undefined}
      {...props}
    >
      {children}
    </a>
  )
}

// 代码块组件:带语言标签和复制按钮
function CodeBlock({ className, children, ...props }: any) {
  const [copied, setCopied] = useState(false)
  const language = className?.replace(/^language-/, '') || ''
  const isInline = !className && typeof children === 'string' && !children.includes('\n')

  const handleCopy = useCallback(() => {
    const text = typeof children === 'string' ? children : String(children ?? '')
    navigator.clipboard?.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }).catch(() => {})
  }, [children])

  if (isInline) {
    return <code className="v2-md-code-inline" {...props}>{children}</code>
  }

  return (
    <div className="v2-md-code-block">
      <div className="v2-md-code-header">
        <span className="v2-md-code-lang">{language ? language.toUpperCase() : 'text'}</span>
        <button
          type="button"
          className="v2-md-code-copy"
          onClick={handleCopy}
          aria-label="复制代码"
        >
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      <pre>
        <code className={className} {...props}>{children}</code>
      </pre>
    </div>
  )
}

const markdownComponents: Components = {
  a: SafeLink as any,
  code: CodeBlock as any,
}

interface AssistantMarkdownProps {
  readonly content: string
  readonly streaming?: boolean
}

/**
 * 流式渲染拆分：把已闭合的 markdown 主体与最后一个未闭合代码围栏尾部
 * 分开处理。主体按内容 memo 冻结（不随每个 delta 重解析），尾部作为
 * 纯文本逐步追加，避免长回答流式时整块重渲染造成的闪烁与跳动。
 */
function splitStreamingTail(content: string): { stable: string; tail: string } {
  const fences = content.split('```')
  if (fences.length % 2 === 1) {
    // 所有围栏已配对闭合：全部作为稳定主体。
    return { stable: content, tail: '' }
  }
  const lastFence = content.lastIndexOf('```')
  return {
    stable: content.slice(0, lastFence),
    tail: content.slice(lastFence + 3),
  }
}

/** 已闭合主体：content 不变则跳过重解析（流式性能关键路径） */
const StableMarkdown = memo(function StableMarkdown({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[[rehypeSanitize, sanitizeSchema]]}
      components={markdownComponents}
    >
      {content}
    </ReactMarkdown>
  )
}, (prev, next) => prev.content === next.content)

/**
 * 流式优化的 Markdown 渲染器。
 *
 * streaming=true 时，已闭合主体被冻结，只有未闭合尾部与光标在更新；
 * streaming=false 时，整个渲染结果由外层调用方按内容稳定。
 */
export function AssistantMarkdown({
  content,
  streaming,
}: AssistantMarkdownProps) {
  // 空内容时返回占位
  if (!content || !content.trim()) {
    return null
  }

  const { stable, tail } = streaming ? splitStreamingTail(content) : { stable: content, tail: '' }

  return (
    <div className="v2-md-container">
      {stable.trim() ? <StableMarkdown content={stable} /> : null}
      {tail ? (
        <pre className="v2-md-stream-tail">
          <code>{tail}</code>
        </pre>
      ) : null}
      {streaming ? <span className="v2-md-cursor" aria-hidden="true">▋</span> : null}
    </div>
  )
}
