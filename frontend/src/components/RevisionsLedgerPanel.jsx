import { useState } from 'react'
import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import useZones from '../hooks/useZones'
// The shared charter list (mirrors backend QUALITY_SERIES). The zone-level
// pseudo-series is deliberately not offered here: it has no store series of
// its own, so there is nothing for the ledger to restate.
import { QUALITY_SERIES } from '../utils/qualitySeries'

const API = '/api'

// Raw-ledger display cap — the API can return up to 20k rows per window and a
// 20k-row DOM table helps nobody. The roll-up above the table is uncapped.
const MAX_RAW_ROWS = 200

// "2026-08-01T13:00:00+00:00" → "2026-08-01 13:00" (always UTC on this desk).
const fmtUtc = (iso) => (iso ? iso.slice(0, 16).replace('T', ' ') : '—')

// Signed percentage, 1 decimal. delta_pct is null when the old value was
// exactly 0 (no honest percentage exists) — shown as an em-dash, never 0.0%.
const fmtDeltaPct = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(1)}%`)

const fmtVal = (v) => (v == null ? '—' : Number(v).toFixed(1))

// Structured ⓘ legend — BordersLegend's <dl> pattern. API `reason` strings are
// shown verbatim in the panel body's empty state (they are written for humans),
// but never rendered raw inside this popover (repo rule).
function RevisionsLegend() {
  const rows = [
    ['Revision', 'the source re-published a DIFFERENT value for an hour it had already published, beyond a float-noise epsilon. The ledger records old → new, when the change was observed, and its size.'],
    ['Mature', 'observed more than 48 h after the hour it restates — settled data changed. The default view; the toggle adds the routine provisional fill-in window (sources routinely re-publish actuals for a day or two).'],
    ['Restated >1×', 'hours the source restated more than once inside the window; "last change" is the newest restatement’s size.'],
    ['Δ%', 'new − old as a share of |old| — the sign is the direction of movement, even across negative prices.'],
    ['Freshness', 'the last time this source was polled for this zone — the newest moment a restatement could have been observed. The ledger accrues forward from first deploy and is never pruned.'],
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
        Descriptive: the ledger reports the source’s own restatements — it never says
        the data was “wrong”. Full definitions: HOW TO READ on the EUROPE tab.
      </div>
    </div>
  )
}

/**
 * The revision ledger over GET /api/v1/quality/revisions: every time the
 * source re-published a different value for an hour it had already published,
 * for one series × zone. Headline counts, the restated-more-than-once roll-up,
 * and the raw old→new rows behind a toggle.
 *
 * Posture B: a restatement is the source revising its own publication — the
 * ledger describes it, it never calls the data wrong.
 */
export default function RevisionsLedgerPanel() {
  const [series, setSeries] = useState('price.dayahead')
  const [zone, setZone] = useState('DE_LU')
  const [mature, setMature] = useState(true) // true = settled-data restatements only (API default)
  const [showRaw, setShowRaw] = useState(false)
  const { zones } = useZones()

  // All selector state lives in the URL, so the url IS the dependency —
  // useFetchWithError invalidates `data` on every change (the #115
  // zone-coherence rule: never show stale other-pair data).
  const url = `${API}/v1/quality/revisions?series=${encodeURIComponent(series)}&zone=${encodeURIComponent(zone)}&mature=${mature}`
  const { data, loading, error } = useFetchWithError(url)

  const rows = data?.data ?? []
  const restated = data?.restated_hours ?? []
  // /revisions' as_of is a full ISO datetime (newest poll for this pair); the
  // header chip renders delivery DATES desk-wide, so trim to the UTC date.
  const freshness = data ? { ...data, as_of: data.as_of ? data.as_of.slice(0, 10) : null } : null

  return (
    <Panel
      id="revisions-ledger"
      title="REVISIONS LEDGER · SOURCE RESTATEMENTS"
      info={<RevisionsLegend />}
      infoWide
      freshness={freshness}
      headerRight={
        data?.available && (
          <span className="font-mono text-[9px] text-neutral-600">
            last {data.days}d
          </span>
        )
      }
    >
      <div className="flex flex-wrap items-center gap-1.5 px-4 py-2.5 border-b border-border/50">
        <select
          value={series}
          onChange={(e) => setSeries(e.target.value)}
          className="font-mono text-[11px] bg-[#0a0a12] border border-border rounded px-2 py-1 text-neutral-300"
          title="Series whose ledger to show"
        >
          {QUALITY_SERIES.map((s) => (
            <option key={s.key} value={s.key}>{s.label}</option>
          ))}
        </select>
        <select
          value={zone}
          onChange={(e) => setZone(e.target.value)}
          className="font-mono text-[11px] bg-[#0a0a12] border border-border rounded px-2 py-1 text-neutral-300"
          title="Bidding zone"
        >
          {zones.map((z) => (
            <option key={z.key} value={z.key}>{z.label || z.key}</option>
          ))}
        </select>
        <button
          onClick={() => setMature((m) => !m)}
          className={`font-mono text-[9px] px-2 py-0.5 rounded border ${
            !mature
              ? 'text-amber-300 border-amber-400/40 bg-amber-400/10'
              : 'text-neutral-500 border-border hover:text-neutral-300'
          }`}
          title="Also show restatements observed within 48 h of the hour — the routine provisional fill-in window sources re-publish in for a day or two"
        >
          include provisional fill-ins
        </button>
      </div>

      {loading && !data && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">Loading ledger…</div>
      )}
      {!loading && error && !data && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-red-400">Fetch error — retrying on next refresh.</div>
      )}
      {data && !data.available && (
        // The API's reason strings are written for humans (empty forward-only
        // ledger, derived series, over-cap window) — shown verbatim.
        <div className="px-4 py-4 font-mono text-[10px] text-neutral-500 leading-relaxed">
          {data.reason || 'No revisions on record for this selection.'}
        </div>
      )}
      {data?.available && (
        <>
          <div className="flex flex-wrap items-center gap-x-6 gap-y-1 px-4 py-3">
            <span className="font-mono text-[11px] text-neutral-300">
              <span className="num text-cyan-glow">{data.count}</span> revision{data.count === 1 ? '' : 's'} in {data.days}d
              <span className="text-neutral-600"> · {mature ? 'settled data only (observed >48 h after the hour)' : 'incl. provisional fill-ins'}</span>
            </span>
            <span className="font-mono text-[11px] text-neutral-300">
              <span className={`num ${restated.length > 0 ? 'text-amber-400' : 'text-neutral-500'}`}>{restated.length}</span>{' '}
              hour{restated.length === 1 ? '' : 's'} restated more than once
            </span>
          </div>

          {restated.length > 0 && (
            <div className="px-2 pb-2 overflow-x-auto">
              <table className="w-full font-mono text-[10px]">
                <thead>
                  <tr className="text-[8px] text-neutral-600 uppercase tracking-wider">
                    <th className="text-left px-2 py-1" title="The delivery hour the source restated (UTC)">Hour (UTC)</th>
                    <th className="text-right px-2 py-1" title="How many times this hour was restated inside the window">Revisions</th>
                    <th className="text-right px-2 py-1" title="Size of the newest restatement, as % of the value it replaced">Last change</th>
                  </tr>
                </thead>
                <tbody>
                  {restated.map((h) => (
                    <tr key={h.ts_utc} className="border-t border-border/30">
                      <td className="px-2 py-1 text-neutral-400 num">{fmtUtc(h.ts_utc)}</td>
                      <td className="px-2 py-1 text-right text-neutral-300 num">{h.n_revisions}</td>
                      {/* Direction is not a valence — a restatement up is not "good",
                          so no green/red here; the sign carries the direction. */}
                      <td className={`px-2 py-1 text-right num ${h.last_change_pct == null ? 'text-neutral-600' : 'text-neutral-300'}`}>
                        {fmtDeltaPct(h.last_change_pct)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="px-4 pb-2">
            <button
              onClick={() => setShowRaw((v) => !v)}
              className="font-mono text-[9px] tracking-wider border border-border rounded px-1.5 py-0.5 text-neutral-500 hover:text-cyan-glow hover:border-cyan-glow/40 transition-colors"
            >
              {showRaw ? '▾ hide individual revisions' : `▸ show individual revisions (${data.count})`}
            </button>
          </div>
          {showRaw && (
            <div className="px-2 pb-2 overflow-x-auto">
              <table className="w-full font-mono text-[10px]">
                <thead>
                  <tr className="text-[8px] text-neutral-600 uppercase tracking-wider">
                    <th className="text-left px-2 py-1" title="The delivery hour the restatement applies to (UTC)">Hour (UTC)</th>
                    <th className="text-right px-2 py-1" title="Previously published value → re-published value">Old → new</th>
                    <th className="text-right px-2 py-1" title="new − old as % of |old|">Δ%</th>
                    <th className="text-right px-2 py-1" title="When the restatement was observed at our fetch (UTC)">Observed (UTC)</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.slice(0, MAX_RAW_ROWS).map((r, i) => (
                    <tr key={`${r.ts_utc}-${r.observed_at}-${i}`} className="border-t border-border/30">
                      <td className="px-2 py-1 text-neutral-400 num">{fmtUtc(r.ts_utc)}</td>
                      <td className="px-2 py-1 text-right text-neutral-300 num whitespace-nowrap">
                        {fmtVal(r.old_value)} <span className="text-neutral-600">→</span> {fmtVal(r.new_value)}
                      </td>
                      <td className={`px-2 py-1 text-right num ${r.delta_pct == null ? 'text-neutral-600' : 'text-neutral-300'}`}>
                        {fmtDeltaPct(r.delta_pct)}
                      </td>
                      <td className="px-2 py-1 text-right text-neutral-500 num">{fmtUtc(r.observed_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {rows.length > MAX_RAW_ROWS && (
                <div className="px-2 py-1 font-mono text-[9px] text-neutral-600">
                  Showing the newest {MAX_RAW_ROWS} of {rows.length} — the full ledger is public at GET /api/v1/quality/revisions.
                </div>
              )}
            </div>
          )}

          <div className="px-4 py-2 border-t border-border font-mono text-[9px] text-neutral-700">
            Every value the source re-published differently, per series × zone — the source revising
            its own publication. Public at GET /api/v1/quality/revisions.
          </div>
        </>
      )}
    </Panel>
  )
}
