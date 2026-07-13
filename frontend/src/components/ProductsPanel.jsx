import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import { POLL_SLOW_MS } from '../utils/poll'
import {
  ResponsiveContainer, ComposedChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, ReferenceLine,
} from 'recharts'
import { fmtDate, CHART_TOOLTIP_STYLE } from '../utils/chart'

const API = '/api'

/**
 * Base / Peak / Off-peak on the CET delivery day — the products Europe trades.
 *
 * The premium is the number to read: below 1.0 means the PEAK product is cheaper
 * than off-peak, which is what solar does to a summer market. The desk could not
 * say that before — it only knew a daily mean.
 */
export default function ProductsPanel({ zone = 'DE_LU' }) {
  const { data, loading, error } = useFetchWithError(
    `${API}/power/products?zone=${zone}&days=30`, { deps: [zone], pollMs: POLL_SLOW_MS },
  )

  if (error) {
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">BASE / PEAK // FETCH ERROR</div>
      </div>
    )
  }
  if (!data?.available && !loading) {
    return (
      <div className="border border-border bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-neutral-500">
          BASE / PEAK — {data?.reason || 'no hourly prices yet.'}
        </div>
      </div>
    )
  }

  const rows = data?.data ?? []
  const latest = data?.latest
  const chart = rows.map((r) => ({
    date: r.date, base: r.base, peak: r.peak, off: r.off_peak,
  }))

  return (
    <Panel
      id="power-products"
      title={`BASE / PEAK · ${data?.zone_label ?? zone}`}
      info={data?.note || 'Base, Peak and Off-peak on the CET delivery day.'}
      freshness={data}
      collapsible
      headerRight={
        latest && (
          <div className="flex items-center gap-3 font-mono text-[10px]">
            <span className="text-neutral-500">Base <span className="text-neutral-200">€{latest.base.toFixed(0)}</span></span>
            {latest.peak != null && (
              <span className="text-neutral-500">Peak <span className="text-neutral-200">€{latest.peak.toFixed(0)}</span></span>
            )}
            {latest.peak_premium != null && (
              <span className={latest.peak_premium < 1 ? 'text-amber-400' : 'text-neutral-400'}>
                ×{latest.peak_premium.toFixed(2)}
              </span>
            )}
          </div>
        )
      }
    >
      {loading && !data ? (
        <div className="px-4 py-4 font-mono text-[10px] text-neutral-600 animate-pulse">Loading products…</div>
      ) : (
        <>
          <div className="px-2 pt-3">
            <ResponsiveContainer width="100%" height={170}>
              <ComposedChart data={chart} margin={{ top: 4, right: 8, bottom: 2, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" />
                <XAxis dataKey="date" tickFormatter={fmtDate} tick={{ fontSize: 8, fill: '#737373' }} minTickGap={50} />
                <YAxis tick={{ fontSize: 8, fill: '#737373' }} width={40} />
                <ReferenceLine y={0} stroke="#444" />
                <Tooltip
                  contentStyle={CHART_TOOLTIP_STYLE}
                  labelFormatter={fmtDate}
                  formatter={(v, n) => [v == null ? '—' : `€${Number(v).toFixed(1)}`, n]}
                />
                <Line type="monotone" dataKey="base" name="base" stroke="#94a3b8" strokeWidth={1.4}
                  dot={false} isAnimationActive={false} />
                <Line type="monotone" dataKey="peak" name="peak" stroke="#fbbf24" strokeWidth={1.4}
                  dot={false} connectNulls={false} isAnimationActive={false} />
                <Line type="monotone" dataKey="off" name="off-peak" stroke="#22d3ee" strokeWidth={1}
                  dot={false} isAnimationActive={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>

          <div className="px-2 py-2 overflow-x-auto">
            <table className="w-full font-mono text-[11px]">
              <thead>
                <tr className="text-[9px] text-neutral-600 uppercase tracking-wider">
                  <th className="text-left px-2 py-1">Delivery day</th>
                  <th className="text-right px-2 py-1">Base</th>
                  <th className="text-right px-2 py-1">Peak</th>
                  <th className="text-right px-2 py-1">Off-peak</th>
                  <th className="text-right px-2 py-1" title="Peak ÷ Base. Below 1.0 = the peak product is cheaper than off-peak.">Premium</th>
                  <th className="text-right px-2 py-1" title="Steepest 3-hour rise of the day">Ramp</th>
                  <th className="text-right px-2 py-1">Neg h</th>
                </tr>
              </thead>
              <tbody>
                {rows.slice(-10).reverse().map((r) => (
                  <tr key={r.date} className="border-t border-border/30">
                    <td className="px-2 py-1.5 text-neutral-300">
                      {r.date}
                      {r.weekend && <span className="ml-1.5 text-[9px] text-neutral-700">WE</span>}
                    </td>
                    <td className="px-2 py-1.5 text-right text-neutral-200">€{r.base.toFixed(0)}</td>
                    <td className="px-2 py-1.5 text-right text-amber-400">
                      {r.peak != null ? `€${r.peak.toFixed(0)}` : '—'}
                    </td>
                    <td className="px-2 py-1.5 text-right text-cyan-glow">€{r.off_peak.toFixed(0)}</td>
                    <td className={`px-2 py-1.5 text-right ${
                      r.peak_premium == null ? 'text-neutral-700'
                        : r.peak_premium < 1 ? 'text-amber-400' : 'text-neutral-400'
                    }`}>
                      {r.peak_premium != null ? `×${r.peak_premium.toFixed(2)}` : '—'}
                    </td>
                    <td className="px-2 py-1.5 text-right text-neutral-500">
                      {r.evening_ramp != null ? `€${r.evening_ramp.toFixed(0)}` : '—'}
                    </td>
                    <td className={`px-2 py-1.5 text-right ${r.negative_hours > 0 ? 'text-orange-400' : 'text-neutral-700'}`}>
                      {r.negative_hours || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="px-4 pb-2 font-mono text-[9px] text-neutral-700 leading-relaxed">
            {data.peak_definition}. Weekends have no peak product. A premium below ×1.00 means the
            peak product cleared BELOW off-peak — what midday solar does to a summer market.
          </div>
        </>
      )}
    </Panel>
  )
}
