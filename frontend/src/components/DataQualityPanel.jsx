import { useMemo, useState } from 'react'
import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import useZones from '../hooks/useZones'
import { ZONE_SERIES_KEY, qualitySeriesLabel as seriesLabel } from '../utils/qualitySeries'

const API = '/api'

// Initial zone-row cap — 37 zones in prod would push the ledger below the fold.
const INITIAL_ZONE_ROWS = 12

// Humanize an arrival lag in seconds. Negative = the data runs AHEAD of the
// wall clock (day-ahead auctions publish future hours) — honest, not an error.
function humanLag(s) {
  if (s == null) return '—'
  const a = Math.abs(s)
  const mag = a < 60 ? `${Math.round(a)} s`
    : a < 3600 ? `${Math.round(a / 60)} min`
    : a < 86400 ? `${(a / 3600).toFixed(1)} h`
    : `${(a / 86400).toFixed(1)} d`
  return `${mag} ${s >= 0 ? 'behind' : 'ahead'}`
}

const fmtPct = (r) => (r == null ? '—' : `${(r * 100).toFixed(1)}%`)

// Same emerald/amber/orange ladder as BordersPanel's convergenceColor — a hue
// already means "how complete/coupled" elsewhere on the desk.
function completenessTone(r) {
  if (r == null) return { text: 'text-neutral-700', bar: 'bg-neutral-700' }
  if (r >= 0.99) return { text: 'text-emerald-400', bar: 'bg-emerald-400' }
  if (r >= 0.95) return { text: 'text-amber-400', bar: 'bg-amber-400' }
  return { text: 'text-orange-400', bar: 'bg-orange-400' }
}

// Tiny inline completeness bar — a styled div, deliberately not a chart lib.
function CompletenessBar({ ratio }) {
  const tone = completenessTone(ratio)
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="inline-block w-12 h-1 rounded bg-white/10 overflow-hidden shrink-0">
        {ratio != null && (
          <span className={`block h-full ${tone.bar}`} style={{ width: `${Math.min(100, ratio * 100)}%` }} />
        )}
      </span>
      <span className={`num ${tone.text}`}>{fmtPct(ratio)}</span>
    </span>
  )
}

// Structured ⓘ legend — BordersLegend's <dl> pattern. The API's `note` string
// is never rendered raw here (repo rule); the long-form prose lives in
// HOW TO READ on the EUROPE tab.
function QualityLegend() {
  const rows = [
    ['Completeness', 'mean of hours published ÷ hours expected per UTC day (24 hourly, 96 quarter-hour), over the trailing 30/90 days with quality rows. A statement about the published record, not the market.'],
    ['Worst series', 'the series with the lowest 30-day completeness in the zone.'],
    ['Flags', 'days a rule flagged the published data (solar reported at night, load flatlining at exact zero, an hourly step 8× the series’ own month). Each flag describes the feed.'],
    ['Revisions', 'hours the source re-published with a DIFFERENT value in the last 30 days, beyond float noise — the source revising its own publication.'],
    ['Arrival', 'newest fetch’s wall clock minus the newest hour it delivered. Negative (“ahead”) for day-ahead series — the auction publishes the future.'],
    ['zone-level checks', 'flags needing several series at once (e.g. generation below load while the zone exports). No completeness, revisions or lag of their own.'],
  ]
  return (
    <div className="space-y-2">
      <dl className="space-y-1.5">
        {rows.map(([term, def]) => (
          <div key={term} className="grid grid-cols-[86px_1fr] gap-x-2">
            <dt className="text-cyan-glow/90">{term}</dt>
            <dd className="text-neutral-400 leading-snug">{def}</dd>
          </div>
        ))}
      </dl>
      <div className="pt-1 border-t border-border/40 text-neutral-500">
        Descriptive throughout — a low day is hours the source has not (yet) published,
        never a judgement. Full definitions: HOW TO READ on the EUROPE tab.
      </div>
    </div>
  )
}

