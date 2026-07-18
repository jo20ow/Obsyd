import Panel from './Panel'
import PanelTakeaway from './PanelTakeaway'
import useFetchWithError from '../hooks/useFetchWithError'
import { POLL_SLOW_MS } from '../utils/poll'

const API = '/api'

function fmtGw(mw) {
  if (mw == null) return '—'
  return mw >= 1000 ? `${(mw / 1000).toFixed(1)} GW` : `${Math.round(mw)} MW`
}

// "2026-08-21T14:00Z" → "Aug 21" (dates are UTC by construction)
function fmtEnd(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })
}

/**
 * Generation unavailability (ENTSO-E A77) — which plants are off, how much
 * capacity that is, and whether it was planned. No free EU product shows this
 * legibly; it is the desk's clearest edge. Only the highest revision per
 * message is shown and withdrawn messages are hidden (the backend enforces
 * that — most raw messages are withdrawn revisions).
 */
export default function OutagePanel({ zone = 'DE_LU' }) {
  const { data, loading, error } = useFetchWithError(`${API}/power/outages?zone=${zone}`, { deps: [zone], pollMs: POLL_SLOW_MS })

  if (error && !data) {
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">OUTAGES // FETCH ERROR</div>
      </div>
    )
  }

  if (!data?.available && !loading) {
    return (
      <div className="border border-border bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-neutral-500">
          OUTAGES — no unavailability messages for this zone yet.
        </div>
      </div>
    )
  }

  const outages = data?.outages ?? []
  const running = outages.filter((o) => o.running_now)
  const upcoming = outages.filter((o) => !o.running_now)
  const forced = data?.forced_offline_mw ?? 0

  return (
    <Panel
      id="power-outages"
      title={`GENERATION OUTAGES · ${data?.zone === 'DE_LU' ? 'DE-LU' : (data?.zone ?? zone)}`}
      freshness={data}
      info="ENTSO-E A77 unavailability of generation units. Counts the worst case of each message's availability curve; only the highest revision per message is shown, withdrawn messages are hidden. PLANNED = scheduled maintenance, FORCED = unplanned trip — forced capacity loss is what moves prices intraday. Descriptive, not a price call."
      collapsible
      headerRight={
        data?.total_offline_mw != null && (
          <span className="font-mono text-[10px] font-bold text-orange-400">
            {fmtGw(data.total_offline_mw)} offline
          </span>
        )
      }
    >
      {loading && !data ? (
        <div className="px-4 py-4 font-mono text-[10px] text-neutral-600 animate-pulse">Loading outages…</div>
      ) : (
        <>
          <div className="px-4 py-3 border-b border-border/30">
            <PanelTakeaway tone={forced > 1000 ? 'warn' : 'info'}>
              {`${fmtGw(data.total_offline_mw)} of generation is offline right now` +
                (forced > 0 ? ` — ${fmtGw(forced)} of it forced (unplanned).` : ', all of it planned maintenance.')}
              {upcoming.length > 0 ? ` ${upcoming.length} more outage${upcoming.length === 1 ? '' : 's'} start within 30 days.` : ''}
            </PanelTakeaway>
          </div>
          <div className="px-2 py-2 overflow-x-auto">
            <table className="w-full font-mono text-[11px]">
              <thead>
                <tr className="text-[9px] text-neutral-600 uppercase tracking-wider">
                  <th className="text-left px-2 py-1">Unit</th>
                  <th className="text-left px-2 py-1">Fuel</th>
                  <th className="text-right px-2 py-1">Offline</th>
                  <th className="text-left px-2 py-1">Kind</th>
                  <th className="text-left px-2 py-1">Until</th>
                </tr>
              </thead>
              <tbody>
                {running.slice(0, 12).map((o) => (
                  <tr key={o.mrid} className="border-t border-border/30">
                    {/* The name comes from the message where it has one, else from the A71/A33
                        unit registry via unit_eic — a key the outage table had been writing and
                        nothing had ever read. Falling back to the mRID means we know neither. */}
                    <td className="px-2 py-1.5 text-neutral-300 max-w-[180px] truncate"
                        title={o.unit_name ? `${o.unit_name}${o.unit_eic ? ` · ${o.unit_eic}` : ''}` : o.unit_eic || o.mrid}>
                      {o.unit_name || o.unit_eic || o.mrid}
                    </td>
                    <td className="px-2 py-1.5 text-neutral-500">{o.fuel || '—'}</td>
                    <td className="px-2 py-1.5 text-right font-bold text-neutral-200">{fmtGw(o.offline_mw)}</td>
                    <td className="px-2 py-1.5">
                      <span className={`text-[9px] tracking-wide border rounded px-1.5 py-0.5 ${
                        o.kind === 'forced'
                          ? 'text-orange-400 border-orange-500/30'
                          : 'text-neutral-500 border-border'
                      }`}>
                        {o.kind === 'forced' ? 'FORCED' : 'PLANNED'}
                      </span>
                    </td>
                    <td className="px-2 py-1.5 text-neutral-500">{fmtEnd(o.end_utc)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {running.length > 12 && (
              <div className="font-mono text-[9px] text-neutral-600 px-2 pt-1">
                + {running.length - 12} smaller outages running
              </div>
            )}
          </div>
        </>
      )}
    </Panel>
  )
}
