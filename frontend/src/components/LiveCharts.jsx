import { useEffect, useState } from 'react'
import useZones from '../hooks/useZones'
import { useViewState } from '../context/ViewStateContext'
import MiniMixCard from './MiniMixCard'
import ZoneCompareChart from './ZoneCompareChart'

// The metric you want, for the zone you are looking at — big. Other zones join it only when you
// ask for them (and then, for the line series, IN THE SAME CHART: six small cards with six
// different y-axes looked comparable and were not; the fuel mix stays side by side, because a
// stacked area cannot be laid over another).
//
// The zone is the global one (ViewState), so the whole desk turns together. Comparisons live in
// the URL (?cmp=FR,NL) — a chart worth showing someone is worth linking to.
const CORE = ['DE_LU', 'FR', 'NL', 'BE', 'ES', 'AT']
const MAX_COMPARE = 3

const SECTIONS = [
  { key: 'prices', label: 'Prices', kind: 'series', series: 'price.dayahead', unit: '€/MWh', scale: 1, color: '#22d3ee' },
  { key: 'mix', label: 'Fuel Mix', kind: 'mix' },
  { key: 'load', label: 'Load', kind: 'series', series: 'load.actual', unit: 'GW', scale: 1 / 1000, color: '#a78bfa' },
  { key: 'residual', label: 'Residual', kind: 'series', series: 'residual.actual', unit: 'GW', scale: 1 / 1000, color: '#f59e0b' },
]

function readCompare() {
  if (typeof window === 'undefined') return []
  const raw = new URLSearchParams(window.location.search).get('cmp')
  return raw ? raw.split(',').filter(Boolean).slice(0, MAX_COMPARE) : []
}

export default function LiveCharts() {
  const [section, setSection] = useState('prices')
  const [resolution, setResolution] = useState('daily')  // series view: daily trend vs today's hourly shape
  const [compare, setCompare] = useState(readCompare)
  const { zone } = useViewState()
  const { zones } = useZones()

  const keys = new Set(zones.map((z) => z.key))
  const labelFor = (k) => zones.find((z) => z.key === k)?.label || k
  const s = SECTIONS.find((x) => x.key === section) || SECTIONS[0]

  // The primary zone can never also be a comparison — switching the desk to FR while comparing
  // against FR would draw it twice.
  const selected = compare.filter((z) => z !== zone && keys.has(z))

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (selected.length) params.set('cmp', selected.join(','))
    else params.delete('cmp')
    const qs = params.toString()
    history.replaceState(null, '', `${window.location.pathname}${qs ? `?${qs}` : ''}${window.location.hash}`)
  }, [selected.join(',')]) // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = (z) =>
    setCompare((prev) =>
      prev.includes(z) ? prev.filter((k) => k !== z) : prev.length >= MAX_COMPARE ? prev : [...prev, z],
    )

  const chips = CORE.filter((k) => keys.has(k) && k !== zone)
  const rest = zones.filter((z) => z.key !== zone && !CORE.includes(z.key) && !selected.includes(z.key))
  const full = selected.length >= MAX_COMPARE

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-1 border-b border-border overflow-x-auto scrollbar-hidden">
        {SECTIONS.map((x) => (
          <button
            key={x.key}
            onClick={() => setSection(x.key)}
            className={`font-mono text-[11px] px-3 py-2 -mb-px border-b-2 shrink-0 transition-colors ${
              section === x.key ? 'text-cyan-glow border-cyan-glow' : 'text-neutral-500 border-transparent hover:text-neutral-300'
            }`}
          >
            {x.label}
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-1.5 font-mono text-[10px]">
        <span className="text-neutral-500 pr-1">Compare {labelFor(zone)} with:</span>
        {chips.map((z) => {
          const on = selected.includes(z)
          return (
            <button
              key={z}
              onClick={() => toggle(z)}
              disabled={!on && full}
              className={`px-2 py-0.5 rounded border transition-colors ${
                on
                  ? 'border-cyan-glow/50 text-cyan-glow bg-cyan-glow/10'
                  : full
                    ? 'border-border text-neutral-700 cursor-not-allowed'
                    : 'border-border text-neutral-400 hover:text-neutral-200 hover:border-neutral-600'
              }`}
            >
              {on ? '✓ ' : '+ '}{labelFor(z)}
            </button>
          )
        })}
        {/* Everything outside the six core zones — the record has 37 of them. */}
        <select
          value=""
          disabled={full || rest.length === 0}
          onChange={(e) => e.target.value && toggle(e.target.value)}
          className="bg-transparent border border-border rounded px-1.5 py-0.5 text-neutral-400 disabled:text-neutral-700"
        >
          <option value="">+ more…</option>
          {rest.map((z) => (
            <option key={z.key} value={z.key}>{z.label || z.key}</option>
          ))}
        </select>
        {/* Compared zones outside the core set need a way back off the chart. */}
        {selected.filter((z) => !CORE.includes(z)).map((z) => (
          <button
            key={z}
            onClick={() => toggle(z)}
            className="px-2 py-0.5 rounded border border-cyan-glow/50 text-cyan-glow bg-cyan-glow/10"
          >
            ✓ {labelFor(z)} ×
          </button>
        ))}
        {full && <span className="text-neutral-600">3 is the limit — four lines is a chart, five is a thicket</span>}
        {/* Daily trend vs the intraday shape — the hourly view is where a zone's
            morning solar dip (FR to €0) or evening peak actually shows. */}
        {s.kind === 'series' && (
          <div className="ml-auto flex items-center gap-0.5 shrink-0">
            {['daily', 'hourly'].map((r) => (
              <button
                key={r}
                onClick={() => setResolution(r)}
                className={`px-2 py-0.5 rounded border transition-colors capitalize ${
                  resolution === r
                    ? 'border-cyan-glow/50 text-cyan-glow bg-cyan-glow/10'
                    : 'border-border text-neutral-500 hover:text-neutral-300'
                }`}
              >
                {r === 'hourly' ? 'Hourly · 3d' : 'Daily'}
              </button>
            ))}
          </div>
        )}
      </div>

      {s.kind === 'mix' ? (
        <div className={`grid gap-3 items-start ${selected.length ? 'grid-cols-1 md:grid-cols-2' : 'grid-cols-1'}`}>
          {[zone, ...selected].map((z) => (
            <MiniMixCard
              key={z}
              title={`Fuel Mix · ${labelFor(z)}`}
              zone={z}
              height={selected.length ? 160 : 260}
            />
          ))}
        </div>
      ) : (
        <ZoneCompareChart
          title={`${s.label} · ${labelFor(zone)}${selected.length ? ` vs ${selected.map(labelFor).join(', ')}` : ''}`}
          series={s.series}
          zone={zone}
          compare={selected}
          unit={s.unit}
          scale={s.scale}
          color={s.color}
          labelFor={labelFor}
          resolution={resolution}
        />
      )}
    </div>
  )
}