// Per-series breakdown under an expanded zone row.
function SeriesBreakdown({ series }) {
  return (
    <table className="w-full font-mono text-[10px]">
      <thead>
        <tr className="text-[8px] text-neutral-600 uppercase tracking-wider">
          <th className="text-left px-2 py-1">Series</th>
          <th className="text-left px-2 py-1" title="Mean daily completeness, trailing 30 days">30d</th>
          <th className="text-right px-2 py-1" title="Mean daily completeness, trailing 90 days">90d</th>
          <th className="text-right px-2 py-1" title="Days with at least one quality flag, trailing 30 days">Flags 30d</th>
          <th className="text-right px-2 py-1" title="Source restatements beyond float noise, trailing 30 days">Rev 30d</th>
          <th className="text-right px-2 py-1" title="Latest fetch's wall clock minus the newest hour it delivered">Arrival</th>
        </tr>
      </thead>
      <tbody>
        {series.map((s) => {
          const isZoneRow = s.series_key === ZONE_SERIES_KEY
          return (
            <tr key={s.series_key} className="border-t border-border/30">
              <td className={`px-2 py-1 ${isZoneRow ? 'text-neutral-500 italic' : 'text-neutral-400'}`}>
                {seriesLabel(s.series_key)}
              </td>
              {isZoneRow ? (
                // Zone-level pseudo-series: flags only — the other columns are
                // null by contract, shown as em-dashes, never as zeros.
                <>
                  <td className="px-2 py-1 text-neutral-700">—</td>
                  <td className="px-2 py-1 text-right text-neutral-700">—</td>
                  <td className="px-2 py-1 text-right text-neutral-300 num">{s.flagged_days_30d}</td>
                  <td className="px-2 py-1 text-right text-neutral-700">—</td>
                  <td className="px-2 py-1 text-right text-neutral-700">—</td>
                </>
              ) : (
                <>
                  <td className="px-2 py-1"><CompletenessBar ratio={s.completeness_30d} /></td>
                  <td className={`px-2 py-1 text-right num ${completenessTone(s.completeness_90d).text}`}>{fmtPct(s.completeness_90d)}</td>
                  {/* No null-guards on this branch: for real series the API
                      contract makes flags AND revisions plain ints (nulls exist
                      only on the _zone branch above) — guarding one but not the
                      other would just imply a contract that isn't there. */}
                  <td className={`px-2 py-1 text-right num ${s.flagged_days_30d > 0 ? 'text-neutral-300' : 'text-neutral-600'}`}>{s.flagged_days_30d}</td>
                  <td className={`px-2 py-1 text-right num ${s.revisions_30d > 0 ? 'text-neutral-300' : 'text-neutral-600'}`}>{s.revisions_30d}</td>
                  <td className="px-2 py-1 text-right text-neutral-500">{humanLag(s.arrival_lag_s)}</td>
                </>
              )}
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

/**
 * The quality matrix over GET /api/v1/quality/summary: per zone, how complete
 * the published record is (30/90d), which series lags worst, how often rules
 * flagged the published data, how often the source restated values, and how
 * far behind the wall clock the newest data arrived. One summary row per zone,
 * expandable to the per-series breakdown. Worst completeness floats to the top.
 *
 * Posture B: every cell describes what the SOURCE published / restated — a low
 * completeness day is hours not (yet) published, never "wrong data".
 */
export default function DataQualityPanel() {
  const { data, loading, error } = useFetchWithError(`${API}/v1/quality/summary`)
  const { zones: zoneList } = useZones()
  const [expanded, setExpanded] = useState(() => new Set())
  const [showAll, setShowAll] = useState(false)

  // key→label Map built once per zone-list load — a find() per row per render
  // would rescan the 37-entry list for every visible zone.
  const zoneLabels = useMemo(() => new Map(zoneList.map((z) => [z.key, z.label || z.key])), [zoneList])
  const zoneLabel = (key) => zoneLabels.get(key) || key

  // Zone summary rows, worst 30d completeness first (problems float up).
  const rows = useMemo(() => {
    const zones = data?.zones || []
    return zones
      .map((z) => {
        const real = z.series.filter((s) => s.series_key !== ZONE_SERIES_KEY)
        const with30 = real.filter((s) => s.completeness_30d != null)
        const mean30 = with30.length
          ? with30.reduce((acc, s) => acc + s.completeness_30d, 0) / with30.length
          : null
        const worst = with30.length
          ? with30.reduce((acc, s) => (s.completeness_30d < acc.completeness_30d ? s : acc))
          : null
        const flags30 = z.series.reduce((acc, s) => acc + (s.flagged_days_30d || 0), 0)
        const revs30 = real.reduce((acc, s) => acc + (s.revisions_30d || 0), 0)
        const lags = real.map((s) => s.arrival_lag_s).filter((v) => v != null)
        // "Worst" = numerically greatest = most behind the clock. A zone whose
        // every series runs ahead (day-ahead) honestly shows "ahead".
        const worstLag = lags.length ? Math.max(...lags) : null
        return { zone: z.zone, series: z.series, mean30, worst, flags30, revs30, worstLag }
      })
      .sort((a, b) => (a.mean30 ?? 2) - (b.mean30 ?? 2))
  }, [data])

  const visible = showAll ? rows : rows.slice(0, INITIAL_ZONE_ROWS)

  const toggle = (zone) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(zone)) next.delete(zone)
      else next.add(zone)
      return next
    })

  return (
    <Panel
      id="data-quality"
      title="DATA QUALITY · COMPLETENESS & FLAGS"
      info={<QualityLegend />}
      infoWide
      freshness={data}
      headerRight={
        data?.available && (
          <span className="font-mono text-[9px] text-neutral-600">
            {rows.length} zones · last {data?.windows?.short_days ?? 30}/{data?.windows?.long_days ?? 90}d
          </span>
        )
      }
    >
      {loading && !data && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">Loading quality matrix…</div>
      )}
      {!loading && error && !data && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-red-400">Fetch error — retrying on next refresh.</div>
      )}
      {data && !data.available && (
        <div className="px-4 py-4 font-mono text-[10px] text-neutral-500">
          No quality aggregates on record yet — the nightly quality job writes one row per zone, series and UTC day.
        </div>
      )}
      {data?.available && (
        <>
          <div className="px-2 py-2 overflow-x-auto">
            <table className="w-full font-mono text-[11px]">
              <thead>
                <tr className="text-[9px] text-neutral-600 uppercase tracking-wider">
                  <th className="text-left px-2 py-1">Zone</th>
                  <th className="text-left px-2 py-1" title="Mean 30-day completeness across this zone's series">Completeness 30d</th>
                  <th className="text-left px-2 py-1" title="The series with the lowest 30-day completeness">Worst series</th>
                  <th className="text-right px-2 py-1" title="Flagged days across all series, trailing 30 days">Flags</th>
                  <th className="text-right px-2 py-1" title="Source restatements across all series, trailing 30 days">Rev</th>
                  <th className="text-right px-2 py-1" title="Worst (most behind) latest arrival lag across this zone's series">Arrival</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((r) => {
                  const isOpen = expanded.has(r.zone)
                  return (
                    <RowGroup key={r.zone} row={r} isOpen={isOpen} onToggle={() => toggle(r.zone)} zoneLabel={zoneLabel} />
                  )
                })}
              </tbody>
            </table>
          </div>
          {rows.length > INITIAL_ZONE_ROWS && (
            <div className="px-4 pb-2">
              <button
                onClick={() => setShowAll((v) => !v)}
                className="font-mono text-[9px] tracking-wider border border-border rounded px-1.5 py-0.5 text-neutral-500 hover:text-cyan-glow hover:border-cyan-glow/40 transition-colors"
              >
                {showAll ? 'show fewer' : `show all ${rows.length} zones`}
              </button>
            </div>
          )}
          <div className="px-4 py-2 border-t border-border font-mono text-[9px] text-neutral-700">
            What the published record looks like, per zone × series — descriptive, never a judgement.
            Public at GET /api/v1/quality/summary.
          </div>
        </>
      )}
    </Panel>
  )
}

