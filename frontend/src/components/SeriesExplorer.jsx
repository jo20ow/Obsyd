import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, ReferenceLine,
} from 'recharts'
import { InfoPopover } from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import useSeriesFrame, { MAX_SERIES_ROWS } from '../hooks/useSeriesFrame'
import { useViewState } from '../context/ViewStateContext'
import { CHART_TOOLTIP_PROPS } from '../utils/chart'

const API = '/api'
const DEFAULT_SERIES = 'price.dayahead'
const DEFAULT_RESOLUTION = 'daily'

// Positional row colors — NOT per-series/per-zone, so a row keeps its color
// when a sibling row is removed. Row 0/1 keep the original Explorer's
// cyan/violet; the rest reuse ZoneCompareChart's pink/green/indigo plus the
// old Δ-spread amber, so a hue already means something elsewhere on the desk.
const ROW_COLORS = ['#22d3ee', '#a78bfa', '#f472b6', '#4ade80', '#fbbf24', '#818cf8']

// Module-level (stable reference) fallbacks for before the catalog loads.
const FALLBACK_SERIES = [{ key: DEFAULT_SERIES, unit: 'EUR/MWh', label: 'Day-ahead price · hourly', group: 'price' }]
const FALLBACK_ZONES = [{ key: 'DE_LU', label: 'DE-LU' }]
const FALLBACK_GROUPS = [{ key: 'price', label: 'Prices' }]

// Module-level (page-load-scoped, NOT per-mount) latch for the row-0-zone
// adoption effect below. SeriesExplorer unmounts/remounts every time the
// EXPLORE tab is switched away from and back, but the `rows=` URL param it
// last wrote is never cleared on unmount — so a naive per-mount effect would
// re-read that stale link on every remount and silently snap the global zone
// back to it, even after the user picked a different zone elsewhere (e.g.
// RegionPills) in between. Adoption must happen at most once per SPA session
// (a genuine fresh page load, or a shared /builder link) — this flag is that
// gate. `let` (not `const`) since exactly one mount flips it.
let adoptedRowZone = false

// Subsequence fuzzy match (same idiom as CommandPalette.jsx's fuzzyScore):
// every char of `q` appears in order in `text`. Lower score = better match.
function fuzzyScore(text, q) {
  if (!q) return 0
  const t = text.toLowerCase()
  let ti = 0
  let score = 0
  let last = -1
  for (const ch of q.toLowerCase()) {
    const idx = t.indexOf(ch, ti)
    if (idx === -1) return null
    score += idx - last - 1
    if (last === -1) score += idx
    last = idx
    ti = idx + 1
  }
  return score
}

// Explorer/Builder selection is shareable: rows/res live in the URL query
// (zone+range already travel via the global ViewState spine). replaceState so
// editing the chart doesn't spam the history stack.
function readParam(name, fallback) {
  if (typeof window === 'undefined') return fallback
  return new URLSearchParams(window.location.search).get(name) || fallback
}

// `?rows=series:zone,series:zone,...` — ':' is safe as a delimiter since
// neither series keys (dot-namespaced) nor zone keys ever contain one.
function parseRowsParam(raw) {
  if (!raw) return null
  const rows = raw.split(',').map((pair) => {
    const i = pair.indexOf(':')
    if (i === -1) return null
    const series = pair.slice(0, i)
    const zone = pair.slice(i + 1)
    return series && zone ? { series, zone } : null
  }).filter(Boolean)
  return rows.length ? rows : null
}

function serializeRows(rows) {
  return rows.map((r) => `${r.series}:${r.zone}`).join(',')
}

// The `rows=`/`res=` pair for the canonical URL, omitting each when it's the
// default (a lone default-series row / daily resolution) — shared by the
// history.replaceState sync effect and the /builder "open full-screen" link,
// so the two can never disagree on what counts as "default enough to omit".
function rowsQueryParams(rows, resolution, primarySeries) {
  const p = new URLSearchParams()
  const isDefaultSingleRow = rows.length === 1 && primarySeries === DEFAULT_SERIES
  if (!isDefaultSingleRow) p.set('rows', serializeRows(rows))
  if (resolution !== DEFAULT_RESOLUTION) p.set('res', resolution)
  return p
}

