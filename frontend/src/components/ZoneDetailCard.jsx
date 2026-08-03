import useFetchWithError from '../hooks/useFetchWithError'
import { POLL_FAST_MS } from '../utils/poll'
import { ZONE_STATE, zColor, metricGlossary } from '../utils/zoneState'
import { InfoPopover } from './Panel'
import FreshnessCaption from './FreshnessCaption'

const API = '/api'

// The desk rail's bottom half: the one zone the split is looking at, in the
// same narrow column as the table that selects it. It is the ANSWER to a row or
// map click — the click stays a LOOK (nothing navigates), and the only door out
// of the tab is the explicit "Open zone →" button in this card's header.
//
// TWO feeds, deliberately:
//   /power/overview  — the headline numbers. The SAME url the matrix above
//     already polls, so useFetchWithError's url-keyed dedupe + SWR cache serve
//     this from the matrix's request: no second GET, and the card can never
//     show a different price than the row it sits under.
//   /power/situation?zone= — the flags and the one-line read. One request per
//     SELECTED zone (not per zone in the table), cached per url on the way back.
// Each feed carries its own freshness, so neither can make the other look
// current: the header stamps the overview row's as_of, the situation block
// stamps its own age when it lags.

// Per-zone flags from /situation, in the backend's own severity. Reported, not
// restyled: warning is amber and critical is red because that is what the
// backend said, and the label travels verbatim from the API.
const FLAG_STYLE = {
  critical: 'border-red-500/40 text-red-400',
  warning: 'border-yellow-500/40 text-yellow-400',
}

// The prose that would otherwise eat three lines of a rail this narrow. Lives
// in the header's ⓘ, the repo's convention for "structured, not a raw note".
// The four definitions are SHARED with the matrix's column legend directly above
// (utils/zoneState.js) — see the note there for why they may not be rewritten
// here. Only the first and last sentences are this card's own.
const CARD_INFO = (baselineDays) => {
  const g = metricGlossary(baselineDays)
  return [
    'This zone, as the desk reads it right now.',
    g.state, g.dayAhead, g.residual, g.renewables, g.sigma,
    'Flags and the one-line read come from the zone situation feed and carry their own timestamp, which can lag the numbers above.',
  ].join(' ')
}

// One headline number + what it is measured against. `sub` is never decoration:
// it carries the σ, i.e. the only thing that makes the number mean "unusual".
function Stat({ label, value, sub, color = 'text-neutral-200' }) {
  return (
    <div className="min-w-0">
      <div className="font-mono text-[8px] text-neutral-600 uppercase tracking-wider truncate">{label}</div>
      <div className={`num text-[13px] font-bold leading-tight truncate ${color}`}>{value}</div>
      <div className="font-mono text-[8px] text-neutral-600 truncate">{sub}</div>
    </div>
  )
}

// The σ line under a stat. Two different silences, told apart: no VALUE means
// the zone does not publish this series (IE-SEM has no load), no Z means the
// value is there but its baseline is not built yet.
const sigma = (value, z) => (
  value == null ? 'not published here' : z == null ? 'no baseline yet' : `${z >= 0 ? '+' : ''}${z.toFixed(1)}σ vs norm`
)

// One height for every state of the situation block, so switching zones does
// not make the table above gain a row while /situation is in flight.
const SITUATION_BOX = 'min-h-[55px] space-y-1.5'

