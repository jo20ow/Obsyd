import { useState, useEffect } from 'react'

const API = '/api'

const SEVERITY_STYLES = {
  critical: { dot: 'bg-red-400', text: 'text-red-400', border: 'border-red-500/30' },
  warning: { dot: 'bg-yellow-400', text: 'text-yellow-400', border: 'border-yellow-500/30' },
  info: { dot: 'bg-green-glow', text: 'text-green-glow', border: 'border-green-500/30' },
}

const RULE_ICONS = {
  floating_storage: 'STOR',
  flow_anomaly: 'FLOW',
  cushing_drawdown: 'CUSH',
}

function timeAgo(isoStr) {
  const diff = Date.now() - new Date(isoStr).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

export default function AlertsPanel() {
  const [alerts, setAlerts] = useState([])

  useEffect(() => {
    fetch(`${API}/alerts?limit=20`)
      .then((r) => (r.ok ? r.json() : []))
      .then(setAlerts)
      .catch(() => {})
  }, [])

  return (
    <div className="border border-border bg-surface rounded">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border">
        <span className="font-mono text-xs text-neutral-500">
          SIGNAL ALERTS
        </span>
        <span className="font-mono text-[10px] text-neutral-600">
          {alerts.length} active
        </span>
      </div>

      <div className="max-h-[400px] overflow-y-auto">
        {alerts.length === 0 ? (
          <div className="px-4 py-6 text-center font-mono text-xs text-neutral-600">
            No alerts generated yet
          </div>
        ) : (
          alerts.map((a) => {
            const sev = SEVERITY_STYLES[a.severity] || SEVERITY_STYLES.info
            const icon = RULE_ICONS[a.rule] || 'SIG'
            return (
              <div
                key={a.id}
                className={`px-4 py-3 border-b border-border last:border-b-0 ${sev.border}`}
              >
                <div className="flex items-start gap-2.5">
                  <div className={`font-mono text-[10px] font-bold mt-0.5 px-1.5 py-0.5 border rounded ${sev.text} ${sev.border}`}>
                    {icon}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-xs text-neutral-300 truncate">
                        {a.title}
                      </span>
                      <div className="flex items-center gap-1.5 shrink-0">
                        <span className={`w-1.5 h-1.5 rounded-full ${sev.dot}`} />
                        <span className="font-mono text-[10px] text-neutral-600">
                          {timeAgo(a.created_at)}
                        </span>
                      </div>
                    </div>
                    <div className="font-mono text-[10px] text-neutral-500 mt-0.5 leading-relaxed">
                      {a.detail}
                    </div>
                    {a.zone && (
                      <span className="inline-block font-mono text-[9px] text-cyan-glow mt-1 px-1 border border-cyan-glow/20 rounded">
                        {a.zone.toUpperCase()}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
