import { Component, type ErrorInfo, type ReactNode } from 'react'

interface ErrorBoundaryProps {
  children: ReactNode
}

interface ErrorBoundaryState {
  hasError: boolean
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false }

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('EKB UI error', { message: error.message, componentStack: info.componentStack })
  }

  render(): ReactNode {
    if (!this.state.hasError) return this.props.children
    return (
      <main className="fatal-state">
        <p className="eyebrow">EKB</p>
        <h1>页面暂时无法显示</h1>
        <p>请刷新后重试；如果问题持续，请把请求编号交给运维人员。</p>
        <button type="button" onClick={() => window.location.reload()}>
          重新加载
        </button>
      </main>
    )
  }
}
