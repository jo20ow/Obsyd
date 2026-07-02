import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, ReferenceLine,
} from 'recharts'
import { CHART_TOOLTIP_STYLE } from '../utils/chart'

const API = '/api'

function Stat({ label, value, color }) {
  return (
    <div>
      <div className="font-mono text-[9px] text-neutral-600 uppercase tracking-wider">{label}</div>
      <div className={`font-mono text-lg font-bold ${color || 'text-neutral-200'}`}>{value}</div>
    </div>
  )
}

export default function RatesPanel() {
  const { data, loading, error } = useFetchWithError(`${API}/rates/curve`)

  if (error) {
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">RATES // FETCH ERROR</div>
      </div>
    )
  }

  const pts = data?.data ?? []
  const byId = Object.fromEntries(pts.map((p) => [p.series_id, p.yield]))
  const spread = data?.spread_10y2y
  const spreadBp = spread == null ? null : Math.round(spread * 100)

  return (
    <Panel
      id="rates"
      title="US TREASURY YIELD CURVE"
      info="Constant-maturity U.S. Treasury yields (1M–30Y) from FRED — free, complete, public-domain. The 10Y-2Y spread is the classic recession/inversion gauge (negative = inverted). Not investment advice."
      collapsible
      headerRight={data?.as_of && <span className="font-mono text-[9px] text-neutral-600">FRED · {data.as_of}</span>}
    >
      {loading && !data && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">Loading rates…</div>
      )}

      {!data?.available && !loading && (
        <div className="px-4 py-4 font-mono text-[11px] text-neutral-500">No yield-curve data yet — check back shortly.</div>
      )}

      {data?.available && (
        <>
          <div className="px-4 py-3 border-b border-border/30 flex items-center gap-6 flex-wrap">
            <Stat label="2Y" value={byId.DGS2 != null ? `${byId.DGS2.toFixed(2)}%` : '—'} />
            <Stat label="10Y" value={byId.DGS10 != null ? `${byId.DGS10.toFixed(2)}%` : '—'} />
            <Stat
              label="10Y-2Y"
              value={spreadBp == null ? '—' : `${spreadBp >= 0 ? '+' : ''}${spreadBp} bp`}
              color={spreadBp == null ? '' : spreadBp < 0 ? 'text-red-400' : 'text-green-glow'}
            />
            {data.inverted && (
              <span className="font-mono text-[10px] tracking-wide text-red-400 border border-red-500/30 rounded px-2 py-0.5 self-center">
                INVERTED
              </span>
            )}
          </div>
          {pts.length > 1 && (
            <div className="px-2 py-2">
              <ResponsiveContainer width="100%" height={140}>
                <LineChart data={pts} margin={{ top: 5, right: 10, bottom: 5, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" />
                  <XAxis dataKey="tenor" tick={{ fontSize: 9, fill: '#666', fontFamily: 'monospace' }} />
                  <YAxis
                    tick={{ fontSize: 8, fill: '#55556688', fontFamily: 'monospace' }}
                    width={34}
                    tickFormatter={(v) => `${v}%`}
                    domain={['auto', 'auto']}
                  />
                  <Tooltip
                    contentStyle={CHART_TOOLTIP_STYLE}
                    formatter={(v) => [`${Number(v).toFixed(2)}%`, 'Yield']}
                  />
                  {byId.DGS2 != null && <ReferenceLine y={byId.DGS2} stroke="#333" strokeDasharray="2 2" />}
                  <Line type="monotone" dataKey="yield" stroke="#22d3ee" strokeWidth={2} dot={{ r: 2, fill: '#22d3ee' }} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
          <div className="px-4 py-1.5 font-mono text-[9px] text-neutral-700">
            Source: FRED (constant-maturity treasury) · not investment advice
          </div>
        </>
      )}
    </Panel>
  )
}
