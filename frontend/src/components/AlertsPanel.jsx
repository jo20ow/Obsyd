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

// Sort order: most urgent first. Unknown severities sink to the bottom.
const SEVERITY_RANK = { critical: 0, Extreme: 0, Severe: 0, warning: 1, Moderate: 1, info: 2, Minor: 2 }
const sevRank = (s) => SEVERITY_RANK[s] ?? 3

const RULE_ICONS = {
  // legacy oil/maritime
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
  // cross-vertical radar detectors
  gas_balance: 'GASBAL',
  days_of_supply: 'DOS',
  supply_demand_divergence: 'DIVRG',
  freight_divergence: 'FRGT',
  negative_prices: 'NEGP',
  sentiment_risk: 'SENT',
}

// Which dashboard tab holds the evidence chart for each vertical (drill-down).
const VERTICAL_TAB = { gas: 'gas', power: 'energy', metals: 'metals', sentiment: 'sentiment', oil: 'overview' }
const VERTICAL_LABELS = { gas: 'GAS', power: 'POWER', oil: 'OIL / MARITIME', metals: 'METALS', sentiment: 'SENTIMENT' }
const VERTICAL_ORDER = ['gas', 'power', 'oil', 'metals', 'sentiment']

const CONVERGENCE_STYLE = { dot: 'bg-amber-400', text: 'text-amber-400', border: 'border-amber-500/30' }