// Flags + the one-line read for the selected zone. Owns its own fetch so the
// card above stays on the shared /overview payload — and so a dead /situation
// degrades to a line of text inside the card instead of blanking the numbers.
function ZoneSituation({ zone }) {
  const { data, loading, error } = useFetchWithError(`${API}/power/situation?zone=${zone}`, {
    deps: [zone], pollMs: POLL_FAST_MS,
  })

  if (error && !data)
    return <div className={`${SITUATION_BOX} font-mono text-[9px] text-red-400`}>Flags &amp; read // fetch error — retrying on next refresh.</div>
  if (loading && !data)
    return <div className={`${SITUATION_BOX} font-mono text-[9px] text-neutral-600 animate-pulse`}>Loading zone situation…</div>
  if (!data?.available)
    return <div className={`${SITUATION_BOX} font-mono text-[9px] text-neutral-500`}>No situation read for this zone yet.</div>

  const flags = data.flags ?? []
  return (
    <div className={SITUATION_BOX}>
      {/* The flag row is ALWAYS rendered, even empty: "nothing flagged" is a
          real reading, and a row that appears and disappears would make the
          table above it gain and lose a row on every zone click. */}
      <div className="flex flex-wrap gap-1">
        {flags.length === 0 ? (
          <span className="font-mono text-[8px] text-neutral-700">No flags — nothing unusual enough to name.</span>
        ) : flags.map((f) => (
          <span
            key={f.key}
            className={`font-mono text-[8px] px-1.5 py-px rounded border leading-snug ${FLAG_STYLE[f.severity] || FLAG_STYLE.warning}`}
          >
            {f.label}
          </span>
        ))}
      </div>
      <div className="flex items-start gap-1.5">
        {/* The desk's one-line read of this zone. Clamped, not truncated: the
            tail (dirty spark) is the part the three numbers above do not carry,
            so it stays reachable on hover instead of being cut for good. */}
        <div className="font-mono text-[9px] text-neutral-500 leading-snug line-clamp-2" title={data.headline}>
          {data.headline}
        </div>
        {/* This feed's OWN age — it can lag the overview row the numbers come
            from, and then it must say so next to its own sentence. */}
        {data.stale && <FreshnessCaption meta={data} dense />}
      </div>
    </div>
  )
}

/**
 * The selected zone, read in the rail. Never a silent hole: a fetch error, a
 * still-loading feed and a zone the overview does not carry each say so in the
 * card's own frame rather than rendering nothing.
 *
 * Height budget: the rail is a flex column of ONE shared height (DESK_COLUMN_H)
 * and the TABLE is its `flex-1 min-h-0` child, so every pixel this card takes is
 * a row the table loses. Everything here is therefore `shrink-0` and bounded —
 * a fixed 3-stat grid, a 2-line clamp on the read, a min-height on the situation
 * box, a flag row that renders even when empty — so the card measures 160 px for
 * every one of the 37 zones AND through every loading state, and clicking from
 * zone to zone never re-flows the table. The only moving part is how many flags
 * the backend raises: the synthetic worst case (3 flags wrapping to 3 lines +
 * a 2-line headline) reaches 203 px and costs the table one row. That is the
 * right thing to spend a row on, and it cannot overflow the column — the table
 * yields, this card does not.
 *
 * That budget is why two things the plan offered are NOT here.
 *
 * MiniMixCard was, and cost 182 px — 53 % of the card — to stack 15 unlabelled
 * series in a 330 px-wide rail. Removing it took the table from 6 visible zones
 * to 11 at 1280×800 and 13 to 18 at 1500×1000. PowerOverviewMatrix's own comment
 * records that the desk split exists because the old `42vh` table "inner-scrolled
 * at 14 of 37 zones"; a rail showing 6 of 37 is that same defect, worse. The
 * chart is also the one element here that is LESS useful than where it already
 * lives — LiveCharts renders this exact component at full width, one click away
 * behind "Open zone →".
 *
 * RecordChip, for the same budget and a second reason: measured at 47–106 px,
 * and it fires only for whichever zones hold a 7-day record (10 of 37 the day
 * this was measured), so the table would gain and lose rows as the user clicks
 * around. A conditional element is affordable in a full-width panel and not in a
 * rail on a fixed budget. Records too are behind "Open zone →" (RecordsPanel).
 *
 * The general rule both cases teach: in this card, height must not depend on
 * WHICH zone is selected or on whether a feed has answered yet. A child whose
 * loading state is a different height than its loaded state (MiniMixCard's was:
 * `px-3 py-8` placeholder vs `px-1 py-2` + 110 px chart, a 45 px delta, and
 * useFetchWithError clears `data` on every url change) makes the table twitch on
 * EVERY click, which is the same defect as RecordChip at 100 % of clicks.
 */
