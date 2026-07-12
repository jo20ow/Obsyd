import { useState } from 'react'
import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import { POLL_SLOW_MS } from '../utils/poll'
import { useViewState } from '../context/ViewStateContext'
import { rangeDays } from '../utils/ranges'
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceLine,
} from 'recharts'
import { fmtDate, fmtTs, CHART_TOOLTIP_STYLE } from '../utils/chart'

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

// Each border = one sparkline + headline bar. Grain-agnostic: `spark` is
// [{x, net_mw}] with x either a date string (daily) or an ISO timestamp (hourly).
function BorderRow({ label, netMw, direction, spark, xFormatter }) {
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
      {spark.length > 1 && (
        <ResponsiveContainer width="100%" height={36}>
          <LineChart data={spark} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
            <XAxis dataKey="x" hide />
            <YAxis hide />
            <ReferenceLine y={0} stroke="#2a2a3a" strokeDasharray="2 2" />
            <Tooltip
              contentStyle={CHART_TOOLTIP_STYLE}
              formatter={(v) => [`${(v / 1000).toFixed(2)} GW`, 'net']}
              labelFormatter={xFormatter}
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

function ModeToggle({ mode, onChange }) {
  return (
    <div className="flex gap-1">
      {['daily', 'hourly'].map((m) => (
        <button
          key={m}
          onClick={() => onChange(m)}
          className={`font-mono text-[9px] tracking-wider px-1.5 py-0.5 rounded border transition-colors ${
            mode === m
              ? 'border-cyan-glow/40 text-cyan-glow'
              : 'border-border text-neutral-600 hover:text-neutral-400'
          }`}
        >
          {m.toUpperCase()}
        </button>
      ))}
    </div>
  )
}

export default function CrossBorderFlowPanel({ zone = 'DE_LU' }) {
  const { range } = useViewState()
  const [mode, setMode] = useState('daily')
  const hourly = mode === 'hourly'
  const url = hourly
    ? `${API}/power/flows/hourly?zone=${zone}&hours=72`
    : `${API}/power/flows?days=${rangeDays(range)}`
  const { data, loading, error } = useFetchWithError(url, {
    deps: [range, zone, mode],
    pollMs: POLL_SLOW_MS,
  })

  const zl = zoneLabel(zone)

  if (error) {
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">
          CROSS-BORDER FLOWS // FETCH ERROR
        </div>
      </div>
    )
  }

  // Never vanish silently: a zone without flow series (Italian sub-zones,
  // DK1/DK2 …) gets the reason, not a blank spot in the grid section.
  if (!data?.available && !loading) {
    return (
      <div className="border border-border bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-neutral-500">
          CROSS-BORDER FLOWS · {zl} — {data?.reason || 'no flow data yet.'}
        </div>
      </div>
    )
  }

  // Daily mode: coherence with the zone selector — show the interconnectors
  // that touch the selected zone, not all 20 borders.
  const dailyBorders = hourly ? [] : (data?.borders ?? []).filter(
    (b) => b.from_zone === zone || b.to_zone === zone,
  )
  const dailyRows = data?.data ?? []
  const hourlyBorders = hourly ? (data?.borders ?? []) : []
  const borderCount = hourly ? hourlyBorders.length : dailyBorders.length

  return (
    <Panel
      id="cross-border-flows"
      freshness={data}
      title={`CROSS-BORDER FLOWS · ${zl}`}
      info={`Net physical electricity flows across ${zl}'s real interconnectors with its neighbours — sorted by magnitude. Positive = net export in the shown direction. Green = net exporter, orange = net importer. DAILY shows daily means over the selected window; HOURLY shows the last 72 hours from the canonical hourly store. Source: Fraunhofer ISE Energy-Charts (CC BY 4.0).`}
      collapsible
      headerRight={
        <div className="flex items-center gap-2">
          <ModeToggle mode={mode} onChange={setMode} />
          {borderCount > 0 && (
            <span className="font-mono text-[9px] text-neutral-600">
              {borderCount} borders · CBPF
            </span>
          )}
        </div>
      }
    >
      {loading && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">
          Loading cross-border flows…
        </div>
      )}

      {!loading && data?.available && !hourly && (
        <>
          {dailyBorders.map((border) => {
            const arrowKey = `${zoneLabel(border.from_zone)}→${zoneLabel(border.to_zone)}`
            const spark = dailyRows
              .filter((d) => d[arrowKey] != null)
              .map((d) => ({ x: d.date, net_mw: d[arrowKey] }))
            return (
              <BorderRow
                key={`${border.from_zone}-${border.to_zone}`}
                label={border.label}
                netMw={border.net_mw}
                direction={border.direction}
                spark={spark}
                xFormatter={fmtDate}
              />
            )
          })}
          <div className="px-4 py-2 font-mono text-[9px] text-neutral-700">
            daily mean MW · latest {data?.latest?.date}
          </div>
        </>
      )}

      {!loading && data?.available && hourly && (
        <>
          {hourlyBorders.map((b) => (
            <BorderRow
              key={b.neighbor}
              label={`${zl}↔${b.neighbor_label}`}
              netMw={b.latest_mw}
              direction={b.direction}
              spark={(b.data ?? []).map((p) => ({ x: p.ts_utc, net_mw: p.net_mw }))}
              xFormatter={fmtTs}
            />
          ))}
          <div className="px-4 py-2 font-mono text-[9px] text-neutral-700">
            hourly mean MW · last {data?.hours}h · {data?.note}
          </div>
        </>
      )}

      {!loading && data?.available && (
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
      )}
    </Panel>
  )
}
