import useFetchWithError from '../hooks/useFetchWithError'

// Single-glance overview — read all bidding zones at once, colour-first, like
// Electricity Maps / Grid Status. Colour encodes how far each metric sits from its
// own ~90-day norm, so the European power picture reads in one second. Click a zone
// to drill into its detail. Descriptive, not a forecast.
const API = '/api'

const STATE = {
  CALM: { t: 'text-green-glow', d: 'bg-green-glow' },
  ELEVATED: { t: 'text-yellow-400', d: 'bg-yellow-400' },
  STRESSED: { t: 'text-red-400', d: 'bg-red-400' },
}

const zColor = (z) =>
  z == null ? 'text-neutral-400' : Math.abs(z) >= 3 ? 'text-red-400' : Math.abs(z) >= 2 ? 'text-yellow-400' : 'text-neutral-300'

export default function PowerOverviewMatrix({ selectedZone, onSelect }) {
  const { data } = useFetchWithError(`${API}/power/overview`)
  if (!data?.available) return null

  return (
    <div className="border border-border bg-surface rounded overflow-hidden">
      <div className="px-3 py-1.5 border-b border-border/60 flex items-center gap-2">
        <span className="font-mono text-[10px] tracking-wider text-neutral-500">EUROPEAN POWER · ALL ZONES</span>
        <span className="font-mono text-[9px] text-neutral-700 ml-auto">click a zone for detail →</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full font-mono text-[11px]">
          <thead>
            <tr className="text-[9px] text-neutral-600 uppercase tracking-wider">
              <th className="text-left px-3 py-1 font-normal">Zone</th>
              <th className="text-left px-2 py-1 font-normal">State</th>
              <th className="text-right px-2 py-1 font-normal">Day-ahead</th>
              <th className="text-right px-2 py-1 font-normal">Residual</th>
              <th className="text-right px-3 py-1 font-normal">Renewables</th>
            </tr>
          </thead>
          <tbody>
            {data.zones.map((z) => {
              const st = STATE[z.state] || STATE.CALM
              const sel = z.zone === selectedZone
              return (
                <tr
                  key={z.zone}
                  onClick={() => onSelect?.(z.zone)}
                  className={`cursor-pointer border-t border-border/40 hover:bg-white/[0.03] ${sel ? 'bg-cyan-glow/5' : ''}`}
                >
                  <td className="px-3 py-1.5 text-neutral-200 whitespace-nowrap">
                    {z.zone_label}
                    {sel && <span className="text-cyan-glow"> ‹</span>}
                    {z.stale && <span className="text-orange-400/70 text-[8px]"> stale</span>}
                  </td>
                  <td className="px-2 py-1.5">
                    <span className={`inline-flex items-center gap-1 font-bold ${st.t}`}>
                      <span className={`w-1.5 h-1.5 rounded-sm ${st.d}`} />
                      {z.state}
                    </span>
                  </td>
                  <td className={`px-2 py-1.5 text-right ${zColor(z.price_z)}`}>
                    {z.price_close != null ? `€${z.price_close.toFixed(0)}` : '—'}
                  </td>
                  <td className={`px-2 py-1.5 text-right ${zColor(z.residual_z)}`}>
                    {z.residual_gw != null ? `${z.residual_gw.toFixed(0)} GW` : '—'}
                  </td>
                  <td className="px-3 py-1.5 text-right text-neutral-300 whitespace-nowrap">
                    {z.renewable_reliable === false ? '—' : z.renewable_share != null ? `${Math.round(z.renewable_share * 100)}%` : '—'}
                    {z.dunkelflaute && <span className="text-yellow-400" title="Dunkelflaute"> ⚠</span>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <div className="px-3 py-1 border-t border-border/40 font-mono text-[8px] text-neutral-700 leading-snug">
        Colour = how far each metric sits from its own ~90-day norm (grey normal · amber elevated · red extreme). Descriptive, not a forecast.
      </div>
    </div>
  )
}
