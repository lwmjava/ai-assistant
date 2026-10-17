import { Component } from 'react'
import type { ReactNode } from 'react'

/** 最后一道页面错误兜底，不把组件异常或堆栈呈现给用户。 */
export class RouteErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() {
    if (this.state.failed) return <div role="alert" className="grid min-h-screen place-items-center p-6"><div className="space-y-4 text-center"><p>页面暂时无法显示，请刷新后重试</p><button className="text-primary" onClick={() => window.location.reload()}>刷新页面</button></div></div>
    return this.props.children
  }
}
