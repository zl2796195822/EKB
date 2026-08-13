import { ArrowRight, CheckCircle, LockKey, ShieldCheck } from '@phosphor-icons/react'
import { useState, type FormEvent } from 'react'
import { BrandMark } from '../components/ui'
import type { AuthNotice } from '../types'

interface LoginPageProps {
  readonly isSubmitting: boolean
  readonly notice: AuthNotice | null
  readonly onSubmit: (email: string, password: string) => Promise<void>
}

export function LoginPage({ isSubmitting, notice, onSubmit }: LoginPageProps) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [validationError, setValidationError] = useState('')

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const trimmedEmail = email.trim()
    if (!trimmedEmail || !password) {
      setValidationError('请输入邮箱和密码。')
      return
    }
    setValidationError('')
    await onSubmit(trimmedEmail, password)
  }

  const displayedError = validationError || (notice?.tone === 'error' ? notice.message : '')

  return (
    <main className="v2-login-page">
      <section className="v2-login-intro" aria-label="产品介绍">
        <BrandMark />
        <p className="v2-eyebrow">TEAM KNOWLEDGE WORKSPACE</p>
        <h1>让团队知识<br /><span>清晰可用</span></h1>
        <p className="v2-login-intro-copy">统一管理知识空间、文档与 AI 问答，让每一次决策都基于可追溯的团队知识。</p>
        <div className="v2-login-benefits">
          <span><CheckCircle size={16} />真实知识资产</span>
          <span><ShieldCheck size={16} />租户与权限隔离</span>
          <span><LockKey size={16} />安全会话管理</span>
        </div>
      </section>

      <section className="v2-login-card" aria-labelledby="v2-login-title">
        <div className="v2-login-card-heading">
          <p className="v2-eyebrow">WELCOME BACK</p>
          <h2 id="v2-login-title">登录工作台</h2>
          <p>使用你的团队账号继续访问知识库。</p>
        </div>
        {notice?.tone === 'expired' ? (
          <div className="v2-login-notice v2-login-notice--expired" role="status">
            {notice.message}
          </div>
        ) : null}
        {displayedError ? (
          <div className="v2-login-notice v2-login-notice--error" role="alert">
            {displayedError}
          </div>
        ) : null}
        <form className="v2-login-form" onSubmit={handleSubmit} noValidate>
          <label htmlFor="v2-login-email">邮箱地址</label>
          <input
            id="v2-login-email"
            name="email"
            type="email"
            autoComplete="username"
            inputMode="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            aria-invalid={Boolean(validationError && !email.trim())}
            required
            disabled={isSubmitting}
          />
          <label htmlFor="v2-login-password">密码</label>
          <input
            id="v2-login-password"
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            aria-invalid={Boolean(validationError && !password)}
            required
            disabled={isSubmitting}
          />
          <button type="submit" className="v2-login-submit" disabled={isSubmitting}>
            {isSubmitting ? '正在验证…' : '登录'}
            <ArrowRight size={17} aria-hidden="true" />
          </button>
        </form>
        <p className="v2-login-security-note"><ShieldCheck size={14} />登录后会话仅保存在当前浏览器会话中。</p>
      </section>
    </main>
  )
}