const RULE_GROUP_LABELS = {
  flow_anomaly: 'chokepoints with anomalous transit',
  chokepoint_anomaly: 'chokepoint traffic anomalies',
  anchored_vessels: 'anchored vessel alerts',
  floating_storage: 'floating storage alerts',
  weather: 'weather alerts',
  refinery_thermal: 'thermal alerts',
  negative_prices: 'zones with negative prices',
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

// Drill-down: jump to the vertical's evidence tab via the existing hash router.
function goToVertical(vertical) {
  const tab = VERTICAL_TAB[vertical] || 'overview'
  window.location.hash = tab
}

export default function AlertsPanel({ weatherAlerts = [] }) {
  const [alerts, setAlerts] = useState([])
  const [chokeAlerts, setChokeAlerts] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // Wait until both alert sources have responded (or failed) before
    // hiding the loading state — otherwise the panel briefly renders an
    // incomplete merge and re-shuffles a second later.
    Promise.allSettled([
      fetch(`${API}/alerts?limit=50`)
        .then((r) => (r.ok ? r.json() : []))
        .then(setAlerts)
        .catch((e) => console.error('AlertsPanel alerts:', e)),
      fetch(`${API}/alerts/portwatch`)
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (data?.alerts) setChokeAlerts(data.alerts)
        })
        .catch((e) => console.error('AlertsPanel portwatch:', e)),
    ]).finally(() => setLoading(false))
  }, [])

  // Merge signal alerts (carry their own `vertical`), weather + chokepoint
  // (oil/maritime). Everything without an explicit vertical defaults to "oil".
  const combined = useMemo(() => [
    ...chokeAlerts.map((c) => ({
      id: `choke-${c.portid}`,
      rule: 'chokepoint_anomaly',
      vertical: 'oil',
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
      vertical: 'oil',
      severity: w.severity,
      title: w.headline || w.event,
      detail: w.area,
      zone: '',
      created_at: w.onset || new Date().toISOString(),
      isWeather: true,
    })),
    ...alerts.map((a) => ({ ...a, vertical: a.vertical || 'oil', isWeather: false })),
  ], [alerts, chokeAlerts, weatherAlerts])

  const totalCount = combined.length

  // Group by vertical → within each vertical, severity-sort and collapse >2 of
  // the same rule into one expandable row.
  const verticalSections = useMemo(() => {
    const byVertical = {}
    for (const a of combined) {
      const v = a.vertical || 'oil'
      if (!byVertical[v]) byVertical[v] = []
      byVertical[v].push(a)
    }

    const sections = []
    for (const vertical of VERTICAL_ORDER) {
      const vAlerts = byVertical[vertical]
      if (!vAlerts || vAlerts.length === 0) continue

      // collapse >2 same-rule
      const groups = {}
      for (const a of vAlerts) {
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
          items.push({ type: 'group', rule, vertical, id: `group-${vertical}-${rule}`, severity: ruleAlerts[0].severity, summary, alerts: ruleAlerts })
        } else {
          items.push(...ruleAlerts.map((a) => ({ type: 'single', ...a })))
        }
      }
      items.sort((a, b) => sevRank(a.severity) - sevRank(b.severity))
      const topRank = Math.min(...items.map((i) => sevRank(i.severity)))
      sections.push({ vertical, items, count: vAlerts.length, topRank })
    }
    // Verticals with the most urgent anomaly bubble up.
    sections.sort((a, b) => a.topRank - b.topRank)
    return sections
  }, [combined])

  const [expandedGroups, setExpandedGroups] = useState(new Set())

  const toggleGroup = (id) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  function renderAlert(a) {
    const sev = a.rule === 'convergence' ? CONVERGENCE_STYLE : (SEVERITY_STYLES[a.severity] || SEVERITY_STYLES.info)
    const icon = RULE_ICONS[a.rule] || 'SIG'
    const isWx = a.isWeather
    return (
      <button
        key={a.id}
        type="button"
        onClick={() => goToVertical(a.vertical)}
        title="Open evidence"
        className={`w-full text-left px-4 py-3 border-b border-border last:border-b-0 hover:bg-white/[0.02] transition-colors ${sev.border}`}
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
      </button>
    )
  }

  if (loading) {
    return (
      <div className="border border-border bg-surface rounded">
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-border">
          <span className="font-mono text-xs text-neutral-500">ANOMALY RADAR</span>
          <span className="font-mono text-[10px] text-neutral-600 animate-pulse">LOADING ...</span>
        </div>
        <div className="px-4 py-4 space-y-3 animate-pulse">
          {[0, 1, 2].map((i) => (
            <div key={i} className="flex items-start gap-2.5">
              <div className="h-5 w-12 bg-neutral-800 rounded" />
              <div className="flex-1 space-y-1.5">
                <div className="h-3 bg-neutral-800 rounded" style={{ width: `${85 - i * 10}%` }} />
                <div className="h-2.5 bg-neutral-800/60 rounded" style={{ width: `${60 - i * 5}%` }} />
              </div>
            </div>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="border border-border bg-surface rounded">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border">
        <span className="font-mono text-xs text-neutral-500">
          ANOMALY RADAR
        </span>
        <span className="font-mono text-[10px] text-neutral-600">
          {totalCount} active
        </span>
      </div>

      <div className="max-h-[300px] md:max-h-[400px] overflow-y-auto scrollbar-hidden">
        {verticalSections.length === 0 ? (
          <div className="px-4 py-6 text-center font-mono text-xs text-neutral-600">
            Nothing abnormal right now
          </div>
        ) : (
          verticalSections.map((section) => (
            <div key={section.vertical}>
              <div className="flex items-center justify-between px-4 py-1.5 bg-white/[0.02] border-b border-border">
                <span className="font-mono text-[10px] tracking-wider text-neutral-500">
                  {VERTICAL_LABELS[section.vertical] || section.vertical.toUpperCase()}
                </span>
                <span className="font-mono text-[9px] text-neutral-600">{section.count}</span>
              </div>
              {section.items.map((item) => {
                if (item.type === 'group') {
                  const sev = SEVERITY_STYLES[item.severity] || SEVERITY_STYLES.info
                  const icon = RULE_ICONS[item.rule] || 'SIG'
                  const isExpanded = expandedGroups.has(item.id)
                  return (
                    <div key={item.id}>
                      <button
                        onClick={() => toggleGroup(item.id)}
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
              })}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
