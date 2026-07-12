import useFetchWithError from '../hooks/useFetchWithError'

const API = '/api'

function timeAgo(iso) {
  try {
    const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
    if (s < 3600) return `${Math.round(s / 60)}m ago`
    if (s < 86400) return `${Math.round(s / 3600)}h ago`
    return `${Math.round(s / 86400)}d ago`
  } catch { return '' }
}

// gridstatus-style "insights" strip: the latest anomaly-radar items as compact,
// horizontally-scrolling cards, with a "More →" into the full ALERTS radar.
export default function InsightsStrip({ onMore }) {
  const { data, error } = useFetchWithError(`${API}/alerts?limit=12`)
  const items = (Array.isArray(data) ? data : []).slice(0, 8)
  // Ephemeral strip: empty is the documented normal state and stays silent —
  // but a FETCH error must not masquerade as "nothing to report".
  if (error)
    return (
      <div className="font-mono text-[9px] text-red-400 px-1 py-0.5">insights // fetch error</div>
    )
  if (items.length === 0) return null
  return (
    <div className="border border-border bg-surface rounded overflow-hidden shadow-sm">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border/50">
        <span className="font-mono text-[12px] font-semibold text-neutral-300">Anomaly radar</span>
        <button onClick={onMore} className="font-mono text-[10px] text-neutral-500 hover:text-cyan-glow transition-colors">
          More →
        </button>
      </div>
      <div className="flex gap-2 overflow-x-auto scrollbar-hidden p-3">
        {items.map((a) => (
          <div key={a.id} className="shrink-0 w-56 border border-border rounded p-2.5 bg-surface-light">
            <div className="font-mono text-[9px] text-neutral-600">
              {timeAgo(a.created_at)}{a.zone ? ` · ${String(a.zone).toUpperCase()}` : (a.vertical ? ` · ${a.vertical}` : '')}
            </div>
            <div className="font-mono text-[11px] text-neutral-300 mt-1 leading-snug line-clamp-2">{a.title}</div>
            {a.detail && <div className="font-mono text-[9px] text-neutral-500 mt-1 line-clamp-2">{a.detail}</div>}
          </div>
        ))}
      </div>
    </div>
  )
}
