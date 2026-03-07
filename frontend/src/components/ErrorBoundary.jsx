import { Component } from 'react'

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error(`ErrorBoundary [${this.props.name || 'unknown'}]:`, error, info)
  }

  render() {
    if (this.state.error) {
      return (
        <div className="border border-red-500/20 bg-red-500/5 rounded px-4 py-3">
          <div className="font-mono text-[10px] text-red-400 tracking-wider">
            {(this.props.name || 'COMPONENT').toUpperCase()} // RENDER ERROR
          </div>
          <div className="font-mono text-[9px] text-neutral-600 mt-1">
            {this.state.error?.message?.substring(0, 120)}
          </div>
          <button
            onClick={() => this.setState({ error: null })}
            className="font-mono text-[9px] text-cyan-glow mt-2 hover:underline"
          >
            RETRY
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
