import useFetchWithError from '../hooks/useFetchWithError'
import AnomalyContextLine from './AnomalyContextLine'

// The niche-defining glance: the whole physical energy system in one strip —
// oil molecules (chokepoints), gas balance, and power electrons — each a
// descriptive deviation vs its own history, collapsed to one overall state.
const API = '/api'

const STATE_UI = {
  CALM: { dot: 'bg-emerald-400', text: 'text-emerald-400' },
  ELEVATED: { dot: 'bg-amber-400', text: 'text-amber-400' },
  STRESSED: { dot: 'bg-red-400', text: 'text-red-400' },
}

const ORDER = ['oil', 'gas', 'power']

export default function PhysicalSituationBar({ onNavigate }) {
  const { data } = useFetchWithError(`${API}/situation`)

  // Stay quiet until there's something real to show (no empty shell on cold start).
  if (!data || !data.available) return null

  const overall = data.overall || 'CALM'
  const ov = STATE_UI[overall] || STATE_UI.CALM

  return (
    <div className="border border-border bg-surface rounded overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-border/60">
        <span className={`w-2 h-2 rounded-sm shrink-0 ${ov.dot}`} />
        <span className="font-mono text-[10px] tracking-wider text-neutral-500">PHYSICAL ENERGY SYSTEM</span>
        <span className={`font-mono text-[10px] font-bold tracking-wider ml-auto ${ov.text}`}>{overall}</span>
      </div>

      {/* Plain-language answer — the anchor. What's happening, in one sentence. */}
      <div className="px-3 py-2 border-b border-border/60">
        <span className="font-mono text-[13px] leading-snug text-neutral-200">
          {(() => {
            const notable = ORDER.filter((k) => data.domains?.[k]?.available && data.domains[k].state !== 'CALM')
            const lead = overall === 'STRESSED'
              ? 'Europe’s physical energy system is under stress'
              : overall === 'ELEVATED'
                ? 'Europe’s physical energy system is somewhat elevated'
                : 'Europe’s physical energy system is calm'
            if (!notable.length) return `${lead} — nothing unusual right now.`
            const reasons = notable.map((k) => `${data.domains[k].label.toLowerCase()} ${data.domains[k].state.toLowerCase()}`)
            return `${lead} — ${reasons.join(', ')}.`
          })()}
        </span>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-3 divide-y sm:divide-y-0 sm:divide-x divide-border/60">
        {ORDER.map((k) => {
          const d = data.domains?.[k]
          if (!d) return null
          const ui = STATE_UI[d.state] || STATE_UI.CALM
          return (
            <button
              key={k}
              type="button"
              onClick={() => d.available && onNavigate?.(d.tab)}
              className="text-left px-3 py-2 hover:bg-white/[0.02] transition-colors disabled:opacity-60"
              disabled={!d.available}
            >
              <div className="flex items-center gap-1.5">
                <span className={`w-1.5 h-1.5 rounded-sm shrink-0 ${d.available ? ui.dot : 'bg-neutral-700'}`} />
                <span className="font-mono text-[10px] tracking-wider text-neutral-300">
                  {(d.label || k).toUpperCase()}
                </span>
                {d.available && (
                  <span className={`font-mono text-[9px] ml-auto ${ui.text}`}>{d.state}</span>
                )}
                {d.stale && (
                  <span className="font-mono text-[8px] text-amber-500/80 border border-amber-500/30 rounded px-1 ml-1">
                    STALE
                  </span>
                )}
              </div>
              <div className="font-mono text-[11px] text-neutral-400 mt-1 leading-snug">
                {d.available ? d.headline : 'No data yet'}
              </div>
              {d.forward?.residual_mw != null && (
                <div className="font-mono text-[10px] text-violet-300/90 mt-0.5">
                  &rarr; D+1 residual {(d.forward.residual_mw / 1000).toFixed(1)} GW
                </div>
              )}
            </button>
          )
        })}
      </div>
      {ORDER.filter((k) => data.domains?.[k]?.context).map((k) => (
        <AnomalyContextLine
          key={k}
          context={data.domains[k].context}
          className="px-3 py-1.5 border-t border-border/60"
        />
      ))}

      <div className="px-3 py-1 font-mono text-[8px] text-neutral-700 border-t border-border/40">
        Deviation vs each domain&apos;s own history — descriptive, not a forecast.
      </div>
    </div>
  )
}
