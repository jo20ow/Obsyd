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

// Compact per-row age stamp: "today" for lag 0, else "D-2" — the full UTC
// timestamp rides in the title tooltip next to it. <= 0, not === 0: a
// future-dated row from a corrupt document must not render "D--1".
function lagStamp(days) {
  if (days == null) return null
  return days <= 0 ? 'today' : `D-${days}`
}

/**
 * Per-plant output at each unit's OWN latest published hour (ENTSO-E A73) —
 * which named plants ran, how hard against nameplate, and whether an outage
 * explains a gap.
 *
 * The honesty IS the feature: the four TSOs publish at different speeds (up to
 * the regulation's D+5), so every row carries its own quiet age stamp, the
 * caption shows the actual lag range instead of pretending one shared hour
 * exists, and the totals line says "mixed timestamps, not a snapshot". The bar
 * is output vs NAMEPLATE (utilization, not availability); the outage badge is
 * joined at the CURRENT time (near-real-time A77) while the output value is the
 * last published reading; and the population is only the published >=100 MW
 * dispatchable units, not the fleet (the API note carries the full caveat into
 * the info popover).
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

  // The caption's lag range is DERIVED from the payload, not hardcoded — the
  // TSOs' actual skew (e.g. D-2…D-5) is the honest thing to show.
  const lags = units.map((u) => u.unit_lag_days).filter((d) => d != null)
  const minLag = lags.length ? Math.min(...lags) : null
  const maxLag = lags.length ? Math.max(...lags) : null

  return (
    <Panel
      source="ENTSO-E A73 · per-unit actual generation"
      id="unit-generation"
      title="PLANT OUTPUT · LATEST PUBLISHED READINGS"
      freshness={data}
      info={data?.note}
      collapsible
      headerRight={
        data?.latest_hour_utc && (
          <span className="font-mono text-[10px] text-neutral-500">
            freshest {fmtDay(data.latest_hour_utc)}
            {minLag != null && (
              maxLag > minLag
                ? ` · per-plant timestamps vary (${lagStamp(minLag)}…${lagStamp(maxLag)} — TSOs publish at different speeds)`
                : ` · all plants ${lagStamp(minLag)}`
            )}
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
                ? `${totals.reporting} of ${totals.units} published units with readings — ${fmtMw(totals.latest_readings_mw)} summed from each plant's latest published reading (mixed timestamps, not a snapshot) against ${fmtMw(totals.nominal_mw)} of nameplate.`
                : "Per-unit output at each plant's latest published hour."}
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
                  <th className="text-right px-2 py-1">Last</th>
                  <th className="text-left px-2 py-1 min-w-[110px]">Utilization</th>
                  <th className="text-left px-2 py-1">Outage now</th>
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
                    <td className="px-2 py-1.5 text-right">
                      <span className="font-bold text-neutral-200">{fmtMw(u.current_mw)}</span>
                      {/* Quiet per-unit age stamp — the TSOs publish at different
                          speeds, so each reading carries its own timestamp. */}
                      {u.unit_lag_days != null && (
                        <div className="text-[9px] text-neutral-600 leading-tight" title={u.unit_latest_hour_utc}>
                          {lagStamp(u.unit_lag_days)}
                        </div>
                      )}
                    </td>
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
                        // current_mw is never null anymore (every listed unit
                        // carries its own latest reading) — a dash here means
                        // the registry has no nameplate to divide by.
                        <span className="text-[9px] text-neutral-600">—</span>
                      )}
                    </td>
                    <td className="px-2 py-1.5">
                      {u.outage ? (
                        <span
                          className={`text-[9px] tracking-wide border rounded px-1.5 py-0.5 ${
                            u.outage.kind === 'forced'
                              ? 'text-orange-400 border-orange-500/30'
                              : 'text-neutral-500 border-border'
                          }`}
                          title={`${u.outage.kind === 'forced' ? 'Forced' : 'Planned'} outage active NOW (near-real-time A77) — the output value is the plant's last published reading${u.outage.offline_mw != null ? ` · ~${Math.round(u.outage.offline_mw)} MW offline` : ''}`}
                        >
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
