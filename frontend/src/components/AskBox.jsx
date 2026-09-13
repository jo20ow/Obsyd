import { useEffect, useState } from 'react'
import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid, Legend,
} from 'recharts'
import Panel from './Panel'
import MultiTip from './ChartTip'
import { useChartTheme } from '../utils/chart'
import { fuelColor } from '../utils/fuels'

const API = '/api'

// Fixed categorical set for zone-comparison bars — assigned by pick order,
// never re-painted when a place is removed.
const ZONE_COLORS = ['#1d4ed8', '#f59e0b', '#10b981', '#8b5cf6', '#ef4444', '#0ea5e9', '#a3a3a3']

const selectCls = 'bg-surface border border-border rounded px-2 py-1.5 font-mono text-[11px] text-neutral-200 focus:border-cyan-glow/40 outline-none'

/**
 * ASK as a FILTER, not a search engine (owner direction: a free-text box puts
 * the burden of guessing valid phrasings on the reader — dropdowns show what
 * exists). Metric · places · years; the answer updates as you change them.
 * Options come from GET /api/v1/ask (bare) so the backend's metric table
 * stays the single source. PREMIUM preview: the mount probe 401s for
 * non-pro sessions and the component renders nothing.
 */
export default function AskBox() {
  const [filters, setFilters] = useState(null) // null=probing, false=hidden
  const [metric, setMetric] = useState('')
  const [places, setPlaces] = useState([]) // labels of picked places
  const [yearFrom, setYearFrom] = useState('')
  const [yearTo, setYearTo] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const ct = useChartTheme()

  useEffect(() => {
    let dead = false
    fetch(`${API}/v1/ask`, { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.status))))
      .then((d) => { if (!dead) setFilters(d.filters || false) })
      .catch(() => { if (!dead) setFilters(false) })
    return () => { dead = true }
  }, [])

  const metricDef = filters && filters.metrics?.find((m) => m.id === metric)
  const isMix = metricDef?.columns === 'fuels'

  // The filter answers live: any complete selection fetches.
  useEffect(() => {
    if (!filters || !metric || places.length === 0) { setResult(null); return }
    const zones = places
      .flatMap((label) => filters.places.find((p) => p.label === label)?.zones || [])
    const params = new URLSearchParams({ metric, zones: zones.join(',') })
    if (yearFrom) params.set('from', yearFrom)
    params.set('to', yearTo || String(filters.years.max))
    const controller = new AbortController()
    setBusy(true)
    fetch(`${API}/v1/ask?${params}`, { credentials: 'include', signal: controller.signal })
      .then((r) => r.json())
      .then(setResult)
      .catch((e) => { if (e.name !== 'AbortError') setResult({ available: false, message: String(e) }) })
      .finally(() => setBusy(false))
    return () => controller.abort()
  }, [filters, metric, places, yearFrom, yearTo])

  if (filters === null || filters === false) return null

  const years = []
  for (let y = filters.years.max; y >= filters.years.min; y--) years.push(y)

  const addPlace = (label) => {
    if (!label) return
    // The mix answers one place at a time — picking replaces instead of adding.
    setPlaces((ps) => (isMix ? [label] : ps.includes(label) ? ps : [...ps, label].slice(0, 6)))
  }

  const columns = result?.columns || []
  const labels = result?.column_labels || {}
  const fuelsMode = result?.column_kind === 'fuels'

  return (
    <Panel
      id="ask"
      source="Deterministic filter + declared per-metric aggregation — no LLM, no guessing"
      title="ASK · PICK A METRIC, GET THE ANSWER"
      info="Pick a metric, one or more places and a time span — the answer updates as you go: a takeaway sentence, the chart, and the honest edges (years outside the record are named, the running period is flagged as partial). Counts are totalled per period, prices and levels averaged; the chips above every answer show exactly how it was computed. Generation mix answers one place at a time, split by fuel. Descriptive, not a forecast."
      collapsible
    >
      <div className="px-4 pt-3 pb-1 flex flex-wrap items-center gap-2">
        <select className={selectCls} value={metric}
          onChange={(e) => { setMetric(e.target.value); if (e.target.value && filters.metrics.find((m) => m.id === e.target.value)?.columns === 'fuels') setPlaces((ps) => ps.slice(0, 1)) }}>
          <option value="">Metric…</option>
          {filters.metrics.map((m) => (
            <option key={m.id} value={m.id}>{m.label}</option>
          ))}
        </select>

        <select className={selectCls} value=""
          onChange={(e) => addPlace(e.target.value)}>
          <option value="">{isMix ? 'Place…' : places.length ? 'Add place…' : 'Place…'}</option>
          {filters.places.map((p) => (
            <option key={p.label} value={p.label}>{p.label}</option>
          ))}
        </select>

        <select className={selectCls} value={yearFrom} onChange={(e) => setYearFrom(e.target.value)}>
          <option value="">record start</option>
          {years.map((y) => <option key={y} value={y}>{y}</option>)}
        </select>
        <span className="font-mono text-[10px] text-neutral-600">→</span>
        <select className={selectCls} value={yearTo || String(filters.years.max)}
          onChange={(e) => setYearTo(e.target.value)}>
          {years.map((y) => <option key={y} value={y}>{y}</option>)}
        </select>
        {busy && <span className="font-mono text-[10px] text-neutral-600 animate-pulse">…</span>}
      </div>

      {places.length > 0 && (
        <div className="px-4 pb-1 flex flex-wrap gap-1.5">
          {places.map((label) => (
            <button key={label} onClick={() => setPlaces((ps) => ps.filter((x) => x !== label))}
              title="Remove"
              className="font-mono text-[10px] px-2 py-0.5 rounded border border-cyan-glow/30 text-cyan-glow/90 hover:border-red-400/50 hover:text-red-400">
              {label} ✕
            </button>
          ))}
        </div>
      )}

      {!metric && (
        <div className="px-4 py-3 font-mono text-[10px] text-neutral-600">
          e.g. Generation mix · Germany · 2018 → {filters.years.max}, or Negative-price
          hours · Finland · 2021 → {filters.years.max}.
        </div>
      )}

      {result && !result.available && (
        <div className="px-4 py-3 space-y-1">
          <div className="font-mono text-[11px] text-amber-400">
            {result.message || 'No data for that pick.'}
          </div>
          {(result.coverage || []).map((c) => (
            <div key={c} className="font-mono text-[10px] text-amber-400/80">{c}</div>
          ))}
        </div>
      )}

      {result?.available && (
        <div className="px-4 py-2 space-y-2">
          {/* How the answer was computed — always visible, never implicit. */}
          <div className="flex flex-wrap gap-1.5 font-mono text-[9px]">
            {[
              result.interpreted.metric,
              (result.interpreted.zones || []).map((z) => labels[z] || z).join(' vs ') || places.join(' vs '),
              `${result.interpreted.from ?? 'record start'}–${result.interpreted.to}`,
              `${result.interpreted.grain} · ${result.interpreted.aggregation}`,
            ].map((chip) => (
              <span key={chip} className="px-1.5 py-0.5 rounded border border-cyan-glow/30 text-cyan-glow/90">
                {chip}
              </span>
            ))}
          </div>

          <div className="border-l-2 border-cyan-glow/50 pl-3 font-mono text-[12px] text-neutral-200">
            {result.sentence}
          </div>

          {result.rows?.length > 0 && (
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={result.rows} margin={{ top: 5, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid {...ct.grid} />
                <XAxis dataKey="period" tick={ct.tick} />
                <YAxis tick={ct.tick} width={54} />
                <Tooltip content={<MultiTip />} formatter={(v, n) => [
                  `${Number(v).toLocaleString()} ${result.unit}`, labels[n] || n,
                ]} />
                {(fuelsMode ? columns.length > 1 : columns.length > 1) && (
                  <Legend wrapperStyle={{ fontSize: 9, fontFamily: 'monospace' }} iconSize={7}
                    formatter={(n) => labels[n] || n} />
                )}
                {columns.map((c, i) => (
                  <Bar key={c} dataKey={c} name={c}
                    stackId={fuelsMode ? 'mix' : undefined}
                    fill={fuelsMode ? fuelColor(c) : ZONE_COLORS[i % ZONE_COLORS.length]}
                    fillOpacity={0.8}
                    isAnimationActive={false} />
                ))}
              </BarChart>
            </ResponsiveContainer>
          )}

          {(result.coverage || []).map((c) => (
            <div key={c} className="font-mono text-[10px] text-amber-400/80">{c}</div>
          ))}
          {result.partial_period && (
            <div className="font-mono text-[10px] text-neutral-500">
              {result.partial_period} is still running — a partial period, not a full one.
            </div>
          )}
          <div className="flex items-center gap-3 font-mono text-[9px] text-neutral-600">
            <span>{result.note}</span>
            {result.download_url && (
              <a href={result.download_url} className="text-cyan-glow/80 hover:underline shrink-0">
                ↓ CSV
              </a>
            )}
          </div>
        </div>
      )}
    </Panel>
  )
}
