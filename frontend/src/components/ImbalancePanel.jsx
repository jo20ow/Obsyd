import { useState } from 'react'
import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import { POLL_SLOW_MS } from '../utils/poll'
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceLine,
} from 'recharts'
import { fmtTs, CHART_TOOLTIP_STYLE } from '../utils/chart'

const API = '/api'

function zoneLabel(zone) {
  return zone === 'DE_LU' ? 'DE-LU' : zone
}

function ResToggle({ res, onChange }) {
  return (
    <div className="flex gap-1">
      {['hourly', 'qh'].map((r) => (
        <button
          key={r}
          onClick={() => onChange(r)}
          className={`font-mono text-[9px] tracking-wider px-1.5 py-0.5 rounded border transition-colors ${
            res === r
              ? 'border-cyan-glow/40 text-cyan-glow'
              : 'border-border text-neutral-600 hover:text-neutral-400'
          }`}
        >
          {r === 'qh' ? '15-MIN' : 'HOURLY'}
        </button>
      ))}
    </div>
  )
}

/**
 * Imbalance prices (ENTSO-E A85; reBAP for DE-LU) — what being out of balance
 * actually costs. This is the intraday stress gauge that day-ahead daily means
 * smooth away: ±1000 €/MWh quarter-hours are invisible everywhere else on the
 * desk. Descriptive, not a forecast.
 */
export default function ImbalancePanel({ zone = 'DE_LU' }) {
  const [res, setRes] = useState('hourly')
  const url = `${API}/power/imbalance?zone=${zone}&days=7&resolution=${res}`
  const { data, loading, error } = useFetchWithError(url, {
    deps: [zone, res],
    pollMs: POLL_SLOW_MS,
  })

  const zl = zoneLabel(zone)

  if (error) {
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">IMBALANCE // FETCH ERROR</div>
      </div>
    )
  }

  // Never vanish silently — A85 coverage varies by zone; say so.
  if (!data?.available && !loading) {
    return (
      <div className="border border-border bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-neutral-500">
          IMBALANCE · {zl} — {data?.reason || 'no imbalance-price series yet.'}
        </div>
      </div>
    )
  }

  const rows = (data?.data ?? []).map((p) => ({ x: p.ts_utc, price: p.price }))
  const latest = data?.latest
  const peak = data?.peak
  const latestColor = latest == null ? '#737373' : Math.abs(latest) >= 300 ? '#fb923c' : '#a3a3a3'

  return (
    <Panel
      id="power-imbalance"
      freshness={data}
      title={`IMBALANCE PRICE · ${zl}`}
      info="ENTSO-E A85 imbalance settlement price (reBAP for DE-LU via the country EIC) — the price of being out of balance, settled per 15 minutes where the market does. This is where grid stress prices first; day-ahead daily means smooth it away. All times UTC. Descriptive, not a forecast."
      collapsible
      headerRight={
        <div className="flex items-center gap-2">
          <ResToggle res={res} onChange={setRes} />
          {latest != null && (
            <span className="font-mono text-[10px]" style={{ color: latestColor }}>
              {latest.toFixed(0)} €/MWh
            </span>
          )}
        </div>
      }
    >
      {loading && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">
          Loading imbalance prices…
        </div>
      )}

      {!loading && data?.available && (
        <>
          <div className="px-4 pt-2">
            <ResponsiveContainer width="100%" height={160}>
              <LineChart data={rows} margin={{ top: 4, right: 8, bottom: 2, left: 0 }}>
                <XAxis
                  dataKey="x"
                  tickFormatter={fmtTs}
                  tick={{ fontSize: 9, fill: '#525252' }}
                  minTickGap={60}
                />
                <YAxis
                  tick={{ fontSize: 9, fill: '#525252' }}
                  width={44}
                  tickFormatter={(v) => `${v}`}
                />
                <ReferenceLine y={0} stroke="#2a2a3a" strokeDasharray="2 2" />
                <Tooltip
                  contentStyle={CHART_TOOLTIP_STYLE}
                  formatter={(v) => [`${v.toFixed(1)} €/MWh`, 'imbalance']}
                  labelFormatter={fmtTs}
                />
                <Line
                  type="stepAfter"
                  dataKey="price"
                  stroke="#22d3ee"
                  strokeWidth={1}
                  dot={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <div className="px-4 py-2 font-mono text-[9px] text-neutral-700">
            last {data?.days}d · window peak {peak?.price?.toFixed(0)} €/MWh ({peak ? fmtTs(peak.ts_utc) : '—'})
            {res === 'qh' ? ' · raw 15-min settlement points' : ' · hourly means'}
          </div>
        </>
      )}
    </Panel>
  )
}
