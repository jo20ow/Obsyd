import { useState, useEffect, useMemo } from 'react'

const API = '/api'

const SEVERITY_STYLES = {
  critical: { dot: 'bg-red-400', text: 'text-red-400', border: 'border-red-500/30' },
  Extreme: { dot: 'bg-red-400', text: 'text-red-400', border: 'border-red-500/30' },
  Severe: { dot: 'bg-red-400', text: 'text-red-400', border: 'border-red-500/30' },
  warning: { dot: 'bg-yellow-400', text: 'text-yellow-400', border: 'border-yellow-500/30' },
  Moderate: { dot: 'bg-yellow-400', text: 'text-yellow-400', border: 'border-yellow-500/30' },
  info: { dot: 'bg-green-glow', text: 'text-green-glow', border: 'border-green-500/30' },
  Minor: { dot: 'bg-green-glow', text: 'text-green-glow', border: 'border-green-500/30' },
}

const RULE_ICONS = {
  anchored_vessels: 'ANCHR',
  floating_storage: 'ANCHR',
  flow_anomaly: 'FLOW',
  cushing_drawdown: 'CUSH',
  refinery_thermal: 'THERM',
  chokepoint_anomaly: 'CHOKE',
  crack_spread_high: 'CRACK',
  crack_spread_low: 'CRACK',
  rerouting_high: 'ROUTE',
  convergence: 'CONV',
  weather: 'WX',
}

const CONVERGENCE_STYLE = { dot: 'bg-amber-400', text: 'text-amber-400', border: 'border-amber-500/30' }

const RULE_GROUP_LABELS = {
  flow_anomaly: 'chokepoints with anomalous transit',
  chokepoint_anomaly: 'chokepoint traffic anomalies',
  anchored_vessels: 'anchored vessel alerts',
  floating_storage: 'floating storage alerts',
  weather: 'weather alerts',
  refinery_thermal: 'thermal alerts',
}

function timeAgo(isoStr) {
  if (!isoStr) return ''
  try {
    const diff = Date.now() - new Date(isoStr).getTime()
    if (isNaN(diff)) return ''
    const mins = Math.floor(diff / 60000)
    if (mins < 60) return `${mins}m ago`
    const hrs = Math.floor(mins / 60)
    if (hrs < 24) return `${hrs}h ago`
    return `${Math.floor(hrs / 24)}d ago`
  } catch { return '' }
}

