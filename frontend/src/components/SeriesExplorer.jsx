import { useEffect, useMemo, useState } from 'react'
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, ReferenceLine,
} from 'recharts'
import { InfoPopover } from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import { useViewState } from '../context/ViewStateContext'
import { rangeStart } from '../utils/ranges'
import { CHART_TOOLTIP_STYLE } from '../utils/chart'

const API = '/api'
const COLOR_A = '#22d3ee'
const COLOR_B = '#a78bfa'

// Friendly names for the canonical series the desk itself charts; everything
// else (gen.<fuel>, flow.<zone>) gets a generated label so the raw-key dump
// the catalog returns stays readable.
const SERIES_LABELS = {
  'price.dayahead': 'Day-ahead price · hourly',
  'price.dayahead.qh': 'Day-ahead price · 15-min',
  'imbalance.price': 'Imbalance price · hourly',
  'imbalance.price.qh': 'Imbalance price · 15-min',
  'load.actual': 'Load · actual',
  'load.forecast': 'Load · TSO forecast',
  'residual.actual': 'Residual load · actual',
  'residual.forecast': 'Residual load · TSO forecast',
  'generation.forecast': 'Generation · TSO forecast',
  'hydro.reservoir': 'Hydro reservoir filling · weekly',
  'wind.actual': 'Wind · actual',
  'solar.actual': 'Solar · actual',
}

const GROUP_ORDER = ['price', 'imbalance', 'load', 'residual', 'generation', 'wind', 'solar', 'gen', 'flow', 'hydro']
const GROUP_LABELS = {
  price: 'Prices', imbalance: 'Imbalance', load: 'Load', residual: 'Residual load',
  generation: 'Generation forecast', wind: 'Wind', solar: 'Solar',
  gen: 'Generation mix (per fuel)', flow: 'Cross-border flows (hourly)', hydro: 'Hydro',
}

function seriesLabel(s) {
  if (SERIES_LABELS[s.key]) return SERIES_LABELS[s.key]
  if (s.key.startsWith('flow.')) return `Flow ↔ ${s.key.slice(5).replace('_', '-')}`
  if (s.key.startsWith('gen.')) return `Generation · ${s.key.slice(4)}`
  return s.key
}

function groupedSeries(seriesList) {
  const groups = new Map()
  for (const s of seriesList) {
    const prefix = s.key.split('.')[0]
    if (!groups.has(prefix)) groups.set(prefix, [])
    groups.get(prefix).push(s)
  }
  const order = [...GROUP_ORDER.filter((g) => groups.has(g)), ...[...groups.keys()].filter((g) => !GROUP_ORDER.includes(g))]
  return order.map((g) => ({
    group: GROUP_LABELS[g] || g,
    items: groups.get(g).slice().sort((a, b) => a.key.localeCompare(b.key)),
  }))
}

// Explorer selection is shareable: s/vs/res live in the URL query (zone+range
// already travel via the global ViewState spine). replaceState so browsing the
// catalog doesn't spam the history stack.
function readParam(name, fallback) {
  if (typeof window === 'undefined') return fallback
  return new URLSearchParams(window.location.search).get(name) || fallback
}