function rowLabel(row, seriesList, zoneList) {
  if (!row) return ''
  const s = seriesList.find((x) => x.key === row.series)
  const z = zoneList.find((x) => x.key === row.zone)
  return `${s?.label || row.series} · ${z?.label || row.zone}`
}

// Searchable series picker: text input + fuzzy-filtered, group-organized list
// (groups/labels come from the v1 catalog, same data the plain <select> used
// to render as <optgroup>s). Replaces the old <select> now that the catalog
// can hold enough series that scrolling one flat list is the wrong UI.
function SeriesPicker({ value, onChange, seriesList, groups }) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [sel, setSel] = useState(0)  // index into the FLATTENED filtered list
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const selected = seriesList.find((s) => s.key === value)

  const filteredGroups = useMemo(() => {
    if (!query) return groups
    return groups
      .map((g) => {
        const scored = g.items
          .map((s) => ({ s, score: fuzzyScore(`${s.label} ${s.key}`, query) }))
          .filter((x) => x.score !== null)
          .sort((a, b) => a.score - b.score)
        return { ...g, items: scored.map((x) => x.s) }
      })
      .filter((g) => g.items.length > 0)
  }, [groups, query])
  // Flattened for arrow-key navigation/highlighting — same idiom as
  // CommandPalette's `results`/`sel` (a group boundary is just a render
  // detail, the selection index doesn't know about groups).
  const flatItems = useMemo(() => filteredGroups.flatMap((g) => g.items), [filteredGroups])
  const indexByKey = useMemo(() => new Map(flatItems.map((s, i) => [s.key, i])), [flatItems])
  const safeSel = Math.min(sel, Math.max(flatItems.length - 1, 0))

  const pick = (key) => { onChange(key); setOpen(false); setQuery(''); setSel(0) }

  const onKeyDown = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); setOpen(false) }
    else if (e.key === 'ArrowDown') { e.preventDefault(); setSel((s) => Math.min(s + 1, flatItems.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)) }
    else if (e.key === 'Enter') { e.preventDefault(); if (flatItems[safeSel]) pick(flatItems[safeSel].key) }
  }

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="font-mono text-[11px] bg-[#0a0a12] border border-border rounded px-2 py-1 text-neutral-300 max-w-[240px] truncate text-left hover:border-cyan-glow/40"
        title={selected ? `${selected.label} (${selected.key})` : value}
      >
        {selected ? `${selected.label}${selected.unit ? ` (${selected.unit})` : ''}` : value}
      </button>
      {open && (
        <div className="absolute z-30 mt-1 w-72 max-h-72 overflow-y-auto border border-border bg-surface rounded shadow-xl shadow-black/40">
          <input
            autoFocus
            value={query}
            onChange={(e) => { setQuery(e.target.value); setSel(0) }}
            onKeyDown={onKeyDown}
            placeholder="Filter series…"
            className="w-full border-b border-border px-2 py-1.5 font-mono text-[11px] text-neutral-200 placeholder:text-neutral-600 outline-none sticky top-0 bg-surface"
          />
          {filteredGroups.length === 0 && (
            <div className="px-2 py-3 font-mono text-[10px] text-neutral-600">No matching series.</div>
          )}
          {filteredGroups.map((g) => (
            <div key={g.group}>
              <div className="px-2 pt-1.5 pb-0.5 font-mono text-[9px] tracking-wider text-neutral-600">{g.group}</div>
              {g.items.map((s) => {
                const idx = indexByKey.get(s.key)
                return (
                  <button
                    key={s.key}
                    type="button"
                    onClick={() => pick(s.key)}
                    onMouseEnter={() => setSel(idx)}
                    className={`w-full flex items-center justify-between gap-2 px-2 py-1 text-left font-mono text-[11px] ${
                      idx === safeSel ? 'bg-cyan-glow/10 text-cyan-glow' : s.key === value ? 'text-cyan-glow' : 'text-neutral-300 hover:bg-white/[0.04]'
                    }`}
                  >
                    <span className="truncate">{s.label}</span>
                    {s.unit && <span className="text-[9px] text-neutral-600 shrink-0">{s.unit}</span>}
                  </button>
                )
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function SeriesExplorer() {
  const { zone, setZone, range } = useViewState()  // primary zone + range = the global spine

  // Parsed ONCE at mount: either the current `rows=` format, or a legacy
  // `s=`/`vs=` link (old shared Explorer URLs) — never both. `primaryZone` is
  // only set from a `rows=` link (the legacy scheme never encoded row 0's
  // zone; it always rode the global spine).
  const initialRef = useRef(null)
  if (initialRef.current === null) {
    const params = new URLSearchParams(window.location.search)
    const rowsParam = parseRowsParam(params.get('rows'))
    if (rowsParam) {
      initialRef.current = {
        primarySeries: rowsParam[0].series,
        primaryZone: rowsParam[0].zone,
        // Stable ids (not array position) so removing a middle row later
        // doesn't reassign a sibling SeriesPicker's open/query state onto it.
        extra: rowsParam.slice(1, MAX_SERIES_ROWS).map((r, idx) => ({ ...r, id: idx + 1 })),
      }
    } else {
      const legacyS = params.get('s')
      const legacyVs = params.get('vs')
      // The old 2-line Explorer treated vs === the current zone as a no-op
      // (`comparing = compareZone && compareZone !== zone`) — never a second
      // line. Preserve that: don't manufacture a duplicate row for it.
      const extra = legacyVs && legacyVs !== zone
        ? [{ series: legacyS || DEFAULT_SERIES, zone: legacyVs, id: 1 }]
        : []
      initialRef.current = { primarySeries: legacyS || DEFAULT_SERIES, primaryZone: null, extra }
    }
  }
  const initial = initialRef.current
  const nextRowId = useRef(initial.extra.length + 1)  // ids already used: 1..extra.length

  const [primarySeries, setPrimarySeries] = useState(initial.primarySeries)
  const [extraRows, setExtraRows] = useState(initial.extra)  // [{id,series,zone}] — rows 1..5
  const [resolution, setResolution] = useState(() => {
    // A mistyped/stale shared link (e.g. `?res=hour`) must fall back to the
    // default instead of being sent straight to the API — `resolution` isn't
    // validated server-side beyond a 422, and the bad value would just get
    // re-persisted into the URL by the sync effect below.
    const r = readParam('res', DEFAULT_RESOLUTION)
    return r === 'hourly' || r === 'daily' ? r : DEFAULT_RESOLUTION
  })
  const [spread, setSpread] = useState(false)  // Δ (row0 − row1) instead of separate lines

  // A `rows=` link may carry an explicit zone for row 0 (the legacy `s=`/`vs=`
  // scheme never did — it always rode the global zone spine). Adopt it AT
  // MOST ONCE per page load (see `adoptedRowZone` above), never on a
  // tab-switch remount.
  useEffect(() => {
    if (adoptedRowZone) return
    adoptedRowZone = true
    if (initial.primaryZone && initial.primaryZone !== zone) setZone(initial.primaryZone)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const rows = useMemo(
    () => [{ id: 'primary', series: primarySeries, zone }, ...extraRows],
    [primarySeries, zone, extraRows]
  )

  // Rewrite the URL to the canonical `rows=`/`res=` form on every edit — this
  // also erases any leftover legacy `s=`/`vs=` from the link that seeded us.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const rq = rowsQueryParams(rows, resolution, primarySeries)
    if (rq.has('rows')) params.set('rows', rq.get('rows')); else params.delete('rows')
    if (rq.has('res')) params.set('res', rq.get('res')); else params.delete('res')
    params.delete('s')
    params.delete('vs')
    const qs = params.toString()
    history.replaceState(null, '', `${window.location.pathname}${qs ? `?${qs}` : ''}${window.location.hash}`)
  }, [rows, resolution, primarySeries])

  // /builder discoverability: a link out of the EXPLORE tab (never shown ON
  // /builder itself, which already IS the full-screen view) carrying the
  // current chart + the zone/range spine, so the exact view travels.
  const isBuilderRoute = typeof window !== 'undefined' && window.location.pathname === '/builder'
  const builderHref = (() => {
    const p = rowsQueryParams(rows, resolution, primarySeries)
    p.set('zone', zone)
    p.set('range', range)
    return `/builder?${p.toString()}`
  })()

  const { data: catalog } = useFetchWithError(`${API}/v1/series/catalog`)
  const seriesList = catalog?.series || FALLBACK_SERIES
  const zoneList = catalog?.zones || FALLBACK_ZONES
  // Grouped once from `catalog` alone (not the derived seriesList/groups
  // fallbacks above, whose `||` literals would otherwise be a fresh array
  // reference every render and defeat this memo).
  const displayGroups = useMemo(() => {
    const groups = catalog?.groups || FALLBACK_GROUPS
    const list = catalog?.series || FALLBACK_SERIES
    return groups.map((g) => ({ group: g.label, items: list.filter((s) => s.group === g.key) })).filter((g) => g.items.length > 0)
  }, [catalog])
  // coverage_by_series can lag `series` by up to an hour (server-cached) — its
  // absence for a (series,zone) pair means "not yet reflected", never a hard
  // block. Used only to dim zone options, never to disable them. A missing/
  // empty coverage list (catalog not loaded yet, or itself failed) is NOT
  // evidence of "no data anywhere" — dimming every zone in that case would be
  // a lie, so dimming is gated on the catalog actually having coverage rows.
  const hasCoverageData = (catalog?.coverage_by_series?.length ?? 0) > 0
  const coverageSet = useMemo(() => {
    const set = new Set()
    for (const c of catalog?.coverage_by_series || []) set.add(`${c.series}|${c.zone}`)
    return set
  }, [catalog])

  const { frame, loading, error, perRow } = useSeriesFrame(rows, range, resolution)

  // Dual-axis-by-unit assignment: the left axis anchors to the FIRST row with
  // a known unit — not strictly row 0 — so a slow-to-load or errored row 0
  // doesn't collapse every other (already-known, possibly different) unit
  // onto one axis while it waits; the first genuinely different unit seen
  // gets the right axis, and a third distinct unit is rejected (2 axes max)
  // rather than drawn on a meaningless scale.
  const { assigned, rejected, rightUnit } = useMemo(() => {
    const leftUnit = perRow.find((r) => r.unit != null)?.unit ?? null
    let right = null
    const bad = []
    const out = perRow.map((r, i) => {
      const color = ROW_COLORS[i % ROW_COLORS.length]
      if (r.unit == null || leftUnit == null || r.unit === leftUnit) return { ...r, axis: 'left', color }
      if (right == null) { right = r.unit; return { ...r, axis: 'right', color } }
      if (r.unit === right) return { ...r, axis: 'right', color }
      bad.push({ ...r, color })
      return { ...r, axis: null, color }
    })
    return { assigned: out, rejected: bad, rightUnit: right }
  }, [perRow])
  const visibleRows = assigned.filter((r) => r.axis)

  // Spread only makes sense for exactly two rows on the SAME unit (both units
  // known and equal) — a superset of the original "comparing" toggle, which
  // only ever had two same-series rows so units always matched by construction.
  const canSpread = rows.length === 2 && perRow[0]?.unit != null && perRow[0].unit === perRow[1]?.unit
  const showSpread = spread && canSpread
  const spreadFrame = useMemo(
    () => frame.map((p) => ({ t: p.t, d: p.v0 != null && p.v1 != null ? Math.round((p.v0 - p.v1) * 100) / 100 : null })),
    [frame]
  )

  const canAddRow = rows.length < MAX_SERIES_ROWS
  const addRow = () => {
    if (!canAddRow) return
    const used = new Set(rows.map((r) => r.zone))
    const nextZone = zoneList.find((z) => !used.has(z.key))?.key || zone
    setExtraRows((rs) => [...rs, { id: nextRowId.current++, series: primarySeries, zone: nextZone }])
  }
  const removeExtraRow = (i) => setExtraRows((rs) => rs.filter((_, idx) => idx !== i))
  const updateExtraRow = (i, patch) => setExtraRows((rs) => rs.map((r, idx) => (idx === i ? { ...r, ...patch } : r)))

  const fmtT = (t) => (resolution === 'daily' ? t : String(t).slice(5, 16).replace('T', ' '))

  return (
    <div className="border border-border bg-surface rounded overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 border-b border-border">
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs text-neutral-500 tracking-wider">SERIES EXPLORER · /api/v1</span>
          <InfoPopover text="Query any series for any zone over the canonical hourly store via the public data API (GET /api/v1/series). Add up to 6 series×zone rows; a second unit gets its own right-hand axis, and two same-unit rows can show their Δ spread instead. Download any row as CSV, or share the exact chart — it's all in the URL. Free, official, redistributable data — descriptive, not a forecast." />
        </div>
        <div className="flex items-center gap-2">
          <span className="font-mono text-[9px] text-neutral-700 tracking-wider">{rows.length}/{MAX_SERIES_ROWS} rows</span>
          {!isBuilderRoute && (
            <a
              href={builderHref}
              title="Open this chart full-screen at /builder"
              className="font-mono text-[9px] tracking-wider border border-border rounded px-1.5 py-0.5 text-neutral-500 hover:text-cyan-glow hover:border-cyan-glow/40 transition-colors"
            >
              open full-screen ↗
            </a>
          )}
        </div>
      </div>

      <div className="px-4 py-2.5 border-b border-border/50 space-y-1.5">
        {rows.map((row, i) => (
          <div key={row.id} className="flex flex-wrap items-center gap-1.5">
            <span className="inline-block w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: ROW_COLORS[i % ROW_COLORS.length] }} />
            <SeriesPicker
              value={row.series}
              onChange={(key) => (i === 0 ? setPrimarySeries(key) : updateExtraRow(i - 1, { series: key }))}
              seriesList={seriesList}
              groups={displayGroups}
            />
            <select
              value={row.zone}
              onChange={(e) => (i === 0 ? setZone(e.target.value) : updateExtraRow(i - 1, { zone: e.target.value }))}
              className={`font-mono text-[11px] bg-[#0a0a12] border rounded px-2 py-1 ${i === 0 ? 'border-cyan-500/40 text-cyan-300' : 'border-border text-neutral-300'}`}
            >
              {zoneList.map((z) => {
                const covered = !hasCoverageData || coverageSet.has(`${row.series}|${z.key}`)
                return (
                  <option key={z.key} value={z.key} style={covered ? undefined : { color: '#525252' }}>
                    {z.label || z.key}{covered ? '' : ' · no data'}
                  </option>
                )
              })}
            </select>
            <a
              href={perRow[i]?.downloadUrl || '#'}
              className="font-mono text-[9px] tracking-wider border border-border rounded px-1.5 py-0.5 text-neutral-500 hover:text-cyan-glow hover:border-cyan-glow/40 transition-colors"
              title="Download this row's data as CSV"
            >
              ↓ CSV
            </a>
            {i > 0 && (
              <button
                onClick={() => removeExtraRow(i - 1)}
                aria-label="Remove row"
                title="Remove row"
                className="font-mono text-[10px] px-1.5 py-0.5 rounded border border-border text-neutral-500 hover:text-red-400 hover:border-red-400/40"
              >
                ×
              </button>
            )}
            {perRow[i]?.error ? (
              <span className="font-mono text-[9px] text-red-400" title={perRow[i].error}>fetch error</span>
            ) : perRow[i]?.available === false && (
              <span className="font-mono text-[9px] text-neutral-600">{perRow[i].reason || 'no data'}</span>
            )}
          </div>
        ))}
        {canAddRow && (
          <button
            onClick={addRow}
            className="font-mono text-[10px] px-2 py-1 rounded border border-border text-neutral-500 hover:text-cyan-glow hover:border-cyan-glow/40 transition-colors"
          >
            + add series
          </button>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2 px-4 py-2 border-b border-border/50">
        {canSpread && (
          <button onClick={() => setSpread((s) => !s)}
            className={`font-mono text-[9px] px-2 py-0.5 rounded border ${spread ? 'text-amber-300 border-amber-400/40 bg-amber-400/10' : 'text-neutral-500 border-border hover:text-neutral-300'}`}
            title="Show the row0 − row1 spread instead of two lines">
            Δ A−B
          </button>
        )}
        <div className="flex items-center gap-1 ml-auto">
          {['hourly', 'daily'].map((rz) => (
            <button key={rz} onClick={() => setResolution(rz)}
              className={`font-mono text-[9px] px-2 py-0.5 rounded border ${resolution === rz ? 'text-violet-300 border-violet-400/40 bg-violet-400/10' : 'text-neutral-500 border-border hover:text-neutral-300'}`}>
              {rz.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      {rejected.length > 0 && (
        <div className="px-4 pt-2 font-mono text-[9px] text-amber-400">
          Not charted (only two units per chart): {rejected.map((r) => `${rowLabel(r, seriesList, zoneList)} (${r.unit})`).join(', ')}.
        </div>
      )}

      <div className="px-2 pt-3 pb-1" style={{ minHeight: 240 }}>
        {loading && frame.length === 0 && (
          <div className="px-4 py-10 text-center font-mono text-[10px] text-neutral-600 animate-pulse">Loading…</div>
        )}
        {!loading && error && frame.length === 0 && (
          <div className="px-4 py-10 text-center font-mono text-[10px] text-red-400">Fetch error — retrying on next refresh.</div>
        )}
        {!loading && !error && frame.length === 0 && (
          <div className="px-4 py-10 text-center font-mono text-[10px] text-neutral-600">No data for this selection.</div>
        )}
        {frame.length > 0 && (
          <>
            <div className="px-2 pb-2 flex flex-wrap items-center gap-3 font-mono text-[10px] text-neutral-500">
              {showSpread ? (
                <span className="flex items-center gap-1">
                  <span className="inline-block w-2 h-0.5" style={{ background: '#fbbf24' }} />
                  {rowLabel(assigned[0], seriesList, zoneList)} − {rowLabel(assigned[1], seriesList, zoneList)}
                </span>
              ) : (
                visibleRows.map((r) => (
                  <span key={r.key} className="flex items-center gap-1">
                    <span className="inline-block w-2 h-0.5" style={{ background: r.color }} />
                    <span>{rowLabel(r, seriesList, zoneList)}{r.unit ? ` (${r.unit})` : ''}</span>
                    {r.error && (
                      <span className="text-red-400" title={r.error}>fetch error</span>
                    )}
                    {r.partialHours != null && (
                      <span className="text-neutral-600" title={`The last day averages ${r.partialHours} of 24 hours — it is still filling in.`}>
                        {r.partialHours}/24h
                      </span>
                    )}
                  </span>
                ))
              )}
            </div>
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={showSpread ? spreadFrame : frame} margin={{ top: 5, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" />
                <XAxis dataKey="t" tickFormatter={fmtT} tick={{ fontSize: 8, fill: '#737373' }} minTickGap={40} />
                <YAxis yAxisId="left" tick={{ fontSize: 8, fill: '#737373' }} width={44} domain={['auto', 'auto']} />
                {!showSpread && rightUnit && (
                  <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 8, fill: '#737373' }} width={44} domain={['auto', 'auto']} />
                )}
                <Tooltip {...CHART_TOOLTIP_PROPS} labelFormatter={fmtT}
                  formatter={(v, name) => {
                    if (showSpread) return [v != null ? Number(v).toFixed(1) : '—', 'Δ']
                    const r = visibleRows.find((x) => x.key === name)
                    return [v != null ? Number(v).toFixed(1) : '—', r ? rowLabel(r, seriesList, zoneList) : name]
                  }} />
                {showSpread ? (
                  <>
                    <ReferenceLine yAxisId="left" y={0} stroke="#444" />
                    <Line yAxisId="left" type="monotone" dataKey="d" stroke="#fbbf24" dot={false} strokeWidth={1.4} connectNulls isAnimationActive={false} />
                  </>
                ) : (
                  visibleRows.map((r) => (
                    <Line key={r.key} yAxisId={r.axis} type="monotone" dataKey={r.key} stroke={r.color} dot={false} strokeWidth={1.4} connectNulls isAnimationActive={false} />
                  ))
                )}
              </LineChart>
            </ResponsiveContainer>
          </>
        )}
      </div>
    </div>
  )
}