// One zone's summary row + (when open) its per-series breakdown row.
function RowGroup({ row, isOpen, onToggle, zoneLabel }) {
  return (
    <>
      <tr
        onClick={onToggle}
        className="border-t border-border/30 cursor-pointer hover:bg-white/[0.03]"
        title={isOpen ? 'Collapse per-series breakdown' : 'Expand per-series breakdown'}
      >
        <td className="px-2 py-1.5 text-neutral-300 whitespace-nowrap">
          <span className="text-neutral-600 mr-1.5">{isOpen ? '▾' : '▸'}</span>
          {zoneLabel(row.zone)}
        </td>
        <td className="px-2 py-1.5"><CompletenessBar ratio={row.mean30} /></td>
        <td className="px-2 py-1.5 text-neutral-500 whitespace-nowrap">
          {row.worst ? (
            <>
              {seriesLabel(row.worst.series_key)}{' '}
              <span className={completenessTone(row.worst.completeness_30d).text}>
                {fmtPct(row.worst.completeness_30d)}
              </span>
            </>
          ) : '—'}
        </td>
        <td className={`px-2 py-1.5 text-right num ${row.flags30 > 0 ? 'text-neutral-300' : 'text-neutral-600'}`}>{row.flags30}</td>
        <td className={`px-2 py-1.5 text-right num ${row.revs30 > 0 ? 'text-neutral-300' : 'text-neutral-600'}`}>{row.revs30}</td>
        <td className="px-2 py-1.5 text-right text-neutral-500 whitespace-nowrap">{humanLag(row.worstLag)}</td>
      </tr>
      {isOpen && (
        <tr className="border-t border-border/20">
          <td colSpan={6} className="px-2 pb-2 pt-1 bg-white/[0.02]">
            <SeriesBreakdown series={row.series} />
          </td>
        </tr>
      )}
    </>
  )
}
