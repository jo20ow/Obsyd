import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid, ReferenceLine,
} from 'recharts'
import { fmtDate, CHART_TOOLTIP_STYLE } from '../utils/chart'

const API = '/api'

export default function SparkSpreadPanel() {
  const { data, loading, error } = useFetchWithError(`${API}/power/spark-spread?days=120`)

  if (error)
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">SPARK SPREAD // FETCH ERROR</div>
      </div>
    )
  if (!data?.available && !loading) return null

  const rows = data?.data ?? []
  const latest = data?.latest
  const spread = latest?.spark_spread
  const spreadColor = spread == null ? '#737373' : spread >= 0 ? '#4ade80' : '#fb923c'

  return (
    <Panel
      id="spark-spread"
      title="SPARK SPREAD · CCGT GENERATION MARGIN"
      info="Spark spread = power − gas × heat-rate (CCGT generation margin). Measures the theoretical profitability of gas-fired power generation. Positive = burning gas to generate electricity is profitable. Clean spark (− CO₂ cost) coming once EUA data is wired."
      collapsible
      headerRight={
        spread != null && (
          <span className="font-mono text-[10px] font-bold" style={{ color: spreadColor }}>
            {spread >= 0 ? '+' : ''}{spread.toFixed(1)} €/MWh
          </span>
        )
      }
    >
      {loading && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">
          Computing spark spread…
        </div>
      )}
      {!loading && data?.available && latest && (
        <>
          <div className="px-4 py-3 border-b border-border/30">
            <div className="flex items-baseline gap-3 flex-wrap">
              <span className="font-mono text-3xl font-bold" style={{ color: spreadColor }}>
                {spread == null
                  ? '—'
                  : `${spread >= 0 ? '+' : ''}${spread.toFixed(1)}`}
              </span>
              <span className="font-mono text-[10px] text-neutral-600">EUR/MWh spark spread</span>
            </div>
            <div className="flex items-center gap-4 mt-2 flex-wrap">
              <div className="font-mono text-[10px] text-neutral-500">
                <span className="text-neutral-600">POWER</span>{' '}
                <span className="text-neutral-300">
                  {latest.power_price != null ? `${latest.power_price.toFixed(1)} €/MWh` : '—'}
                </span>
              </div>
              <div className="font-mono text-[10px] text-neutral-500">
                <span className="text-neutral-600">GAS</span>{' '}
                <span className="text-neutral-300">
                  {latest.gas_price != null ? `${latest.gas_price.toFixed(2)} €/MWh` : '—'}
                </span>
              </div>
              <div className="font-mono text-[10px] text-neutral-500">
                <span className="text-neutral-600">HEAT-RATE</span>{' '}
                <span className="text-neutral-300">
                  {latest.heat_rate != null ? latest.heat_rate.toFixed(3) : '—'}
                </span>
              </div>
            </div>
            <div className="font-mono text-[10px] text-neutral-700 mt-1.5">
              Clean spark (− CO₂) coming once EUA data is wired.
            </div>
          </div>
          {rows.length > 1 && (
            <div className="px-2 py-2">
              <ResponsiveContainer width="100%" height={120}>
                <AreaChart data={rows} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" />
                  <XAxis
                    dataKey="date"
                    tick={{ fontSize: 8, fill: '#555', fontFamily: 'monospace' }}
                    tickFormatter={fmtDate}
                    interval="preserveStartEnd"
                    minTickGap={60}
                  />
                  <YAxis
                    tick={{ fontSize: 8, fill: '#55556688', fontFamily: 'monospace' }}
                    width={30}
                  />
                  <Tooltip
                    contentStyle={CHART_TOOLTIP_STYLE}
                    formatter={(v) => [
                      `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(1)} €/MWh`,
                      'Spark Spread',
                    ]}
                    labelFormatter={fmtDate}
                  />
                  <ReferenceLine y={0} stroke="#444" />
                  <Area
                    type="monotone"
                    dataKey="spark_spread"
                    stroke="#4ade80"
                    fill="#4ade80"
                    fillOpacity={0.06}
                    strokeWidth={1.5}
                    dot={false}
                  />
                </AreaChart>
              </ResponsiveContainer>
              <div className="flex items-center justify-center gap-4 mt-1 font-mono text-[8px] text-neutral-600">
                <span style={{ color: '#4ade80' }}>▬ spark spread</span>
                <span className="text-neutral-600">— zero line</span>
              </div>
            </div>
          )}
        </>
      )}
    </Panel>
  )
}
