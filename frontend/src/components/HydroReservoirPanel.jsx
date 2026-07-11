import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'

const API = '/api'

// Reservoir filling is hard seasonal, so "vs normal" is the SAME ISO week across
// prior years (the backend builds that band) — never a trailing window, which
// would flag every spring melt as an anomaly.
const BAND_STYLE = {
  below: { label: 'BELOW NORMAL', cls: 'text-orange-400 border-orange-500/30' },
  above: { label: 'ABOVE NORMAL', cls: 'text-cyan-glow border-cyan-glow/30' },
  within: { label: 'WITHIN BAND', cls: 'text-neutral-500 border-border' },
}

function BandTag({ zone }) {
  if (!zone.vs_band) {
    return <span className="font-mono text-[9px] text-neutral-700">building history (n={zone.band_n})</span>
  }
  const s = BAND_STYLE[zone.vs_band]
  return (
    <span
      className={`font-mono text-[9px] tracking-wide border rounded px-1.5 py-0.5 ${s.cls}`}
      title={`Same-week band across ${zone.band_n} prior year${zone.band_n === 1 ? '' : 's'}: ${zone.band_min_twh}–${zone.band_max_twh} TWh`}
    >
      {s.label}
    </span>
  )
}

/**
 * Weekly reservoir filling (ENTSO-E A72) for the hydro zones — Nordics, Alps,
 * Iberia, France. Southern Norway alone stores ~20 TWh; these levels move
 * power prices continent-wide. Descriptive: filling vs its own seasonal norm.
 */
export default function HydroReservoirPanel() {
  const { data, loading } = useFetchWithError(`${API}/power/hydro`, { deps: [] })

  if (!data?.available && !loading) return null
  const zones = data?.zones ?? []

  return (
    <Panel
      id="hydro-reservoirs"
      title="HYDRO RESERVOIRS · WEEKLY FILLING"
      freshness={data}
      info="ENTSO-E A72 stored hydro energy per zone (TWh), published weekly. 'vs normal' compares the newest week against the SAME calendar week in the zone's own prior years — reservoir levels are seasonal, so only a same-week band is meaningful. Descriptive: a filling level vs its norm, not a price forecast."
      collapsible
    >
      {loading && !data ? (
        <div className="px-4 py-4 font-mono text-[10px] text-neutral-600 animate-pulse">Loading reservoirs…</div>
      ) : (
        <div className="px-2 py-2 overflow-x-auto">
          <table className="w-full font-mono text-[11px]">
            <thead>
              <tr className="text-[9px] text-neutral-600 uppercase tracking-wider">
                <th className="text-left px-2 py-1">Zone</th>
                <th className="text-right px-2 py-1">Stored</th>
                <th className="text-right px-2 py-1">Δ week</th>
                <th className="text-left px-2 py-1">vs normal (same week)</th>
              </tr>
            </thead>
            <tbody>
              {zones.map((z) => (
                <tr key={z.zone} className="border-t border-border/30">
                  <td className="px-2 py-1.5 text-neutral-300">{z.zone_label}</td>
                  <td className="px-2 py-1.5 text-right text-neutral-200 font-bold">
                    {z.reservoir_twh.toFixed(1)} <span className="text-[9px] text-neutral-600 font-normal">TWh</span>
                  </td>
                  <td className={`px-2 py-1.5 text-right ${
                    z.wow_twh == null ? 'text-neutral-700'
                      : z.wow_twh >= 0 ? 'text-cyan-glow' : 'text-orange-400'
                  }`}>
                    {z.wow_twh == null ? '—' : `${z.wow_twh >= 0 ? '+' : ''}${z.wow_twh.toFixed(2)}`}
                  </td>
                  <td className="px-2 py-1.5"><BandTag zone={z} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}
