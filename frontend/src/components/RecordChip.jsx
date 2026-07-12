import useFetchWithError from '../hooks/useFetchWithError'
import { POLL_SLOW_MS } from '../utils/poll'

const API = '/api'

const SERIES_LABEL = {
  'price.dayahead': 'day-ahead hour',
  'price.dayahead.qh': 'day-ahead 15-min slot',
  'imbalance.price.qh': 'imbalance settlement',
  'load.actual': 'load',
  'residual.actual': 'residual load',
}

function fmt(r) {
  const what = SERIES_LABEL[r.series] || r.series
  const dir = r.kind === 'max' ? 'highest' : 'lowest'
  const val = r.unit === 'EUR/MWh' ? `€${r.value.toFixed(0)}/MWh` : `${(r.value / 1000).toFixed(1)} GW`
  return `${dir} ${what} on record: ${val} (${r.date})`
}

/**
 * Renders ONLY when a record fell within the last 7 days — a fresh all-time
 * extreme is the story of the week; archive records live in the API. Not
 * rendering is the normal state, not a data gap.
 */
export default function RecordChip({ zone = 'DE_LU' }) {
  const { data, error } = useFetchWithError(`${API}/power/records?zone=${zone}`, { deps: [zone], pollMs: POLL_SLOW_MS })
  const fresh = (data?.records ?? []).filter((r) => r.fresh)
  // Ephemeral strip: empty is the documented normal state and stays silent —
  // but a FETCH error must not masquerade as "nothing to report".
  if (error)
    return (
      <div className="font-mono text-[9px] text-red-400 px-1 py-0.5">records // fetch error</div>
    )
  if (fresh.length === 0) return null

  return (
    <div className="border border-yellow-500/30 bg-yellow-500/5 rounded px-3 py-2 flex flex-wrap items-center gap-x-3 gap-y-1">
      <span className="font-mono text-[9px] font-bold tracking-widest text-yellow-400">⚡ NEW RECORD</span>
      {fresh.slice(0, 2).map((r) => (
        <span key={`${r.series}-${r.kind}`} className="font-mono text-[10px] text-neutral-300">
          {fmt(r)}
        </span>
      ))}
    </div>
  )
}
