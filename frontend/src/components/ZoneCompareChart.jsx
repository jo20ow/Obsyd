import { useMemo } from 'react'
import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid } from 'recharts'
import useFetchWithError from '../hooks/useFetchWithError'
import { useViewState } from '../context/ViewStateContext'
import { rangeStart } from '../utils/ranges'
import { CHART_TOOLTIP_PROPS, fmtDate } from '../utils/chart'

const API = '/api'

// One series, one chart, one big line for the zone you are looking at — and, only if you ask for
// them, up to three more zones drawn ON TOP of it. The Live grid used to render six zones as six
// small cards, each with its OWN y-axis (0-220 for DE-LU, 0-160 for FR, 0-240 for NL): they looked
// comparable and were not. A spread is a thing you see on one axis or you do not see at all.
const MAX_COMPARE = 3

// Colour is positional, not per-zone: the first compared zone is always pink, the second green,
// the third indigo. The primary keeps its section's colour.
const COMPARE_COLORS = ['#f472b6', '#4ade80', '#818cf8']

export default function ZoneCompareChart({ title, series, zone, compare = [], unit, scale = 1, color = '#22d3ee', labelFor }) {
  const { range } = useViewState()
  const start = rangeStart(range)

  const urlFor = (z) => `${API}/v1/series?series=${series}&zone=${z}&start=${start}&resolution=daily`

  // Hooks cannot run in a loop, so the compare slots are fixed. An unused slot points at the
  // PRIMARY zone's url — already in the SWR cache, so it costs no request (the SeriesExplorer
  // trick). MAX_COMPARE is therefore also the number of hooks below.
  const zones = [zone, ...compare.slice(0, MAX_COMPARE)]
  const slot = (i) => zones[i] ?? zone
  const r0 = useFetchWithError(urlFor(slot(0)), { deps: [series, slot(0), start] })
  const r1 = useFetchWithError(urlFor(slot(1)), { deps: [series, slot(1), start] })
  const r2 = useFetchWithError(urlFor(slot(2)), { deps: [series, slot(2), start] })
  const r3 = useFetchWithError(urlFor(slot(3)), { deps: [series, slot(3), start] })
  const responses = [r0, r1, r2, r3]

  const { rows, latest, hours } = useMemo(() => {
    const byDate = new Map()
    const latestByZone = {}
    const hoursByZone = {}
    zones.forEach((z, i) => {
      const points = responses[i].data?.data || []
      for (const p of points) {
        if (!byDate.has(p.date)) byDate.set(p.date, { t: p.date })
        byDate.get(p.date)[z] = p.value == null ? null : p.value * scale
      }
      const last = points.at(-1)
      latestByZone[z] = last?.value == null ? null : last.value * scale
      hoursByZone[z] = last?.hours ?? null
    })
    return {
      rows: [...byDate.values()].sort((a, b) => (a.t < b.t ? -1 : 1)),
      latest: latestByZone,
      hours: hoursByZone,
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [r0.data, r1.data, r2.data, r3.data, zones.join(','), scale])

  const loading = responses.slice(0, zones.length).some((r) => r.loading)
  const colorOf = (z, i) => (i === 0 ? color : COMPARE_COLORS[(i - 1) % COMPARE_COLORS.length])
  const label = (z) => (labelFor ? labelFor(z) : z)

  return (
    <div className="border border-border bg-surface rounded overflow-hidden shadow-sm">
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-border/50">
        <span className="font-mono text-[11px] font-medium text-neutral-300">{title}</span>
        <a
          href={`${urlFor(zone)}&format=csv`}
          title={`Download ${label(zone)} as CSV`}
          className="font-mono text-[9px] border border-border rounded px-1.5 py-0.5 text-neutral-500 hover:text-cyan-glow hover:border-cyan-glow/40 transition-colors shrink-0"
        >
          ↓ CSV
        </a>
      </div>

      {/* Legend: the zones on the chart, each with its latest value — and, when the last day is
          still filling in, the hours that mean actually averaged. */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 pt-2 font-mono text-[10px]">
        {zones.map((z, i) => (
          <span key={z} className="flex items-center gap-1.5">
            <span className="inline-block w-2.5 h-0.5" style={{ background: colorOf(z, i) }} />
            <span className="text-neutral-400">{label(z)}</span>
            {latest[z] != null ? (
              <span className="num font-bold" style={{ color: colorOf(z, i) }}>
                {latest[z].toFixed(1)} {unit}
              </span>
            ) : (
              <span className="text-neutral-600">no data</span>
            )}
            {hours[z] != null && hours[z] < 24 && (
              <span
                className="text-neutral-600"
                title={`The last day is still filling in: this mean averages ${hours[z]} of 24 hours.`}
              >
                {hours[z]}/24 h
              </span>
            )}
          </span>
        ))}
      </div>

      {loading && rows.length === 0 && (
        <div className="px-3 py-16 text-center font-mono text-[10px] text-neutral-600 animate-pulse">Loading…</div>
      )}
      {!loading && rows.length === 0 && (
        <div className="px-3 py-16 text-center font-mono text-[10px] text-neutral-600">No data for this zone.</div>
      )}
      {rows.length > 0 && (
        <div className="px-1 py-2">
          <ResponsiveContainer width="100%" height={280}>
            <LineChart data={rows} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" />
              <XAxis dataKey="t" tickFormatter={fmtDate} tick={{ fontSize: 9, fill: '#737373' }} minTickGap={40} />
              <YAxis tick={{ fontSize: 9, fill: '#737373' }} width={40} domain={['auto', 'auto']} />
              <Tooltip
                {...CHART_TOOLTIP_PROPS}
                labelFormatter={fmtDate}
                formatter={(v, name) => [v != null ? `${Number(v).toFixed(1)} ${unit}` : '—', label(name)]}
              />
              {zones.map((z, i) => (
                <Line
                  key={z}
                  type="monotone"
                  dataKey={z}
                  stroke={colorOf(z, i)}
                  dot={false}
                  strokeWidth={i === 0 ? 1.8 : 1.2}
                  connectNulls
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  )
}
