import { InfoPopover } from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'

const API = '/api'

// Turn a freshness key into a readable label.
function label(key) {
  if (key === 'power_flows') return 'Cross-border flows'
  if (key === 'gas_balance') return 'Gas balance'
  if (key === 'ttf') return 'TTF gas price'
  const [kind, zone] = key.split(':')
  const z = zone === 'DE_LU' ? 'DE-LU' : zone
  if (kind === 'power_dayahead') return `${z} · day-ahead`
  if (kind === 'power_grid') return `${z} · load/grid`
  return key
}

export default function CoveragePanel() {
  const { data, loading, error } = useFetchWithError(`${API}/v1/status`)
  const sources = data?.sources || []

  return (
    <div className="border border-border bg-surface rounded overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 border-b border-border">
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs text-neutral-500 tracking-wider">DATA COVERAGE</span>
          <InfoPopover text="Exactly what is fresh and what is stale right now, per zone and source — measured on the DATA's own delivery date, not the write timestamp. The transparency answer to a black-box feed: we show our gaps." />
        </div>
        <div className="flex items-center gap-2">
          {data && (
            <span className={`font-mono text-[10px] font-bold ${data.healthy ? 'text-green-400' : 'text-amber-400'}`}>
              {data.fresh_count}/{data.total} fresh
            </span>
          )}
          {/* Cross-panel link to the quality matrix below — same
              getElementById + scrollIntoView idiom as the Borders chip. */}
          <button
            onClick={() => document.getElementById('panel-data-quality')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
            className="font-mono text-[9px] tracking-wider border border-border rounded px-1.5 py-0.5 text-neutral-500 hover:text-cyan-glow hover:border-cyan-glow/40 transition-colors"
            title="Completeness, quality flags, restatements and arrival lag per zone × series — further down this tab"
          >
            DATA QUALITY ↓
          </button>
        </div>
      </div>

      {loading && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">Checking coverage…</div>
      )}
      {!loading && error && sources.length === 0 && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-red-400">Fetch error — retrying on next refresh.</div>
      )}
      {!loading && sources.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1 px-4 py-3">
          {sources.map((s) => (
            <div key={s.key} className="flex items-center justify-between gap-2 font-mono text-[10px]">
              <span className="flex items-center gap-2 min-w-0">
                <span className={`inline-block w-1.5 h-1.5 rounded-full ${s.fresh ? 'bg-green-400' : 'bg-red-400'}`} />
                <span className="text-neutral-400 truncate">{label(s.key)}</span>
              </span>
              <span className="text-neutral-600 shrink-0">{s.last_seen || '—'}</span>
            </div>
          ))}
        </div>
      )}
      <div className="px-4 py-2 border-t border-border font-mono text-[9px] text-neutral-700">
        Delivery-date freshness · green = within window, red = stale. Public at GET /api/v1/status.
      </div>
    </div>
  )
}
