import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import {
  ResponsiveContainer,
  ComposedChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Legend,
} from 'recharts'
import { fmtDate, CHART_TOOLTIP_STYLE } from '../utils/chart'
import TrackRecordBadge from './TrackRecordBadge'

const API = '/api'

const COLOR_MINE   = '#22d3ee' // cyan — mine production
const COLOR_STOCKS = '#f59e0b' // amber — refined stocks
const COLOR_PRICE  = '#a78bfa' // violet — copper price (right axis)

// Helper: format metric tons (e.g. 92900 → "92.9k t")
function fmtTons(v) {
  if (v == null) return '—'
  return `${(v / 1000).toFixed(1)}k t`
}

// Merge monthly supply rows with daily price rows by YYYY-MM prefix
function mergeData(supplyRows, priceRows) {
  // Build map: YYYY-MM → mean close for that month
  const priceByMonth = {}
  for (const p of priceRows) {
    const ym = p.date.slice(0, 7)
    if (!priceByMonth[ym]) priceByMonth[ym] = []
    priceByMonth[ym].push(p.close)
  }
  const monthMean = {}
  for (const [ym, closes] of Object.entries(priceByMonth)) {
    monthMean[ym] = closes.reduce((a, b) => a + b, 0) / closes.length
  }

  return supplyRows.map((r) => ({
    date: r.date,
    us_mine_production: r.us_mine_production,
    us_refined_production: r.us_refined_production,
    us_refined_stocks: r.us_refined_stocks,
    price: monthMean[r.date.slice(0, 7)] ?? null,
  }))
}

export default function CopperPanel() {
  const { data, loading, error } = useFetchWithError(`${API}/metals/copper?months=36`)

  if (error)
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">COPPER SUPPLY // FETCH ERROR</div>
      </div>
    )
  if (!data?.available && !loading) return null

  const rows = data?.data ?? []
  const latest = data?.latest
  const priceRows = data?.price ?? []

  const chartData = mergeData(rows, priceRows)

  // Latest values for headline
  const latestMine   = latest?.us_mine_production
  const latestStocks = latest?.us_refined_stocks
  const latestDate   = latest?.date

  // Most-recent price
  const latestPrice = priceRows.length > 0
    ? priceRows[priceRows.length - 1].close
    : null

  return (
    <Panel
      id="copper-supply"
      title="U.S. COPPER SUPPLY // USGS"
      info="Monthly U.S. copper mine production and refined stocks from the USGS Mineral Industry Surveys (public domain). Price overlay: COMEX copper front-month (HG=F, USD/lb) via yfinance. Mine production = recoverable copper, metric tons."
      collapsible
      headerRight={
        latestDate && (
          <span className="font-mono text-[9px] text-neutral-600">{latestDate.slice(0, 7)}</span>
        )
      }
    >
      {loading && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">
          Loading copper data…
        </div>
      )}

      {!loading && data?.available && latest && (
        <>
          {/* ── Hero numbers ── */}
          <div className="px-4 py-3 border-b border-border/30">
            <div className="flex items-baseline gap-4 flex-wrap">
              <div>
                <span className="font-mono text-2xl font-bold" style={{ color: COLOR_MINE }}>
                  {fmtTons(latestMine)}
                </span>
                <span className="font-mono text-[10px] text-neutral-600 ml-1.5">mine production</span>
              </div>
              <div>
                <span className="font-mono text-2xl font-bold" style={{ color: COLOR_STOCKS }}>
                  {fmtTons(latestStocks)}
                </span>
                <span className="font-mono text-[10px] text-neutral-600 ml-1.5">refined stocks</span>
              </div>
              {latestPrice != null && (
                <div>
                  <span className="font-mono text-xl font-bold" style={{ color: COLOR_PRICE }}>
                    ${latestPrice.toFixed(2)}
                  </span>
                  <span className="font-mono text-[10px] text-neutral-600 ml-1.5">/lb (COMEX)</span>
                </div>
              )}
            </div>
            <div className="font-mono text-[10px] text-neutral-600 mt-1">
              Latest: {latestDate} · {rows.length} monthly observations
            </div>
          </div>

          {/* ── Chart: mine production + stocks + price overlay ── */}
          {chartData.length > 1 && (
            <div className="px-2 py-2">
              <ResponsiveContainer width="100%" height={160}>
                <ComposedChart data={chartData} margin={{ top: 5, right: 40, bottom: 5, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" />
                  <XAxis
                    dataKey="date"
                    tick={{ fontSize: 8, fill: '#555', fontFamily: 'monospace' }}
                    tickFormatter={fmtDate}
                    interval="preserveStartEnd"
                    minTickGap={60}
                  />
                  {/* Left Y: supply (metric tons) */}
                  <YAxis
                    yAxisId="supply"
                    orientation="left"
                    tick={{ fontSize: 8, fill: '#55556688', fontFamily: 'monospace' }}
                    width={42}
                    tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`}
                  />
                  {/* Right Y: copper price USD/lb */}
                  <YAxis
                    yAxisId="price"
                    orientation="right"
                    tick={{ fontSize: 8, fill: '#a78bfa88', fontFamily: 'monospace' }}
                    width={36}
                    tickFormatter={(v) => `$${v.toFixed(2)}`}
                  />
                  <Tooltip
                    contentStyle={CHART_TOOLTIP_STYLE}
                    formatter={(v, name) => {
                      if (name === 'mine prod') return [`${Math.round(v).toLocaleString()} t`, name]
                      if (name === 'ref stocks') return [`${Math.round(v).toLocaleString()} t`, name]
                      if (name === 'price') return [`$${v.toFixed(2)}/lb`, name]
                      return [v, name]
                    }}
                    labelFormatter={fmtDate}
                  />
                  <Legend
                    iconSize={8}
                    wrapperStyle={{ fontFamily: 'monospace', fontSize: 9, paddingTop: 2 }}
                  />
                  <Line
                    yAxisId="supply"
                    type="monotone"
                    dataKey="us_mine_production"
                    name="mine prod"
                    stroke={COLOR_MINE}
                    strokeWidth={1.5}
                    dot={false}
                    activeDot={{ r: 3 }}
                  />
                  <Line
                    yAxisId="supply"
                    type="monotone"
                    dataKey="us_refined_stocks"
                    name="ref stocks"
                    stroke={COLOR_STOCKS}
                    strokeWidth={1.5}
                    dot={false}
                    activeDot={{ r: 3 }}
                    strokeDasharray="4 2"
                  />
                  <Line
                    yAxisId="price"
                    type="monotone"
                    dataKey="price"
                    name="price"
                    stroke={COLOR_PRICE}
                    strokeWidth={1}
                    dot={false}
                    activeDot={{ r: 3 }}
                    connectNulls
                  />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          )}
        </>
      )}

      <div className="px-4 pb-3 font-mono text-[8px] text-neutral-700">
        Source: USGS Mineral Industry Surveys (public domain) · Price: COMEX HG=F via yfinance
      </div>
      <TrackRecordBadge signal="copper_stocks" targetLabel="Copper" />
    </Panel>
  )
}
