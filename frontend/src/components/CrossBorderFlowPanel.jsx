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

// Derive the arrow key for the wide-format data lookup.
// The API builds arrows from _zone_label(from_zone) + "→" + _zone_label(to_zone).
// DE_LU → "DE-LU", everything else → the zone code as-is.
function zoneLabel(zone) {
  return zone === 'DE_LU' ? 'DE-LU' : zone
}

// Each border = one sparkline + headline bar
function BorderRow({ border, data }) {
  const { label, net_mw: netMw, direction, from_zone, to_zone } = border

  const arrowKey = `${zoneLabel(from_zone)}→${zoneLabel(to_zone)}`

  const sparkData = data
    .filter((d) => d[arrowKey] != null)
    .map((d) => ({ date: d.date, net_mw: d[arrowKey] }))

  const absGW = netMw != null ? Math.abs(netMw / 1000).toFixed(2) : '—'
  const color = borderColor(netMw)

  // Bar width: scale relative to ±10 GW max visible (more borders → larger range)
  const MAX_BAR_GW = 10
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
      info="Net physical electricity flows between bidding zones. All real interconnectors of DE-LU, FR, and NL — sorted by magnitude. Daily mean MW — positive = net export in the shown direction. Green = net exporter, orange = net importer. Sparkline shows the last 30 days signed. Source: Fraunhofer ISE Energy-Charts (CC BY 4.0)."
      collapsible
      headerRight={
        borders.length > 0 && (
          <span className="font-mono text-[9px] text-neutral-600">
            {borders.length} borders · CBPF
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
            daily mean MW · latest {data?.latest?.date}
          </div>
          <div className="px-4 pb-2 font-mono text-[9px] text-neutral-700">
            Source:{' '}
            <a
              href="https://www.energy-charts.info"
              target="_blank"
              rel="noopener noreferrer"
              className="underline hover:text-neutral-500"
            >
              Energy-Charts
            </a>
            {' '}(CC BY 4.0)
          </div>
        </>
      )}
    </Panel>
  )
}
