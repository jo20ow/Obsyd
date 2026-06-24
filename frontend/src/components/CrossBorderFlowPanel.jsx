import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceLine,
} from 'recharts'
import { fmtDate, CHART_TOOLTIP_STYLE } from '../utils/chart'

const API = '/api'

// Color palette: green = export, orange = import, neutral = near-zero
const COLOR_EXPORT = '#22d3ee'  // cyan – net exporter
const COLOR_IMPORT = '#fb923c'  // orange – net importer
const COLOR_ZERO   = '#94a3b8'  // slate – near-zero or missing

function borderColor(netMw) {
  if (netMw == null) return COLOR_ZERO
  if (netMw > 0) return COLOR_EXPORT
  if (netMw < 0) return COLOR_IMPORT
  return COLOR_ZERO
}

// Each border = one sparkline + headline bar
function BorderRow({ border, data }) {
  const { label, net_mw: netMw, direction, from_zone, to_zone } = border

  // Pick the column key for this border: "DE-LU→FR" or similar
  // The API builds arrows like "DE-LU→FR" from zone labels.
  // `direction` is the ACTUAL direction for the latest day.
  // For the sparkline we want the signed net_mw values.
  const fromLabel = from_zone === 'DE_LU' ? 'DE-LU' : from_zone
  const toLabel   = to_zone   === 'DE_LU' ? 'DE-LU' : to_zone
  const arrowKey  = `${fromLabel}→${toLabel}`

  const sparkData = data
    .filter((d) => d[arrowKey] != null)
    .map((d) => ({ date: d.date, net_mw: d[arrowKey] }))

  const absGW = netMw != null ? Math.abs(netMw / 1000).toFixed(2) : '—'
  const color = borderColor(netMw)

  // Bar width: scale relative to ±5 GW max visible
  const MAX_BAR_GW = 5
  const barPct = netMw != null
    ? Math.min(Math.abs(netMw) / (MAX_BAR_GW * 1000), 1) * 100
    : 0
  const barLeft = netMw != null && netMw < 0  // bar grows left from center

  return (
    <div className="px-4 py-2.5 border-b border-border/30 last:border-0">
      {/* Label + headline */}
      <div className="flex items-center justify-between gap-2 mb-1">
        <span className="font-mono text-[10px] text-neutral-500 shrink-0">{label}</span>
        <div className="flex items-center gap-2">
          <span className="font-mono text-[10px]" style={{ color }}>
            {absGW} GW
          </span>
          <span className="font-mono text-[9px] text-neutral-600">
            {direction}
          </span>
        </div>
      </div>

      {/* Signed horizontal bar */}
      <div className="flex items-center gap-1 mb-1.5">
        {/* Left half */}
        <div className="flex-1 h-1.5 bg-neutral-900 rounded-l overflow-hidden flex justify-end">
          {barLeft && (
            <div
              className="h-full rounded-l transition-all"
              style={{ width: `${barPct}%`, background: color }}
            />
          )}
        </div>
        {/* Centre tick */}
        <div className="w-px h-2.5 bg-neutral-700 shrink-0" />
        {/* Right half */}
        <div className="flex-1 h-1.5 bg-neutral-900 rounded-r overflow-hidden">
          {!barLeft && netMw != null && netMw > 0 && (
            <div
              className="h-full rounded-r transition-all"
              style={{ width: `${barPct}%`, background: color }}
            />
          )}
        </div>
      </div>

      {/* Sparkline */}
      {sparkData.length > 1 && (
        <ResponsiveContainer width="100%" height={36}>
          <LineChart data={sparkData} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
            <XAxis dataKey="date" hide />
            <YAxis hide />
            <ReferenceLine y={0} stroke="#2a2a3a" strokeDasharray="2 2" />
            <Tooltip
              contentStyle={CHART_TOOLTIP_STYLE}
              formatter={(v) => [`${(v / 1000).toFixed(2)} GW`, 'net']}
              labelFormatter={fmtDate}
            />
            <Line
              type="monotone"
              dataKey="net_mw"
              stroke={color}
              strokeWidth={1}
              dot={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}

export default function CrossBorderFlowPanel() {
  const url = `${API}/power/flows?days=30`
  const { data, loading, error } = useFetchWithError(url)

  if (error) {
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">
          CROSS-BORDER FLOWS // FETCH ERROR
        </div>
      </div>
    )
  }

  if (!data?.available && !loading) return null

  const borders = data?.borders ?? []
  const rows = data?.data ?? []

  return (
    <Panel
      id="cross-border-flows"
      title="CROSS-BORDER FLOWS"
      info="Net physical electricity flows between bidding zones (ENTSO-E A11, Actual Cross-Border Physical Flow). Daily mean MW — positive = net export in the shown direction. Green = net exporter, orange = net importer. Sparkline shows the last 30 days signed."
      collapsible
      headerRight={
        borders.length > 0 && (
          <span className="font-mono text-[9px] text-neutral-600">
            {borders.length} borders · A11
          </span>
        )
      }
    >
      {loading && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">
          Loading cross-border flows…
        </div>
      )}

      {!loading && data?.available && (
        <>
          {borders.map((border) => (
            <BorderRow
              key={`${border.from_zone}-${border.to_zone}`}
              border={border}
              data={rows}
            />
          ))}
          <div className="px-4 py-2 font-mono text-[9px] text-neutral-700">
            ENTSO-E A11 · daily mean MW · latest {data?.latest?.date}
          </div>
        </>
      )}
    </Panel>
  )
}
