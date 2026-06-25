import { useState, useEffect, useMemo } from 'react'

const API = '/api'

// Alert rules that are about physical SUPPLY (the wedge), filtered out of the full radar.
const SUPPLY_RULES = new Set([
  'chokepoint_anomaly', 'rerouting_high', 'floating_storage', 'flow_anomaly', 'convergence',
  'days_of_supply', 'supply_demand_divergence', 'freight_divergence', 'gas_balance',
  'dunkelflaute', 'negative_prices',
])

const SEV = {
  critical: 'text-red-400 border-red-500/40',
  warning: 'text-yellow-400 border-yellow-500/40',
  info: 'text-cyan-glow border-cyan-glow/30',
}

function concLevel(hhi) {
  if (hhi >= 0.5) return { label: 'EXTREME', cls: 'text-red-400', bar: '#f87171' }
  if (hhi >= 0.25) return { label: 'HIGH', cls: 'text-orange-400', bar: '#fb923c' }
  if (hhi >= 0.15) return { label: 'MODERATE', cls: 'text-yellow-400', bar: '#facc15' }
  return { label: 'DIVERSIFIED', cls: 'text-green-glow', bar: '#34d399' }
}

function MaterialCard({ m }) {
  const lvl = concLevel(m.hhi)
  return (
    <button
      type="button"
      onClick={() => { window.location.hash = 'atlas' }}
      className="text-left border border-border bg-surface rounded p-3 hover:border-cyan-glow/40 transition-colors"
      title="Open on the world map"
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-mono text-xs text-neutral-300 tracking-wider">{m.label}</span>
        <span className={`font-mono text-[9px] px-1.5 py-0.5 border rounded ${lvl.cls} border-current/30`}>{lvl.label}</span>
      </div>
      <div className="flex items-baseline gap-1.5 mt-1.5">
        <span className="font-mono text-2xl font-bold text-cyan-glow">{Math.round(m.top_share * 100)}%</span>
        <span className="font-mono text-[10px] text-neutral-500">{m.top_country_name?.slice(0, 18) || m.top_country}</span>
      </div>
      <div className="font-mono text-[9px] text-neutral-600 mb-1.5">top producer · {m.producers} sources · as of {m.as_of}</div>
      <div className="space-y-1">
        {m.top3.map((c) => (
          <div key={c.iso3} className="flex items-center gap-1.5">
            <span className="font-mono text-[9px] text-neutral-500 w-7 shrink-0">{c.iso3}</span>
            <div className="flex-1 h-1.5 bg-neutral-800 rounded-full overflow-hidden">
              <div className="h-1.5 rounded-full" style={{ width: `${c.share * 100}%`, background: lvl.bar }} />
            </div>
            <span className="font-mono text-[9px] text-neutral-500 w-8 text-right shrink-0">{Math.round(c.share * 100)}%</span>
          </div>
        ))}
      </div>
    </button>
  )
}

export default function CriticalMaterialsView() {
  const [crit, setCrit] = useState(null)
  const [alerts, setAlerts] = useState([])

  useEffect(() => {
    fetch(`${API}/atlas/criticality`).then((r) => (r.ok ? r.json() : null)).then(setCrit).catch((e) => console.error('criticality:', e))
    fetch(`${API}/alerts?limit=80`).then((r) => (r.ok ? r.json() : [])).then(setAlerts).catch((e) => console.error('alerts:', e))
  }, [])

  const disruptions = useMemo(() => {
    const rank = { critical: 0, warning: 1, info: 2 }
    return (alerts || [])
      .filter((a) => SUPPLY_RULES.has(a.rule))
      .sort((a, b) => (rank[a.severity] ?? 9) - (rank[b.severity] ?? 9))
  }, [alerts])

  return (
    <div className="space-y-4">
      {/* Product header / value proposition */}
      <div className="border border-border bg-surface rounded px-4 py-3">
        <div className="font-mono text-sm text-neutral-200 tracking-wide">CRITICAL MATERIALS &amp; ENERGY SECURITY</div>
        <div className="font-mono text-[11px] text-neutral-500 mt-1 leading-relaxed">
          Who controls the world&apos;s strategic resources — and where that supply is disrupted right now.
          Official, public-domain data (USGS · EIA · ENTSO-E). Descriptive, no black box.
        </div>
      </div>

      {/* Pillar 1 — supply concentration */}
      <div>
        <div className="font-mono text-[10px] text-neutral-500 tracking-wider mb-2 px-1">SUPPLY CONCENTRATION · who controls production</div>
        {!crit ? (
          <div className="font-mono text-[10px] text-neutral-600 px-1 animate-pulse">loading…</div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {crit.materials.map((m) => <MaterialCard key={m.key} m={m} />)}
          </div>
        )}
        {crit && <div className="font-mono text-[9px] text-neutral-700 mt-2 px-1">Source: {crit.source}. Sorted by concentration (most strategically fragile first).</div>}
      </div>

      {/* Pillar 2 — live supply disruptions */}
      <div>
        <div className="font-mono text-[10px] text-neutral-500 tracking-wider mb-2 px-1">SUPPLY DISRUPTIONS · what&apos;s abnormal now</div>
        <div className="border border-border bg-surface rounded divide-y divide-border">
          {disruptions.length === 0 ? (
            <div className="px-4 py-4 font-mono text-[10px] text-neutral-600 text-center">No supply disruptions flagged right now.</div>
          ) : (
            disruptions.map((a) => (
              <div key={a.id} className="px-4 py-2.5 flex items-start gap-2.5">
                <span className={`font-mono text-[9px] mt-0.5 px-1.5 py-0.5 border rounded shrink-0 ${SEV[a.severity] || SEV.info}`}>{a.severity.toUpperCase().slice(0, 4)}</span>
                <div className="min-w-0">
                  <div className="font-mono text-[11px] text-neutral-300 truncate">{a.title}</div>
                  <div className="font-mono text-[9px] text-neutral-600 leading-relaxed">{a.detail}</div>
                </div>
              </div>
            ))
          )}
        </div>
        <div className="font-mono text-[9px] text-neutral-700 mt-2 px-1">
          Physical supply anomalies from the radar (chokepoints, rerouting, floating storage, gas/power balance). Descriptive — deviation vs history, not a forecast.
        </div>
      </div>
    </div>
  )
}
