import FreshnessCaption from '../../FreshnessCaption'
import { rgbCss } from '../palettes'

/* Legend for the A78 transmission-outage overlay. Its own row, like the flow
   arcs' — the overlay is not a fill, so it never shares the footer legend slot
   that the FILLS registry owns. Swatches read straight from pal.outage, counts
   straight from buildOutagePaths, so neither can drift from the map.

   The honesty line is the point of this component: the feed carries events the
   map physically cannot place (a counterparty outside the bidding-zone
   registry has no centroid to draw a line to). They are COUNTED here rather
   than disappearing between the endpoint and the canvas. */
export default function OutageLegend({ pal, counts, meta, error, atLatest }) {
  // A dead feed has to SAY so — the overlay is on, the map draws nothing, and
  // silence would read as "no lines are out", the opposite of the truth.
  if (error && !meta) {
    return (
      <div className="flex flex-wrap items-center gap-x-3 px-4 py-1.5 border-t border-border font-mono text-[9px] text-red-400">
        line outages // FETCH ERROR — no outages drawn
      </div>
    )
  }
  if (!meta) {
    return (
      <div className="flex flex-wrap items-center gap-x-3 px-4 py-1.5 border-t border-border font-mono text-[9px] text-neutral-600">
        line outages · loading…
      </div>
    )
  }
  // The endpoint's own "nothing ingested yet" answer, which carries no events
  // key at all — distinct from a healthy feed that happens to be empty.
  const unavailable = meta.available === false
  const borders = counts.forced + counts.planned
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 px-4 py-1.5 border-t border-border font-mono text-[9px] text-neutral-600">
      {/* Two different units live in this row, so both are spelled out: the
          swatches count BORDERS (one chord each, however many events it
          carries), the tallies below count EVENTS. */}
      <span title="One dashed chord per border — a single border can carry dozens of separate outage events.">
        line outages{meta.horizon_days ? ` (${meta.horizon_days}d)` : ''}: {borders} borders
      </span>
      <span title="Borders whose worst event is a forced (unplanned) outage.">
        <span style={{ color: rgbCss(pal.outage.forced) }}>■</span> forced ×{counts.forced}
      </span>
      <span title="Borders carrying only planned maintenance.">
        <span style={{ color: rgbCss(pal.outage.planned) }}>■</span> planned ×{counts.planned}
      </span>
      <span title="An outage is a window. Tight dash = something on that border is out right now; sparse dash = it only starts later inside the horizon.">
        tight dash = out now · sparse = starts later
      </span>
      <span title="Events whose window covers this moment, across the whole feed — including the ones the map cannot place.">
        {counts.total} events · {counts.running} running now
      </span>
      {/* Nothing vanishes silently: events the map cannot place are named. */}
      {counts.undrawable > 0 && (
        <span
          className="text-neutral-500"
          title="Their A78 message names a counterparty outside the bidding-zone registry (or a zone with no centroid) — there is no line to draw them along, so they are counted here instead."
        >
          only {counts.drawable} drawable
        </span>
      )}
      {unavailable && <span className="text-neutral-500">no A78 messages ingested yet</span>}
      {/* Outages are WINDOWS, not hours: the overlay keeps showing the current
          window while the scrubber sits in the past (the arcs hide instead —
          a latest-hour flow over a past-hour choropleth would lie, a window
          that is open right now stays true whatever hour is painted). */}
      {!atLatest && (
        <span className="text-neutral-500">outages show the current window, not the scrubbed hour</span>
      )}
      <FreshnessCaption meta={meta} />
      {/* Payload on screen but the last refresh failed — the SWR cache is
          still serving it, so the lines are real, just not newly confirmed. */}
      {error && <span className="text-red-400">refresh failed — showing last payload</span>}
    </div>
  )
}
