import { PathLayer } from '@deck.gl/layers'
import { PathStyleExtension } from '@deck.gl/extensions'
import {
  ZONE_COORDS, OUTAGE_DASH_RUNNING, OUTAGE_DASH_UPCOMING, OUTAGE_WIDTH, OUTAGE_CASING_PX, fmtTs,
} from '../constants'

// ── Transmission-outage overlay (GET /api/power/outages/transmission, A78) ────
// The feed is per EVENT (hundreds of them) but the map draws per BORDER: one
// straight dashed chord per canonical (zone_a, zone_b) pair, carrying every
// event on it. This layer is an OVERLAY, not a choropleth fill — it never
// touches the FILLS registry, it hangs off its own `overlays.outages` toggle.

// Per-pair severity precedence, worst first: a forced outage that is RUNNING
// beats a forced one still to come, and any forced beats anything else. Only
// the winner's `kind` colours the chord and only its asset is named in the
// tooltip. The colour split is forced-vs-everything-else, so a business type
// the backend could not map to a kind lands in the quiet bucket and its raw
// value is shown verbatim in the tooltip — never dressed up as "forced". The
// COUNTS split three ways for the same reason (see below): only A53 may be
// called "planned".
const rank = (e) => (e.kind === 'forced' ? (e.running_now ? 3 : 2) : 1)

// Counts carry their unit in the name: `total`/`drawable`/`undrawable`/
// `running` are EVENTS, `borders`/`*Borders` are chords on the map. The legend
// prints both, so a nameless mix would silently misreport one of them.
const EMPTY = {
  paths: [],
  counts: {
    total: 0, drawable: 0, undrawable: 0, running: 0,
    borders: 0, forcedBorders: 0, plannedBorders: 0, otherBorders: 0,
  },
}

// Module-level, not per call: this runs again on every theme flip (the palette
// is one of the memo's inputs), and re-printing the same dozen lines each time
// buries the console instead of informing it. A genuinely NEW unplaceable pair
// still has a new key and still warns.
const warnedPairs = new Set()

// One instance for every layer build (deck.gl compares extensions with
// equals(), so a fresh one per call would be harmless — but constants are
// hoisted everywhere else in powermap/, and this is one).
const DASH_EXTENSIONS = [new PathStyleExtension({ dash: true })]

/**
 * Aggregate the event feed into one drawable path per border.
 *
 * Undrawable events are COUNTED, never silently dropped: `counterparty_mapped:
 * false` means the A78 message named a counterparty outside the bidding-zone
 * registry (zone_b is then a raw EIC or null) — there is nothing to draw a line
 * to, but the outage is real and the legend has to say how many are missing.
 * Same for a zone that has no centroid in ZONE_COORDS.
 *
 * Memoize on the payload's identity (useFetchWithError hands out a stable
 * object per fetch): several hundred events must not be re-bucketed per render.
 */