export default function ZoneDetailCard({ zone, onOpenZone }) {
  const { data, loading, error } = useFetchWithError(`${API}/power/overview`, { pollMs: POLL_FAST_MS })
  const row = data?.zones?.find((z) => z.zone === zone)

  if (error && !row)
    return (
      <div className="shrink-0 border border-red-500/20 bg-surface rounded px-3 py-2">
        <div className="font-mono text-[10px] text-red-400">ZONE DETAIL // FETCH ERROR</div>
      </div>
    )
  if (!row)
    return (
      <div className="shrink-0 border border-border bg-surface rounded px-3 py-2">
        <div className={`font-mono text-[10px] ${loading ? 'text-neutral-600 animate-pulse' : 'text-neutral-500'}`}>
          {loading ? 'Loading zone detail…' : `No overview row for ${zone} — nothing to detail.`}
        </div>
      </div>
    )

  const st = ZONE_STATE[row.state] || ZONE_STATE.CALM
  return (
    // NO `overflow-hidden` on this frame, unlike the panels it sits under. Those
    // are 380–580 px tall and clip nothing that matters; this card is ~160 px, so
    // the clip landed inside its own ⓘ — 154 of the popover's 283 px cut off mid
    // sentence, right after "Residual: load − wind − solar…", taking the σ
    // paragraph that explains the "+0.7σ vs norm" printed on every tile. The
    // ancestor is hidden, not scrollable, so the text was unreachable, not just
    // out of view. All the class bought was clipping a header border against a
    // 4 px radius. A popover on a short card outranks a rounded corner.
    <div className="shrink-0">
      <div className={`border ${st.border} bg-surface rounded shadow-sm`}>
        <div className="flex items-center gap-2 px-3 py-1.5 border-b border-border/60">
          <span
            className={`inline-flex items-center gap-1 shrink-0 font-mono text-[11px] font-bold ${st.text}`}
            title={`${row.state} — how far this zone sits from its own norm`}
          >
            <span className={`w-2 h-2 rounded-sm ${st.dot}`} />
            <span aria-hidden="true">{st.code}</span>
            <span className="sr-only">{row.state}</span>
          </span>
          <span className="font-mono text-[12px] font-semibold text-neutral-200 truncate" title={row.zone_label || zone}>
            {row.zone_label || zone}
          </span>
          <InfoPopover text={CARD_INFO(data.baseline_days)} />
          {/* The date the NUMBERS below are for — always on screen, replaced by
              the louder chip when the row is stale. Overview rows carry no
              `age_days`, so this renders a bare STALE; the situation block below
              has the age and prints it. */}
          <FreshnessCaption meta={row} dense />
          {/* The deliberate door: a row click is a look, THIS navigates. */}
          <button
            onClick={onOpenZone}
            title={`Open the full power desk for ${row.zone_label || zone}`}
            className="ml-auto shrink-0 border border-border rounded px-2 py-0.5 font-mono text-[9px] text-neutral-400 hover:text-cyan-glow hover:border-cyan-glow/40 transition-colors"
          >
            Open zone <span className="text-cyan-glow">→</span>
          </button>
        </div>

        <div className="px-3 py-2 space-y-1.5">
          <ZoneSituation zone={zone} />
          <div className="grid grid-cols-3 gap-2">
            <Stat
              label="Day-ahead"
              value={row.price_close != null ? `€${row.price_close.toFixed(0)}` : '—'}
              sub={sigma(row.price_close, row.price_z)}
              color={zColor(row.price_z)}
            />
            <Stat
              label="Residual"
              value={row.residual_gw != null ? `${row.residual_gw.toFixed(1)} GW` : '—'}
              sub={sigma(row.residual_gw, row.residual_z)}
              color={zColor(row.residual_z)}
            />
            {/* An unreliable share is blank WITH a reason — the feed is too
                incomplete to divide by, which is coverage, not a zero. */}
            <Stat
              label="Renewables"
              value={row.renewable_reliable === false || row.renewable_share == null
                ? '—' : `${Math.round(row.renewable_share * 100)}%`}
              sub={row.renewable_reliable === false ? 'feed incomplete' : 'wind + solar of load'}
            />
          </div>
        </div>
      </div>
    </div>
  )
}
