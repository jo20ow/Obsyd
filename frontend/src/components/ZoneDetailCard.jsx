import useFetchWithError from '../hooks/useFetchWithError'
import { POLL_FAST_MS } from '../utils/poll'
import { ZONE_STATE, zColor } from '../utils/zoneState'
import { InfoPopover } from './Panel'
import MiniMixCard from './MiniMixCard'

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
const CARD_INFO = (baselineDays) => (
  'This zone, as the desk reads it right now. '
  + 'Day-ahead: the auction price cleared the day before for this delivery day — the DAILY MEAN across its hours, a settled market price, not a forecast. '
  + 'Residual: load − wind − solar, the gap conventional plants must fill. '
  + 'Renewables: wind + solar as a share of load, blank when the feed is too incomplete to divide by. '
  + `σ: distance from this zone's own ${baselineDays ? `${baselineDays}-day` : 'trailing'} norm — amber past 2σ, red past 3σ. Descriptive, never a forecast. `
  + 'Flags and the one-line read come from the zone situation feed and carry their own timestamp.'
)

function StaleChip({ asOf, ageDays }) {
  return (
    <span
      className="shrink-0 font-mono text-[8px] tracking-wide text-orange-400 border border-orange-500/30 rounded px-1 py-px"
      title={`Latest data ${asOf} — this zone's feed may be stalled`}
    >
      STALE{ageDays != null ? ` · ${ageDays}d` : ''}
    </span>
  )
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
        {data.stale && <StaleChip asOf={data.as_of} ageDays={data.age_days} />}
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
 * box, a flag row that renders even when empty — so the card measures 342 px for
 * every one of the 37 zones and switching zones does not re-flow the table.
 * The single moving part is how many flags the backend raises: the synthetic
 * worst case (3 flags wrapping to 3 lines + a 2-line headline) reaches 385 px at
 * 1280 wide and costs the table one row. That is the right thing to spend a row
 * on, and it cannot overflow the column — the table yields, this card does not.
 *
 * That constant is also why RecordChip is NOT here, though it is narrow enough
 * and stays silent without a fresh record. Measured: it costs 47–106 px when it
 * does fire, which took the table from 6 visible rows to 3 at 1280×800 (2 at
 * 1024×768) — and it fires for whichever zones happen to hold a 7-day record
 * (10 of 37 the day this was measured), so the table would gain and lose half
 * its rows as the user clicks around. A conditional element is affordable in a
 * full-width panel and not in a rail on a fixed budget. Fresh records stay one
 * click away behind "Open zone →", where RecordsPanel already carries them.
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
    // gap-3 matches the rail's own gap, so the three boxes read as one stack.
    <div className="shrink-0 flex flex-col gap-3">
      <div className={`border ${st.border} bg-surface rounded overflow-hidden shadow-sm`}>
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
              the louder chip when the row is stale. */}
          {row.stale
            ? <StaleChip asOf={row.as_of} />
            : <span className="shrink-0 num text-[8px] text-neutral-700">{row.as_of}</span>}
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

      <MiniMixCard title="Generation mix" zone={zone} height={110} />
    </div>
  )
}
