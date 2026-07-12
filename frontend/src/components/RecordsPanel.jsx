import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'

const API = '/api'

function zoneLabel(zone) {
  return zone === 'DE_LU' ? 'DE-LU' : zone
}

const SERIES_LABELS = {
  'price.dayahead': 'Day-ahead hour',
  'price.dayahead.qh': 'Day-ahead quarter-hour',
  'imbalance.price.qh': 'Imbalance quarter-hour',
  'load.actual': 'Load hour',
  'residual.actual': 'Residual-load hour',
}

/**
 * All-time records per series for the selected zone — the archive behind the
 * ephemeral RecordChip (which only surfaces records set in the last 7 days).
 * Recomputed nightly by SQL min/max over the canonical store; "all-time" means
 * within our coverage. Descriptive archive facts, not signals.
 */
export default function RecordsPanel({ zone = 'DE_LU' }) {
  const url = `${API}/power/records?zone=${zone}`
  const { data, loading, error } = useFetchWithError(url, { deps: [zone] })

  const zl = zoneLabel(zone)

  if (error) {
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">RECORDS // FETCH ERROR</div>
      </div>
    )
  }

  if (!data?.available && !loading) {
    return (
      <div className="border border-border bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-neutral-500">
          RECORDS · {zl} — {data?.reason || 'no records computed yet.'}
        </div>
      </div>
    )
  }

  const records = data?.records ?? []

  return (
    <Panel
      id="power-records"
      title={`ALL-TIME RECORDS · ${zl}`}
      info="All-time extremes per series for this zone — highest/lowest day-ahead hour, quarter-hour, load and residual load — recomputed nightly from the canonical hourly store. 'All-time' means within our coverage (deep history varies by zone). FRESH marks records set in the last 7 days. Descriptive archive facts."
      collapsible
    >
      {loading && !data ? (
        <div className="px-4 py-4 font-mono text-[10px] text-neutral-600 animate-pulse">
          Loading records…
        </div>
      ) : (
        <div className="px-2 py-2 overflow-x-auto">
          <table className="w-full font-mono text-[11px]">
            <thead>
              <tr className="text-[9px] text-neutral-600 uppercase tracking-wider">
                <th className="text-left px-2 py-1">Series</th>
                <th className="text-left px-2 py-1">Kind</th>
                <th className="text-right px-2 py-1">Value</th>
                <th className="text-right px-2 py-1">Date</th>
              </tr>
            </thead>
            <tbody>
              {records.map((r) => (
                <tr key={`${r.series}-${r.kind}`} className="border-t border-border/30">
                  <td className="px-2 py-1.5 text-neutral-300">
                    {SERIES_LABELS[r.series] || r.series}
                  </td>
                  <td className={`px-2 py-1.5 ${r.kind === 'max' ? 'text-orange-400' : 'text-cyan-glow'}`}>
                    {r.kind === 'max' ? 'HIGHEST' : 'LOWEST'}
                  </td>
                  <td className="px-2 py-1.5 text-right text-neutral-200 font-bold">
                    {r.value.toLocaleString('en-US')}{' '}
                    <span className="text-[9px] text-neutral-600 font-normal">{r.unit}</span>
                  </td>
                  <td className="px-2 py-1.5 text-right text-neutral-500">
                    {r.date}
                    {r.fresh && (
                      <span className="ml-1.5 font-mono text-[8px] tracking-wide border border-amber-500/40 text-amber-400 rounded px-1 py-0.5">
                        FRESH
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="px-2 pt-2 font-mono text-[9px] text-neutral-700">
            recomputed nightly · all-time within coverage
          </div>
        </div>
      )}
    </Panel>
  )
}
