import Panel from './Panel'
import PanelTakeaway from './PanelTakeaway'
import useFetchWithError from '../hooks/useFetchWithError'
import { POLL_SLOW_MS } from '../utils/poll'
import { fuelColor } from '../utils/fuels'

const API = '/api'
const ROW_CAP = 15

function fmtMw(mw) {
  if (mw == null) return '—'
  return mw >= 1000 ? `${(mw / 1000).toFixed(1)} GW` : `${Math.round(mw)} MW`
}

// "2026-07-22T22:00Z" → "Jul 22" (hours are UTC by construction)
function fmtDay(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })
}

/**
 * Per-plant output for the LATEST PUBLISHED day (ENTSO-E A73) — which named
 * plants ran, how hard against nameplate, and whether an outage explains a gap.
 *
 * The honesty IS the feature: A73 publishes ~6 days behind, so the caption says
 * "published <day> · N days behind" instead of pretending to be live; the bar
 * is output vs NAMEPLATE (utilization, not availability); and the population is
 * only the published >=100 MW dispatchable units, not the fleet (the API note
 * carries the full caveat into the info popover).
 */
export default function UnitGenerationPanel({ zone = 'DE_LU' }) {
  const { data, loading, error } = useFetchWithError(`${API}/power/units/generation?zone=${zone}`, { deps: [zone], pollMs: POLL_SLOW_MS })

  if (error && !data) {
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">PLANT OUTPUT // FETCH ERROR</div>
      </div>
    )
  }

  if (!data?.available && !loading) {
    return (
      <div className="border border-border bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-neutral-500">
          PLANT OUTPUT — {data?.reason || 'no per-unit generation for this zone yet.'}
        </div>
      </div>
    )
  }

  const units = data?.units ?? []
  const shown = units.slice(0, ROW_CAP)
  const totals = data?.totals

  return (
    <Panel
      id="unit-generation"
      title="PLANT OUTPUT · LATEST PUBLISHED DAY"
      freshness={data}
      info={data?.note}
      collapsible
      headerRight={
        data?.latest_hour_utc && (
          <span className="font-mono text-[10px] text-neutral-500">
            published {fmtDay(data.latest_hour_utc)}
            {data.lag_days != null ? ` · ${data.lag_days} day${data.lag_days === 1 ? '' : 's'} behind` : ''}
          </span>
        )
      }
    >
      {loading && !data ? (
        <div className="px-4 py-4 font-mono text-[10px] text-neutral-600 animate-pulse">Loading plant output…</div>
      ) : (
        <>
          <div className="px-4 py-3 border-b border-border/30">
            <PanelTakeaway tone="info">
              {totals
                ? `${totals.reporting} of ${totals.units} published units reporting at the latest hour — ${fmtMw(totals.generating_mw)} generating against ${fmtMw(totals.nominal_mw)} of nameplate.`
                : 'Per-unit output for the latest published day.'}
              {' '}Published units only (≥100 MW, dispatchable fuels) — not the fleet.
            </PanelTakeaway>
          </div>
          <div className="px-2 py-2 overflow-x-auto">
            <table className="w-full font-mono text-[11px]">
              <thead>
                <tr className="text-[9px] text-neutral-600 uppercase tracking-wider">
                  <th className="text-left px-2 py-1">Unit</th>
                  <th className="text-left px-2 py-1">Fuel</th>
                  <th className="text-right px-2 py-1">Nominal</th>
                  <th className="text-right px-2 py-1">Now</th>
                  <th className="text-left px-2 py-1 min-w-[110px]">Utilization</th>
                  <th className="text-left px-2 py-1">Outage</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((u) => (
                  <tr key={u.unit_eic} className="border-t border-border/30">
                    <td className="px-2 py-1.5 text-neutral-300 max-w-[180px] truncate"
                        title={u.name ? `${u.name} · ${u.unit_eic}` : u.unit_eic}>
                      {u.name || u.unit_eic}
                    </td>
                    <td className="px-2 py-1.5">
                      {u.fuel ? (
                        <span className="inline-flex items-center gap-1.5 text-neutral-400">
                          <span className="w-2 h-2 rounded-sm shrink-0" style={{ backgroundColor: fuelColor(u.psr_type) }} />
                          <span className="max-w-[110px] truncate">{u.fuel}</span>
                        </span>
                      ) : '—'}
                    </td>
                    <td className="px-2 py-1.5 text-right text-neutral-500">{fmtMw(u.nominal_mw)}</td>
                    <td className="px-2 py-1.5 text-right font-bold text-neutral-200">{fmtMw(u.current_mw)}</td>
                    <td className="px-2 py-1.5">
                      {u.utilization_pct != null ? (
                        <div className="flex items-center gap-1.5">
                          {/* Width is clamped for display; the LABEL keeps the unclamped
                              number — a unit above nameplate should say so, not hide it. */}
                          <div className="flex-1 h-1.5 bg-border/40 rounded overflow-hidden min-w-[48px]">
                            <div
                              className="h-full rounded bg-cyan-glow/70"
                              style={{ width: `${Math.max(0, Math.min(100, u.utilization_pct))}%` }}
                            />
                          </div>
                          <span className="text-[9px] text-neutral-500 w-9 text-right shrink-0">{u.utilization_pct}%</span>
                        </div>
                      ) : (
                        <span className="text-[9px] text-neutral-600">{u.current_mw == null ? 'not reporting' : '—'}</span>
                      )}
                    </td>
                    <td className="px-2 py-1.5">
                      {u.outage ? (
                        <span className={`text-[9px] tracking-wide border rounded px-1.5 py-0.5 ${
                          u.outage.kind === 'forced'
                            ? 'text-orange-400 border-orange-500/30'
                            : 'text-neutral-500 border-border'
                        }`}>
                          {u.outage.kind === 'forced' ? 'FORCED' : 'PLANNED'}
                        </span>
                      ) : ''}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {/* No empty-state branch: available:true implies at least the unit that
                set the latest hour, so units is never empty here. */}
            {units.length > ROW_CAP && (
              <div className="font-mono text-[9px] text-neutral-600 px-2 pt-1">
                + {units.length - ROW_CAP} more units
              </div>
            )}
          </div>
        </>
      )}
    </Panel>
  )
}
