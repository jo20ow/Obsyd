import { useMemo } from 'react'
import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid } from 'recharts'
import useFetchWithError from '../hooks/useFetchWithError'
import { useViewState } from '../context/ViewStateContext'
import { rangeStart } from '../utils/ranges'
import { CHART_TOOLTIP_STYLE, fmtDate } from '../utils/chart'

const API = '/api'

// Compact single-series line card for the Live multi-zone grid (gridstatus 3-up
// style). Title + latest value + CSV download; reads one /api/v1/series over the
// global range. No fixed DOM id → safe to render many instances side by side.
export default function MiniSeriesCard({ title, series, zone, unit, scale = 1, color = '#22d3ee' }) {
  const { range } = useViewState()
  const start = rangeStart(range)
  const url = `${API}/v1/series?series=${series}&zone=${zone}&start=${start}&resolution=daily`
  const { data: resp, loading } = useFetchWithError(url, { deps: [series, zone, start] })

  const rows = useMemo(
    () => (resp?.data || []).map((p) => ({
      t: p.date,
      v: p.value == null ? null : p.value * scale,
      hours: p.hours,
    })),
    [resp, scale],
  )
  const last = rows.length ? rows[rows.length - 1] : null
  const latest = last ? last.v : null
  // The last day of a live series is usually a stump — the hours that have elapsed, not a day.
  // Printing its mean as "the price" is how the desk came to show 132.6 next to the panel's 123.8.
  const partialHours = last && last.hours != null && last.hours < 24 ? last.hours : null

  return (
    <div className="border border-border bg-surface rounded overflow-hidden shadow-sm">
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-border/50">
        <span className="font-mono text-[11px] font-medium text-neutral-300 truncate">{title}</span>
        <div className="flex items-center gap-2 shrink-0">
          {latest != null && (
            <span className="num text-[11px] font-bold text-cyan-glow">{latest.toFixed(1)} {unit}</span>
          )}
          {partialHours != null && (
            <span
              className="font-mono text-[9px] text-neutral-500"
              title={`The last day is still filling in: this mean averages ${partialHours} of 24 hours.`}
            >
              {partialHours}/24 h
            </span>
          )}
          <a
            href={`${url}&format=csv`}
            title="Download CSV"
            className="font-mono text-[9px] border border-border rounded px-1.5 py-0.5 text-neutral-500 hover:text-cyan-glow hover:border-cyan-glow/40 transition-colors"
          >
            ↓
          </a>
        </div>
      </div>
      {loading && <div className="px-3 py-8 text-center font-mono text-[10px] text-neutral-600 animate-pulse">Loading…</div>}
      {!loading && rows.length > 0 && (
        <div className="px-1 py-2">
          <ResponsiveContainer width="100%" height={120}>
            <LineChart data={rows} margin={{ top: 5, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" />
              <XAxis dataKey="t" tickFormatter={fmtDate} tick={{ fontSize: 8, fill: '#737373' }} minTickGap={40} />
              <YAxis tick={{ fontSize: 8, fill: '#737373' }} width={34} domain={['auto', 'auto']} />
              <Tooltip {...CHART_TOOLTIP_STYLE} labelFormatter={fmtDate}
                formatter={(v) => [v != null ? `${Number(v).toFixed(1)} ${unit}` : '—', title]} />
              <Line type="monotone" dataKey="v" stroke={color} dot={false} strokeWidth={1.4} connectNulls />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
      {!loading && rows.length === 0 && (
        <div className="px-3 py-8 text-center font-mono text-[10px] text-neutral-600">No data for this zone.</div>
      )}
    </div>
  )
}
