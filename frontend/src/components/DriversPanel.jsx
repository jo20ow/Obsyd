import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import { POLL_FAST_MS } from '../utils/poll'
import ReferenceBand from './ReferenceBand'

const API = '/api'

function fmtValue(d) {
  if (d.value == null) return '—'
  // The net position is signed; say the word instead of making the reader decode it.
  if (d.key === 'net_position') {
    return `${d.value >= 0 ? 'exp' : 'imp'} ${Math.abs(d.value / 1000).toFixed(1)} GW`
  }
  if (d.unit === 'MW') return `${(d.value / 1000).toFixed(1)} GW`
  if (d.unit === 'EUR/MWh') return `€${d.value.toFixed(0)}`
  return `${d.value.toFixed(0)} ${d.unit}`
}

/**
 * "Why is this zone expensive today?" — the conditions that CO-OCCUR with the
 * price, ranked by how far each sits from its own norm, plus what physically
 * similar days actually cleared.
 *
 * Posture B lives or dies in the wording here: the backend writes the sentence
 * (template-based, never an LLM) and it says WHILE, never because. This panel
 * renders it; it does not compose claims of its own.
 */
export default function DriversPanel({ zone = 'DE_LU' }) {
  const { data, loading, error } = useFetchWithError(
    `${API}/power/drivers?zone=${zone}`, { deps: [zone], pollMs: POLL_FAST_MS },
  )

  if (error) {
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">DRIVERS // FETCH ERROR</div>
      </div>
    )
  }
  if (!data?.available && !loading) {
    return (
      <div className="border border-border bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-neutral-500">
          DRIVERS — {data?.reason || 'no driver data yet.'}
        </div>
      </div>
    )
  }

  const drivers = data?.drivers ?? []
  const outage = data?.outage
  const a = data?.analogs
  const mnp = data?.market_net_position

  return (
    <Panel
      id="power-drivers"
      title={`DRIVERS · ${data?.zone_label ?? zone}`}
      info={data?.note || 'Conditions co-occurring with today’s price.'}
      freshness={data}
      collapsible
    >
      {loading && !data ? (
        <div className="px-4 py-4 font-mono text-[10px] text-neutral-600 animate-pulse">Loading drivers…</div>
      ) : (
        <>
          {/* The sentence the backend wrote. Co-occurrence, never causation. */}
          <div className="px-4 pt-3 pb-2">
            <p className="font-mono text-[13px] leading-relaxed text-neutral-300">{data.headline}</p>
          </div>

          <div className="px-2 pb-1 overflow-x-auto">
            <table className="w-full font-mono text-[11px]">
              <thead>
                <tr className="text-[9px] text-neutral-600 uppercase tracking-wider">
                  <th className="text-left px-2 py-1">Condition</th>
                  <th className="text-right px-2 py-1">Today</th>
                  <th className="text-right px-2 py-1">vs own norm</th>
                  <th className="text-left px-2 py-1 w-40">Where it sits</th>
                </tr>
              </thead>
              <tbody>
                {drivers.map((d) => (
                  <tr key={d.key} className="border-t border-border/30">
                    <td className={`px-2 py-1.5 ${d.notable ? 'text-neutral-200' : 'text-neutral-500'}`}>
                      {d.label}
                    </td>
                    <td className="px-2 py-1.5 text-right text-neutral-200">{fmtValue(d)}</td>
                    <td className={`px-2 py-1.5 text-right ${
                      d.z == null ? 'text-neutral-700'
                        : Math.abs(d.z) >= 2 ? 'text-orange-400'
                          : d.notable ? 'text-amber-400' : 'text-neutral-500'
                    }`}>
                      {d.z == null ? 'no baseline' : `${d.z >= 0 ? '+' : ''}${d.z.toFixed(1)}σ`}
                    </td>
                    <td className="px-2 py-1.5">
                      {d.z != null && <ReferenceBand z={d.z} baselineN={d.baseline_n} />}
                    </td>
                  </tr>
                ))}
                {outage && (
                  <tr className="border-t border-border/30">
                    <td className="px-2 py-1.5 text-neutral-200">{outage.label}</td>
                    <td className="px-2 py-1.5 text-right text-orange-400">
                      {(outage.value / 1000).toFixed(1)} GW
                    </td>
                    <td className="px-2 py-1.5 text-right text-neutral-500">
                      {outage.fleet_pct != null ? `${outage.fleet_pct.toFixed(0)}% of fleet` : '—'}
                    </td>
                    <td className="px-2 py-1.5 font-mono text-[9px] text-neutral-700">
                      level, not a deviation
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* The MARKET net position (ENTSO-E A25), which is NOT the physical one in the table
              above. Labelled apart, never merged: two numbers called "net position" on one
              screen, meaning two different things, is how a desk loses an analyst. */}
          {mnp && (
            <div className="px-4 py-2 border-t border-border/40 font-mono text-[10px] leading-relaxed">
              {mnp.available ? (
                <span className="text-neutral-400">
                  Day-ahead <span className="text-neutral-300">market</span> net position:{' '}
                  <span className={mnp.mean_mw >= 0 ? 'text-cyan-glow' : 'text-amber-400'}>
                    {mnp.mean_mw >= 0 ? '+' : ''}{(mnp.mean_mw / 1000).toFixed(1)} GW
                  </span>{' '}
                  ({mnp.direction}, {mnp.export_hours_pct.toFixed(0)}% of hours exporting).
                  <span className="text-neutral-700"> The SDAC auction allocation — a different
                  quantity from the physical net flow above.</span>
                </span>
              ) : (
                <span className="text-neutral-600">{mnp.reason}</span>
              )}
            </div>
          )}

          {/* Analogs: what similar days DID clear. Past tense, sample size attached. */}
          <div className="px-4 py-2 border-t border-border/40 font-mono text-[10px] leading-relaxed">
            {a?.enough ? (
              <span className="text-neutral-400">
                The <span className="text-neutral-200">{a.n}</span> days whose residual load was
                within {(a.band_mw / 1000).toFixed(1)} GW of today’s cleared at{' '}
                <span className="text-neutral-200">€{a.mean_price.toFixed(0)}</span> on average
                (p10–p90: €{a.p10.toFixed(0)}–€{a.p90.toFixed(0)}).
                {data.price?.value != null && (
                  <> Today is <span className="text-cyan-glow">€{data.price.value.toFixed(0)}</span>.</>
                )}
              </span>
            ) : (
              <span className="text-neutral-600">{a?.reason || 'No comparable days yet.'}</span>
            )}
          </div>
        </>
      )}
    </Panel>
  )
}