export function buildOutagePaths(feed, pal) {
  const events = feed?.events
  if (!events?.length) return EMPTY
  const byPair = new Map()
  let undrawable = 0
  let running = 0
  for (const e of events) {
    if (e.running_now) running++
    const ca = e.counterparty_mapped ? ZONE_COORDS[e.zone_a] : null
    const cb = e.counterparty_mapped ? ZONE_COORDS[e.zone_b] : null
    if (!ca || !cb) {
      undrawable++
      const key = `${e.zone_a}-${e.zone_b}` // one line per distinct pair, not per event
      if (!warnedPairs.has(key)) {
        warnedPairs.add(key)
        console.warn(
          e.counterparty_mapped
            ? `PowerMap outages: no coordinates for border ${key}`
            : `PowerMap outages: counterparty of ${e.zone_a} is outside the zone registry (${e.zone_b}) — counted, not drawn`
        )
      }
      continue
    }
    const key = `${e.zone_a}~${e.zone_b}`
    let p = byPair.get(key)
    if (!p) {
      p = { key, zone_a: e.zone_a, zone_b: e.zone_b, path: [ca, cb], events: [], worst: null, worstRank: 0, running: 0 }
      byPair.set(key, p)
    }
    p.events.push(e)
    if (e.running_now) p.running++
    const r = rank(e)
    // STRICTLY greater, so a tie keeps the FIRST event of the winning tier —
    // and the API already sorts running-first, then most-constrained
    // (available_mw ascending, nulls last). The survivor is therefore the one
    // worth naming, without this file re-implementing that ordering.
    if (r > p.worstRank) { p.worstRank = r; p.worst = e }
  }
  let forcedBorders = 0
  let plannedBorders = 0
  const paths = [...byPair.values()].map((p) => {
    const isForced = p.worst.kind === 'forced'
    if (isForced) forcedBorders++
    else if (p.worst.kind === 'planned') plannedBorders++ // anything else stays "other"
    const width = isForced ? OUTAGE_WIDTH.forced : OUTAGE_WIDTH.planned
    // "Running" is a property of the BORDER, not of its worst event: if
    // anything on this line is out right now the chord reads as out now,
    // and the tooltip carries which event that is.
    const dash = p.running > 0 ? OUTAGE_DASH_RUNNING : OUTAGE_DASH_UPCOMING
    return {
      ...p,
      color: [...(isForced ? pal.outage.forced : pal.outage.planned), 255],
      width,
      dash,
      // The casing's dash, rescaled by width/(width + casing) so its ABSOLUTE
      // period matches the coloured stroke's — deck.gl dash lengths are
      // RELATIVE TO STROKE WIDTH (see makeOutageLayers). Unscaled it ran 1.67×
      // (forced) / 2.33× (planned) longer, the two patterns drifted out of
      // phase, and the casing stopped being a contour: most of each coloured
      // dash sat on bare choropleth while the casing read as a dashed line in
      // its own right (planned chords looked white-with-dark-speckles on the
      // light surface — exactly the pal.zoneLine collision stone-700 was chosen
      // to avoid). dashJustified stays in step too: the round(vPathLength/
      // unitLength) it divides by is the same integer once the period matches.
      // Precomputed beside `dash` — both dash decisions in one place, and with
      // two possible widths × two patterns there are only ever two results.
      casingDash: dash.map((v) => (v * width) / (width + OUTAGE_CASING_PX)),
    }
  })
  return {
    paths,
    counts: {
      total: events.length,
      drawable: events.length - undrawable,
      undrawable,
      running,
      borders: paths.length,
      forcedBorders,
      plannedBorders,
      // Kinds the backend passed through raw (business types beyond A53/A54).
      // Kept apart so the legend can never file them under "planned".
      otherBorders: paths.length - forcedBorders - plannedBorders,
    },
  }
}

// Tooltip body for one chord — plain html lines, wired into tooltip.js as an
// overlay resolver (an overlay's tooltip is its own concern; it must not ride
// on the FILLS registry's per-zone `tooltipLines`). Returns null for anything
// that is not one of our paths, so the resolver chain falls through.
export function outageTooltip(o) {
  if (!o?.worst || !Array.isArray(o.events)) return null
  const w = o.worst
  const more = o.events.length - 1
  const name = w.asset_name || w.asset_eic || 'unnamed asset'
  // available_mw is what the asset can STILL carry. ENTSO-E publishes no
  // capacity baseline for A78, so the amount LOST is not derivable and is
  // never stated — "reduced to X" only ever names the remaining figure.
  const avail = w.available_mw == null
    ? 'no capacity figure published'
    : w.available_mw === 0
      ? 'fully out — 0 MW available'
      : `reduced to ${w.available_mw.toFixed(0)} MW still available`
  const when = w.running_now ? 'running now' : `starts ${fmtTs(w.start_utc)} UTC`
  // Only our own API values are interpolated — no user-controlled strings.
  return [
    `<div style="font-weight:600">${o.zone_a}↔${o.zone_b} · line outage</div>`,
    `<div>${name}${more > 0 ? ` +${more} more` : ''}</div>`,
    `<div>${[w.asset_type, w.kind].filter(Boolean).join(' · ')}</div>`,
    `<div>${avail}</div>`,
    `<div>${fmtTs(w.start_utc)} → ${fmtTs(w.end_utc)} UTC</div>`,
    `<div style="opacity:.55">${when}${o.running > 0 && !w.running_now ? ` · ${o.running} other out now` : ''}</div>`,
    // Same affordance as the arcs' tooltip — the chord carries the same click.
    '<div style="opacity:.55">click → border detail</div>',
  ].join('')
}