export default function SeriesExplorer() {
  const [series, setSeries] = useState(() => readParam('s', 'price.dayahead'))
  const { zone, setZone, range } = useViewState()  // primary zone + range = the global spine
  const [compareZone, setCompareZone] = useState(() => readParam('vs', ''))  // '' = off
  const [spread, setSpread] = useState(false)  // Δ (A−B) instead of two lines
  const [resolution, setResolution] = useState(() => readParam('res', 'daily'))

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const setOrDelete = (k, v, def) => (v && v !== def ? params.set(k, v) : params.delete(k))
    setOrDelete('s', series, 'price.dayahead')
    setOrDelete('vs', compareZone, '')
    setOrDelete('res', resolution, 'daily')
    const qs = params.toString()
    history.replaceState(null, '', `${window.location.pathname}${qs ? `?${qs}` : ''}${window.location.hash}`)
  }, [series, compareZone, resolution])

  const { data: catalog } = useFetchWithError(`${API}/v1/series/catalog`)
  const start = rangeStart(range)

  const enc = encodeURIComponent(series)
  const url = `${API}/v1/series?series=${enc}&zone=${zone}&start=${start}&resolution=${resolution}`
  const { data: resp, loading } = useFetchWithError(url, { deps: [series, zone, start, resolution] })

  const comparing = !!compareZone && compareZone !== zone
  const cmpZoneEff = compareZone || zone  // when off, same URL as primary → served from SWR cache (no extra fetch)
  const cmpUrl = `${API}/v1/series?series=${enc}&zone=${cmpZoneEff}&start=${start}&resolution=${resolution}`
  const { data: cmpResp } = useFetchWithError(cmpUrl, { deps: [series, cmpZoneEff, start, resolution] })

  const tkey = resolution === 'daily' ? 'date' : 'datetime_utc'
  const data = useMemo(() => {
    const base = (resp?.data || []).map((p) => ({ t: p[tkey], a: p.value }))
    if (!comparing) return base
    const bByT = new Map((cmpResp?.data || []).map((p) => [p[tkey], p.value]))
    return base.map((row) => {
      const b = bByT.get(row.t) ?? null
      const d = row.a != null && b != null ? Math.round((row.a - b) * 100) / 100 : null
      return { ...row, b, d }
    })
  }, [resp, cmpResp, comparing, tkey])
  const showSpread = comparing && spread

  const csvUrl = `${url}&format=csv`
  const seriesList = catalog?.series || [{ key: 'price.dayahead', unit: 'EUR/MWh' }]
  const zoneList = catalog?.zones || [{ key: 'DE_LU', label: 'DE-LU' }]
  const zoneLabel = (k) => zoneList.find((z) => z.key === k)?.label || k
  const fmtT = (t) => (resolution === 'daily' ? t : String(t).slice(5, 16).replace('T', ' '))

  return (
    <div className="border border-border bg-surface rounded overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 border-b border-border">
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs text-neutral-500 tracking-wider">SERIES EXPLORER · /api/v1</span>
          <InfoPopover text="Query any series for any zone over the canonical hourly store via the public data API (GET /api/v1/series). Pick a series, a zone (optionally a second zone to compare), a range and resolution; download the exact query as CSV. Free, official, redistributable data — descriptive, not a forecast." />
        </div>
        <a
          href={csvUrl}
          className="font-mono text-[10px] tracking-wider border border-border rounded px-2 py-1 text-neutral-400 hover:text-cyan-glow hover:border-cyan-glow/40 transition-colors"
        >
          ↓ CSV
        </a>
      </div>

      <div className="flex flex-wrap items-center gap-2 px-4 py-2.5 border-b border-border/50">
        <select value={series} onChange={(e) => setSeries(e.target.value)}
          className="font-mono text-[11px] bg-[#0a0a12] border border-border rounded px-2 py-1 text-neutral-300">
          {groupedSeries(seriesList).map(({ group, items }) => (
            <optgroup key={group} label={group}>
              {items.map((s) => (
                <option key={s.key} value={s.key}>
                  {seriesLabel(s)}{s.unit ? ` (${s.unit})` : ''}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        <select value={zone} onChange={(e) => setZone(e.target.value)}
          className="font-mono text-[11px] bg-[#0a0a12] border border-cyan-500/40 text-cyan-300 rounded px-2 py-1">
          {zoneList.map((z) => <option key={z.key} value={z.key}>{z.label || z.key}</option>)}
        </select>
        <select value={compareZone} onChange={(e) => setCompareZone(e.target.value)}
          className="font-mono text-[11px] bg-[#0a0a12] border border-violet-400/40 text-violet-300 rounded px-2 py-1">
          <option value="">vs … compare</option>
          {zoneList.filter((z) => z.key !== zone).map((z) => <option key={z.key} value={z.key}>vs {z.label || z.key}</option>)}
        </select>
        {comparing && (
          <button onClick={() => setSpread((s) => !s)}
            className={`font-mono text-[9px] px-2 py-0.5 rounded border ${spread ? 'text-amber-300 border-amber-400/40 bg-amber-400/10' : 'text-neutral-500 border-border hover:text-neutral-300'}`}
            title="Show the A−B spread instead of two lines">
            Δ A−B
          </button>
        )}
        <div className="flex items-center gap-1">
          {['hourly', 'daily'].map((rz) => (
            <button key={rz} onClick={() => setResolution(rz)}
              className={`font-mono text-[9px] px-2 py-0.5 rounded border ${resolution === rz ? 'text-violet-300 border-violet-400/40 bg-violet-400/10' : 'text-neutral-500 border-border hover:text-neutral-300'}`}>
              {rz.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      <div className="px-2 pt-3 pb-1" style={{ minHeight: 240 }}>
        {loading && <div className="px-4 py-10 text-center font-mono text-[10px] text-neutral-600 animate-pulse">Loading…</div>}
        {!loading && resp && resp.available === false && (
          <div className="px-4 py-10 text-center font-mono text-[10px] text-neutral-600">
            {resp.reason || 'No data for this selection.'}
          </div>
        )}
        {!loading && data.length > 0 && (
          <>
            <div className="px-2 pb-2 flex items-center gap-3 font-mono text-[10px] text-neutral-500">
              <span>{resp.count} points · {resp.unit || ''} · {resolution}</span>
              {showSpread ? (
                <span className="flex items-center gap-1"><span className="inline-block w-2 h-0.5" style={{ background: '#fbbf24' }} />{zoneLabel(zone)} − {zoneLabel(compareZone)}</span>
              ) : (
                <>
                  <span className="flex items-center gap-1"><span className="inline-block w-2 h-0.5" style={{ background: COLOR_A }} />{zoneLabel(zone)}</span>
                  {comparing && <span className="flex items-center gap-1"><span className="inline-block w-2 h-0.5" style={{ background: COLOR_B }} />{zoneLabel(compareZone)}</span>}
                </>
              )}
            </div>
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={data} margin={{ top: 5, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" />
                <XAxis dataKey="t" tickFormatter={fmtT} tick={{ fontSize: 8, fill: '#737373' }} minTickGap={40} />
                <YAxis tick={{ fontSize: 8, fill: '#737373' }} width={44} domain={['auto', 'auto']} />
                <Tooltip {...CHART_TOOLTIP_STYLE} labelFormatter={fmtT}
                  formatter={(v, n) => [v != null ? Number(v).toFixed(1) : '—', n === 'd' ? `${zoneLabel(zone)} − ${zoneLabel(compareZone)}` : n === 'a' ? zoneLabel(zone) : zoneLabel(compareZone)]} />
                {showSpread ? (
                  <>
                    <ReferenceLine y={0} stroke="#444" />
                    <Line type="monotone" dataKey="d" stroke="#fbbf24" dot={false} strokeWidth={1.4} connectNulls isAnimationActive={false} />
                  </>
                ) : (
                  <>
                    <Line type="monotone" dataKey="a" stroke={COLOR_A} dot={false} strokeWidth={1.4} connectNulls isAnimationActive={false} />
                    {comparing && <Line type="monotone" dataKey="b" stroke={COLOR_B} dot={false} strokeWidth={1.4} connectNulls isAnimationActive={false} />}
                  </>
                )}
              </LineChart>
            </ResponsiveContainer>
          </>
        )}
      </div>
    </div>
  )
}