export default function AlertsPanel({ weatherAlerts = [] }) {
  const [alerts, setAlerts] = useState([])
  const [chokeAlerts, setChokeAlerts] = useState([])

  useEffect(() => {
    fetch(`${API}/alerts?limit=20`)
      .then((r) => (r.ok ? r.json() : []))
      .then(setAlerts)
      .catch((e) => console.error('AlertsPanel alerts:', e))

    fetch(`${API}/alerts/portwatch`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data?.alerts) setChokeAlerts(data.alerts)
      })
      .catch((e) => console.error('AlertsPanel portwatch:', e))
  }, [])

  // Merge signal alerts, weather alerts, and chokepoint alerts
  const combined = [
    ...chokeAlerts.map((c) => ({
      id: `choke-${c.portid}`,
      rule: 'chokepoint_anomaly',
      severity: c.alert_level,
      title: `${c.chokepoint || '?'}: ${c.anomaly_pct > 0 ? '+' : ''}${c.anomaly_pct ?? 0}% ${c.direction || ''}`,
      detail: `${c.n_total ?? '?'} vessels (${c.baseline_type === 'yoy' ? 'YoY' : '30d'}: ${c.baseline_avg ?? '?'})${c.disruption_name ? ` // ${c.disruption_name}` : ''}`,
      zone: (c.chokepoint || '').toLowerCase().split(' ').pop() || '',
      created_at: `${c.date}T00:00:00Z`,
      isChoke: true,
    })),
    ...weatherAlerts.map((w) => ({
      id: `wx-${w.id}`,
      rule: 'weather',
      severity: w.severity,
      title: w.headline || w.event,
      detail: w.area,
      zone: '',
      created_at: w.onset || new Date().toISOString(),
      isWeather: true,
    })),
    ...alerts.map((a) => ({ ...a, isWeather: false })),
  ]

  const totalCount = combined.length

  // Group alerts by rule — collapse >2 of the same type into one expandable row
  const displayItems = useMemo(() => {
    const groups = {}
    for (const a of combined) {
      if (!groups[a.rule]) groups[a.rule] = []
      groups[a.rule].push(a)
    }
    const items = []
    for (const [rule, ruleAlerts] of Object.entries(groups)) {
      if (ruleAlerts.length > 2) {
        const zones = ruleAlerts.map((a) => (a.zone || '').toUpperCase()).filter(Boolean)
        const label = RULE_GROUP_LABELS[rule] || rule
        const summary = zones.length > 0
          ? `${ruleAlerts.length} ${label}: ${zones.join(', ')}`
          : `${ruleAlerts.length} ${label}`
        items.push({ type: 'group', rule, id: `group-${rule}`, severity: ruleAlerts[0].severity, summary, alerts: ruleAlerts })
      } else {
        items.push(...ruleAlerts.map((a) => ({ type: 'single', ...a })))
      }
    }
    return items
  }, [combined])

  const [expandedGroups, setExpandedGroups] = useState(new Set())

  const toggleGroup = (rule) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev)
      next.has(rule) ? next.delete(rule) : next.add(rule)
      return next
    })
  }

  function renderAlert(a) {
    const sev = a.rule === 'convergence' ? CONVERGENCE_STYLE : (SEVERITY_STYLES[a.severity] || SEVERITY_STYLES.info)
    const icon = RULE_ICONS[a.rule] || 'SIG'
    const isWx = a.isWeather
    return (
      <div
        key={a.id}
        className={`px-4 py-3 border-b border-border last:border-b-0 ${sev.border}`}
      >
        <div className="flex items-start gap-2.5">
          <div className={`font-mono text-[10px] font-bold mt-0.5 px-1.5 py-0.5 border rounded ${a.isChoke ? 'text-cyan-glow border-cyan-glow/30' : (isWx || a.rule === 'refinery_thermal') ? 'text-orange-400 border-orange-500/30' : `${sev.text} ${sev.border}`}`}>
            {icon}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center justify-between gap-2">
              <span className="font-mono text-xs text-neutral-300 truncate">
                {a.title}
              </span>
              <div className="flex items-center gap-1.5 shrink-0">
                <span className={`w-1.5 h-1.5 rounded-full ${a.isChoke ? 'bg-cyan-glow' : isWx ? 'bg-orange-400' : sev.dot}`} />
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
  }

  return (
    <div className="border border-border bg-surface rounded">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border">
        <span className="font-mono text-xs text-neutral-500">
          SIGNAL ALERTS
        </span>
        <span className="font-mono text-[10px] text-neutral-600">
          {totalCount} active
        </span>
      </div>

      <div className="max-h-[300px] md:max-h-[400px] overflow-y-auto scrollbar-hidden">
        {displayItems.length === 0 ? (
          <div className="px-4 py-6 text-center font-mono text-xs text-neutral-600">
            No alerts generated yet
          </div>
        ) : (
          displayItems.map((item) => {
            if (item.type === 'group') {
              const sev = SEVERITY_STYLES[item.severity] || SEVERITY_STYLES.info
              const icon = RULE_ICONS[item.rule] || 'SIG'
              const isExpanded = expandedGroups.has(item.rule)
              return (
                <div key={item.id}>
                  <button
                    onClick={() => toggleGroup(item.rule)}
                    className={`w-full text-left px-4 py-3 border-b border-border ${sev.border} hover:bg-white/[0.02] transition-colors`}
                  >
                    <div className="flex items-start gap-2.5">
                      <div className={`font-mono text-[10px] font-bold mt-0.5 px-1.5 py-0.5 border rounded ${sev.text} ${sev.border}`}>
                        {icon}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-mono text-xs text-neutral-300">
                            {item.summary}
                          </span>
                          <span className="font-mono text-[10px] text-neutral-600 shrink-0">
                            {isExpanded ? '▾' : '▸'} {item.alerts.length}
                          </span>
                        </div>
                      </div>
                    </div>
                  </button>
                  {isExpanded && item.alerts.map(renderAlert)}
                </div>
              )
            }
            return renderAlert(item)
          })
        )}
      </div>
    </div>
  )
}
