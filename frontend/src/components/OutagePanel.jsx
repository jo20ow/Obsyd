import { useState } from 'react'
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

function ToggleBtn({ id, label, view, setView }) {
  return (
    <button
      type="button"
      onClick={() => setView(id)}
      className={`font-mono text-[9px] tracking-wider px-2 py-0.5 rounded border transition-colors ${
        view === id ? 'text-cyan-glow border-cyan-glow/50 bg-cyan-glow/5' : 'text-neutral-600 border-border hover:text-neutral-400'
      }`}
    >
      {label}
    </button>
  )
}

/**
 * Generation unavailability (ENTSO-E A77) — which plants are off, how much
 * capacity that is, and whether it was planned. No free EU product shows this
 * legibly; it is the desk's clearest edge. Only the highest revision per
 * message is shown and withdrawn messages are hidden (the backend enforces
 * that — most raw messages are withdrawn revisions).
 *
 * GENERATION | TRANSMISSION toggle (Task P12): the SAME endpoint (`kind=all` by
 * default) already returns both sections in one response, so switching views is a
 * local re-render, not a re-fetch. Transmission = ENTSO-E A78 interconnector/line
 * unavailability — a legally clean substitute for the biggest unique slice of Nord
 * Pool's UMM feed (39% of volume), which cannot be re-displayed here without Nord
 * Pool's written consent (docs/findings/2026-07-20-umm-feasibility.md).
 */
export default function OutagePanel({ zone = 'DE_LU' }) {
  const { data, loading, error } = useFetchWithError(`${API}/power/outages?zone=${zone}`, { deps: [zone], pollMs: POLL_SLOW_MS })
  const [view, setView] = useState('generation') // 'generation' | 'transmission'

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

  const transmission = data?.transmission ?? []
  const transRunning = transmission.filter((o) => o.running_now)
  const transUpcoming = transmission.filter((o) => !o.running_now)

  const zoneLabel = data?.zone === 'DE_LU' ? 'DE-LU' : (data?.zone ?? zone)

  return (
    <Panel
      id="power-outages"
      title={`${view === 'transmission' ? 'TRANSMISSION' : 'GENERATION'} OUTAGES · ${zoneLabel}`}
      freshness={data}
      info="ENTSO-E A77 unavailability of generation units + A78 unavailability of transmission infrastructure (interconnectors/lines). Counts the worst case of each message's availability curve; only the highest revision per message is shown, withdrawn messages are hidden. PLANNED = scheduled maintenance, FORCED = unplanned trip — forced capacity loss is what moves prices intraday. Descriptive, not a price call."
      collapsible
      headerRight={
        view === 'transmission'
          ? transRunning.length > 0 && (
              <span className="font-mono text-[10px] font-bold text-orange-400">
                {transRunning.length} running
              </span>
            )
          : data?.total_offline_mw != null && (
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
          <div className="flex items-center gap-1.5 px-4 py-2 border-b border-border/30">
            <ToggleBtn id="generation" label="GENERATION" view={view} setView={setView} />
            <ToggleBtn id="transmission" label="TRANSMISSION" view={view} setView={setView} />
          </div>

          {view === 'generation' ? (
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
                {running.length === 0 && (
                  <div className="font-mono text-[10px] text-neutral-600 px-2 py-2">
                    No generation outages running right now.
                  </div>
                )}
                {running.length > 12 && (
                  <div className="font-mono text-[9px] text-neutral-600 px-2 pt-1">
                    + {running.length - 12} smaller outages running
                  </div>
                )}
              </div>
            </>
          ) : (
            <>
              <div className="px-4 py-3 border-b border-border/30">
                <PanelTakeaway tone="info">
                  {transRunning.length > 0
                    ? `${transRunning.length} interconnector/line outage${transRunning.length === 1 ? '' : 's'} affecting ${zoneLabel} right now.`
                    : `No transmission (interconnector/line) outages affecting ${zoneLabel} right now.`}
                  {transUpcoming.length > 0 ? ` ${transUpcoming.length} more start within 30 days.` : ''}
                </PanelTakeaway>
              </div>
              <div className="px-2 py-2 overflow-x-auto">
                <table className="w-full font-mono text-[11px]">
                  <thead>
                    <tr className="text-[9px] text-neutral-600 uppercase tracking-wider">
                      <th className="text-left px-2 py-1">Asset</th>
                      <th className="text-left px-2 py-1">Counterparty</th>
                      <th className="text-right px-2 py-1">Available</th>
                      <th className="text-left px-2 py-1">Kind</th>
                      <th className="text-left px-2 py-1">Until</th>
                    </tr>
                  </thead>
                  <tbody>
                    {transRunning.slice(0, 12).map((o) => (
                      <tr key={o.mrid} className="border-t border-border/30">
                        {/* No nominal capacity is ever published for a transmission asset (ENTSO-E
                            A78 schema), so "Available" is the honest figure — there is no baseline
                            to subtract it from for an "offline" number the way generation has. */}
                        <td className="px-2 py-1.5 text-neutral-300 max-w-[180px] truncate"
                            title={o.asset_name ? `${o.asset_name}${o.asset_eic ? ` · ${o.asset_eic}` : ''}` : o.asset_eic || o.mrid}>
                          {o.asset_name || o.asset_eic || o.mrid}
                        </td>
                        <td className="px-2 py-1.5 text-neutral-500">{o.counterparty_zone || '—'}</td>
                        <td className="px-2 py-1.5 text-right font-bold text-neutral-200">{fmtGw(o.available_mw)}</td>
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
                {transRunning.length === 0 && (
                  <div className="font-mono text-[10px] text-neutral-600 px-2 py-2">
                    No transmission outages running right now.
                  </div>
                )}
                {transRunning.length > 12 && (
                  <div className="font-mono text-[9px] text-neutral-600 px-2 pt-1">
                    + {transRunning.length - 12} smaller outages running
                  </div>
                )}
              </div>
            </>
          )}

          {/* Nord Pool's UMM feed can't be re-displayed here without their written consent
              (Clause 11.1, REMIT UMM Services General Terms) — per-message deep links need
              no licence. See docs/findings/2026-07-20-umm-feasibility.md. */}
          <div className="px-4 py-2 border-t border-border/30">
            <a
              href="https://umm.nordpoolgroup.com"
              target="_blank"
              rel="noopener noreferrer"
              className="font-mono text-[9px] text-neutral-500 hover:text-cyan-glow transition-colors"
            >
              Nordic/Baltic market messages: Nord Pool UMM ↗
            </a>
            <div className="font-mono text-[9px] text-neutral-700 mt-0.5">
              Licensed for research/analysis, not re-display here — link out to read the originals.
            </div>
          </div>
        </>
      )}
    </Panel>
  )
}
