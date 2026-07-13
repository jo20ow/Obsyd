import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import { POLL_SLOW_MS } from '../utils/poll'
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, ReferenceLine, Legend,
} from 'recharts'
import { CHART_TOOLTIP_STYLE } from '../utils/chart'

const API = '/api'

/** One colour per technology, held across the table and the chart. */
const FUEL_COLOR = {
  B16: '#fbbf24',  // solar
  B19: '#22d3ee',  // wind onshore
  B18: '#38bdf8',  // wind offshore
  B11: '#2dd4bf',  // hydro run-of-river
  B12: '#4ade80',  // hydro reservoir
  B14: '#a78bfa',  // nuclear
  B04: '#f87171',  // fossil gas
}

/**
 * Capture rate — what a MWh of each technology actually earned.
 *
 * The value factor is the whole point: below 1.00 the technology earned less than
 * the plain baseload price, because it produces in the hours it made cheap. That is
 * cannibalisation, in the market's own numbers, and no free European tool publishes
 * it per bidding zone.
 */
export default function CapturePanel({ zone = 'DE_LU' }) {
  const { data, loading, error } = useFetchWithError(
    `${API}/power/capture?zone=${zone}&months=36`, { deps: [zone], pollMs: POLL_SLOW_MS },
  )

  if (error) {
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">CAPTURE RATE // FETCH ERROR</div>
      </div>
    )
  }
  if (!data?.available && !loading) {
    return (
      <div className="border border-border bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-neutral-500">
          CAPTURE RATE — {data?.reason || 'no complete month of prices and generation yet.'}
        </div>
      </div>
    )
  }

  const fuels = data?.fuels ?? []

  // One row per month, one column per technology: the value-factor history.
  const months = [...new Set(fuels.flatMap((f) => f.data.map((r) => r.month)))].sort()
  const chart = months.map((month) => {
    const row = { month }
    for (const f of fuels) {
      const hit = f.data.find((r) => r.month === month)
      if (hit) row[f.psr] = hit.value_factor
    }
    return row
  })
  const worst = fuels[0]?.latest

  return (
    <Panel
      id="power-capture"
      title={`CAPTURE RATE · ${data?.zone_label ?? zone}`}
      info={data?.note || 'Generation-weighted price each technology achieved, vs. baseload.'}
      freshness={data}
      collapsible
      headerRight={
        worst && (
          <div className="flex items-center gap-3 font-mono text-[10px]">
            <span className="text-neutral-500">
              Baseload <span className="text-neutral-200">€{data.baseload_price?.toFixed(0)}</span>
            </span>
            {worst.value_factor != null && (
              <span className="text-neutral-500">
                {fuels[0].label} <span className="text-amber-400">×{worst.value_factor.toFixed(2)}</span>
              </span>
            )}
            <span className="text-neutral-600">{data.latest_month}</span>
          </div>
        )
      }
    >
      {loading && !data ? (
        <div className="px-4 py-4 font-mono text-[10px] text-neutral-600 animate-pulse">Loading capture rates…</div>
      ) : (
        <>
          <div className="px-2 py-2 overflow-x-auto">
            <table className="w-full font-mono text-[11px]">
              <thead>
                <tr className="text-[9px] text-neutral-600 uppercase tracking-wider">
                  <th className="text-left px-2 py-1">Technology</th>
                  <th className="text-right px-2 py-1" title="Generation-weighted average day-ahead price it achieved">Capture</th>
                  <th className="text-right px-2 py-1" title="Capture price ÷ the month's baseload price. Below 1.00 = earned less than baseload.">Value factor</th>
                  <th className="px-2 py-1"></th>
                  <th className="text-right px-2 py-1" title="Share of this technology's own output that landed in negative-price hours">Neg. output</th>
                  <th className="text-right px-2 py-1">Volume</th>
                </tr>
              </thead>
              <tbody>
                {fuels.map((f) => {
                  const L = f.latest
                  const vf = L.value_factor
                  const color = FUEL_COLOR[f.psr] || '#94a3b8'
                  return (
                    <tr key={f.psr} className="border-t border-border/30">
                      <td className="px-2 py-1.5 text-neutral-300">
                        <span className="inline-block w-1.5 h-1.5 rounded-full mr-2" style={{ background: color }} />
                        {f.label}
                      </td>
                      <td className="px-2 py-1.5 text-right text-neutral-200">€{L.capture_price.toFixed(1)}</td>
                      <td className={`px-2 py-1.5 text-right ${
                        vf == null ? 'text-neutral-700' : vf < 1 ? 'text-amber-400' : 'text-cyan-glow'
                      }`}>
                        {vf != null ? `×${vf.toFixed(2)}` : '—'}
                      </td>
                      {/* The bar reads against 1.00: left of the line = earned below baseload. */}
                      <td className="px-2 py-1.5 w-32">
                        {vf != null && (
                          <div className="relative h-1.5 bg-neutral-900 rounded-sm">
                            <div className="absolute inset-y-0 left-1/2 w-px bg-neutral-600" />
                            <div
                              className="absolute inset-y-0 rounded-sm"
                              style={{
                                background: color,
                                opacity: 0.7,
                                ...(vf < 1
                                  ? { right: '50%', width: `${Math.min(50, (1 - vf) * 50)}%` }
                                  : { left: '50%', width: `${Math.min(50, (vf - 1) * 50)}%` }),
                              }}
                            />
                          </div>
                        )}
                      </td>
                      <td className={`px-2 py-1.5 text-right ${L.negative_gen_pct > 0 ? 'text-orange-400' : 'text-neutral-700'}`}>
                        {L.negative_gen_pct > 0 ? `${L.negative_gen_pct.toFixed(1)}%` : '—'}
                      </td>
                      <td className="px-2 py-1.5 text-right text-neutral-500">{L.generation_gwh.toFixed(0)} GWh</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {chart.length > 1 && (
            <div className="px-2 pb-2">
              <div className="px-2 pb-1 font-mono text-[9px] text-neutral-600 uppercase tracking-wider">
                Value factor by month
              </div>
              <ResponsiveContainer width="100%" height={170}>
                <LineChart data={chart} margin={{ top: 4, right: 8, bottom: 2, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" />
                  <XAxis dataKey="month" tick={{ fontSize: 8, fill: '#737373' }} minTickGap={40} />
                  <YAxis tick={{ fontSize: 8, fill: '#737373' }} width={34}
                    tickFormatter={(v) => `×${v.toFixed(1)}`} />
                  {/* Baseload itself. Everything below this line earned less than the base product. */}
                  <ReferenceLine y={1} stroke="#525252" strokeDasharray="4 4" />
                  <Tooltip
                    contentStyle={CHART_TOOLTIP_STYLE}
                    formatter={(v, n) => [v == null ? '—' : `×${Number(v).toFixed(2)}`, n]}
                  />
                  <Legend wrapperStyle={{ fontSize: 9, color: '#737373' }} iconSize={6} />
                  {fuels.map((f) => (
                    <Line key={f.psr} type="monotone" dataKey={f.psr} name={f.label}
                      stroke={FUEL_COLOR[f.psr] || '#94a3b8'} strokeWidth={1.4}
                      dot={false} connectNulls isAnimationActive={false} />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}

          <div className="px-4 pb-2 font-mono text-[9px] text-neutral-700 leading-relaxed">
            Realised day-ahead revenue per MWh, not a forecast. A factor below ×1.00 means the
            technology earned less than the month&apos;s baseload price — it produces in the hours it
            made cheap. Day-ahead only: assets also earn in intraday, balancing and their PPA.
            Months where a technology is absent for whole days are dropped (≥{data.min_days} days required).
          </div>
        </>
      )}
    </Panel>
  )
}
