import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts'
import { fmtDate, CHART_TOOLTIP_STYLE } from '../utils/chart'

const API = '/api'

const COLOR_FORECAST = '#a78bfa' // violet — the forward view
const COLOR_ACTUAL = '#22d3ee'   // cyan — realised

export default function PowerLoadForecastPanel({ zone = 'DE_LU' }) {
  const url = `${API}/power/load-forecast?days=30&zone=${zone}`
  const { data, loading, error } = useFetchWithError(url, { deps: [zone] })

  if (error)
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">LOAD FORECAST // FETCH ERROR</div>
      </div>
    )
  if (!data?.available && !loading) return null

  const rows = data?.data ?? []
  const chart = rows.map((r) => ({
    date: r.date,
    forecast: r.forecast_mw != null ? r.forecast_mw / 1000 : null,
    actual: r.actual_mw != null ? r.actual_mw / 1000 : null,
  }))
  const forward = data?.forward?.[0]
  const gw = (mw) => (mw != null ? (mw / 1000).toFixed(1) : null)
  const tomorrowGW = gw(forward?.forecast_mw)
  const residGW = gw(forward?.residual_forecast_mw)
  const windGW = gw(forward?.wind_forecast_mw)
  const solarGW = gw(forward?.solar_forecast_mw)
  const mape = data?.mape_pct

  return (
    <Panel
      id="power-load-forecast"
      title="LOAD FORECAST vs ACTUAL // ENTSO-E"
      info="ENTSO-E day-ahead forecasts (processType A01): total load (A65) and wind+solar (A69). The headline is tomorrow's RESIDUAL load = load − wind − solar — the demand dispatchable plants must cover, and the quantity that most drives the day-ahead price. The chart tracks the load forecast (violet) vs realised (cyan); the gap is the forecast error. Descriptive — higher residual tends to firm prices, but this is not a price call."
      collapsible
      headerRight={<span className="font-mono text-[9px] text-neutral-600">ENTSO-E</span>}
    >
      <div className="px-4 pt-3">
        {residGW != null ? (
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="font-mono text-[10px] text-neutral-500 tracking-wider">TOMORROW (D+1) RESIDUAL</span>
            <span className="font-mono text-xl text-violet-300 font-bold">{residGW} GW</span>
            <span className="font-mono text-[10px] text-neutral-600">
              = load {tomorrowGW} &minus; wind {windGW} &minus; solar {solarGW} · the demand dispatchable plants must cover
            </span>
          </div>
        ) : tomorrowGW != null ? (
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="font-mono text-[10px] text-neutral-500 tracking-wider">TOMORROW (D+1) LOAD</span>
            <span className="font-mono text-lg text-violet-300 font-bold">{tomorrowGW} GW</span>
            <span className="font-mono text-[10px] text-neutral-600">forecast demand</span>
          </div>
        ) : null}
        {mape != null && (
          <div className="mt-1">
            <span className="font-mono text-[10px] text-neutral-500 tracking-wider">LOAD FORECAST ERROR </span>
            <span className="font-mono text-sm text-neutral-300">±{mape}%</span>
            <span className="font-mono text-[10px] text-neutral-600"> mean abs, 30d</span>
          </div>
        )}
      </div>

      {chart.length > 0 && (
        <div className="px-2 pt-3 pb-1">
          <ResponsiveContainer width="100%" height={180}>
            <LineChart data={chart} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" />
              <XAxis dataKey="date" tickFormatter={fmtDate} tick={{ fontSize: 9, fill: '#737373' }} minTickGap={24} />
              <YAxis tick={{ fontSize: 9, fill: '#737373' }} width={34} domain={['auto', 'auto']} unit="" />
              <Tooltip {...CHART_TOOLTIP_STYLE} labelFormatter={fmtDate}
                formatter={(v, n) => [v != null ? `${v.toFixed(1)} GW` : '—', n === 'forecast' ? 'Forecast' : 'Actual']} />
              <Line type="monotone" dataKey="forecast" stroke={COLOR_FORECAST} dot={false} strokeWidth={1.5} connectNulls />
              <Line type="monotone" dataKey="actual" stroke={COLOR_ACTUAL} dot={false} strokeWidth={1.5} connectNulls />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
      <div className="px-4 py-2 font-mono text-[9px] text-neutral-700">
        Source: ENTSO-E day-ahead load forecast (A65/A01) vs realised (A16) · descriptive, not a forecast of price
      </div>
    </Panel>
  )
}