/**
 * Two PathLayers, bottom first: a wider casing in pal.labelOutline (the repo's
 * "readable over anything" ink) and the coloured stroke on top. The casing is
 * what makes the overlay legible over ANY fill — the tech fill alone puts 21
 * fuel hues under it — and it shares the dash pattern, so the gaps still let
 * the choropleth through instead of laying an opaque rail across it.
 * Only the top layer is pickable; the casing would just steal its own hovers.
 */
export function makeOutageLayers({ paths, pal, onBorderSelect }) {
  const common = {
    data: paths,
    getPath: (d) => d.path,
    widthUnits: 'pixels',
    // deck.gl dash lengths are RELATIVE TO STROKE WIDTH, not absolute pixels:
    // PathLayer's vertex shader divides path space by the stroke width, so
    // [4,3] on a 1.5 px stroke paints 6 px on / 4.5 px off. Handing the SAME
    // array to the wider casing therefore stretches its period — which is why
    // it draws `casingDash` (rescaled in buildOutagePaths) instead of `dash`.
    // dashJustified then stretches the pattern so both ends of a chord finish
    // on a dash.
    extensions: DASH_EXTENSIONS,
    dashJustified: true,
    dashGapPickable: true,
    getDashArray: (d) => d.dash,
    // Keyed by ACCESSOR NAME, so this entry keeps covering getDashArray on the
    // casing layer below, which overrides the accessor but inherits these.
    updateTriggers: { getPath: [paths], getColor: [paths], getWidth: [paths], getDashArray: [paths] },
  }
  return [
    new PathLayer({
      ...common,
      id: 'outage-casing',
      pickable: false,
      getColor: pal.labelOutline,
      getWidth: (d) => d.width + OUTAGE_CASING_PX,
      // Overrides the shared accessor with the width-rescaled pattern, so the
      // casing's ABSOLUTE dash period matches the coloured stroke's — the whole
      // reasoning sits on casingDash in buildOutagePaths.
      getDashArray: (d) => d.casingDash,
    }),
    new PathLayer({
      ...common,
      id: 'outage-lines',
      pickable: true,
      // No autoHighlight, unlike the arcs: pal.highlight is a wash at alpha 60
      // and REPLACES the colour, which on a 1.5 px stroke reads as the line
      // vanishing under the cursor. The tooltip is the hover feedback here.
      getColor: (d) => d.color,
      getWidth: (d) => d.width,
      // Same click as the flow arcs, deliberately: these chords sit ABOVE the
      // zones layer and are pickable (dashGapPickable + pickingRadius 4, so the
      // corridor is ~11 px wide), and deck.gl does not fall through to the
      // layer underneath — without this, turning the overlay on would lay a
      // few dozen dead stripes across the map where a zone click stops working.
      // The pair is the /borders canonical sorted pair (same convention as the
      // arcs), so BordersPanel opens the matching row. If a pair has no row
      // (none of today's 37 do, but A78 names the counterparty, not the border)
      // the panel still expands and scrolls into view with nothing pre-opened —
      // degraded, never dead, and never throwing.
      onClick: ({ object }) => { if (object) onBorderSelect?.(object.zone_a, object.zone_b) },
    }),
  ]
}
