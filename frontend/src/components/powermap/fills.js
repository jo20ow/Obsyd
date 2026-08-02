import { PriceScaleLegend, StateLegend, TechLegend } from './legends'
import { fmtTs } from './constants'
import { techIndex, techRgb } from './tech'

// Fill registry — one entry per choropleth fill mode, carrying the FULL
// per-fill contract so a new fill lands purely additively here;
// index.jsx never branches on a fill key:
//   key/label      — header toggle button
//   info           — ONE sentence (or two) for the map's ⓘ popover, describing
//                    what THIS fill encodes. MapHeader composes the popover
//                    from these in registry order, so a new fill ships its own
//                    explanation next to its colours — there is no central
//                    string to remember to edit.
//   scrub          — whether the time scrubber applies (grid state is always live)
//   labelText(pt, ctx) — OPTIONAL label string for one point row, ctx as in
//                    getColor; null = no label for this point (price fill skips
//                    zones without a price). ITS PRESENCE is what gives the fill
//                    the ZONES-view TextLayer + the header's LABELS toggle —
//                    there is no separate hasLabels flag to fall out of sync.
//   labelPriority(pt, ctx) — OPTIONAL collision-cull rank: when labels overlap,
//                    the HIGHER value survives (default 0 = let deck.gl's own
//                    order decide). Must stay within deck.gl's −1000..1000.
//   getColor(zoneKey, ctx) — base RGB for one zone, ctx = {byZone, scale, pal,
//                    extra}; null = zone has no data (index falls back to
//                    pal.contextFill). `extra` is this fill's own lazily
//                    fetched payload (EXTRA_BY_FILL in useMapData), null for
//                    fills that don't register one.
//   alpha          — layer opacities: .zone (choropleth) / .point (dots)
//   Legend         — footer legend row component; always receives
//                    {scale, pal, extra, extraError} (a dead per-fill feed is
//                    the legend's job to report — panels never fail silently)
//   tooltipLines(zone, ctx) — OPTIONAL extra tooltip lines for a zone (same
//                    ctx as getColor), so the fill's own reading stays next to
//                    its colors and tooltip.js branches on no fill key either
//   triggers(ctx)  — OPTIONAL updateTriggers tail: the identities THIS fill's
//                    colors depend on beyond the shared [fill, effRows, theme]
export const FILLS = [
  {
    key: 'price',
    label: 'DAY-AHEAD €/MWh',
    info: (
      'DAY-AHEAD shades each zone by its cleared auction price. IMPORTANT: ONE HOUR at a time (the '
      + 'hour on the slider below), not the whole day — so a zone can read €0 here at 08:00 while the '
      + 'all-zones table shows a positive daily mean; drag the slider to move through the hours. '
      + 'Fixed equal-frequency colour scale across the shown week: colours spread by rank among the '
      + "week's all-zone hours, not by € distance (tooltips carry exact €) — violet = negative prices "
      + '(a distinct state, not just cheap), brighter cyan = more expensive.'
    ),
    scrub: true,
    labelText: (p) => (p.price == null ? null : `${p.label} ${Math.round(p.price)}`),
    // |price| so the EXTREME zones survive the collision cull — a €300 spike or
    // a €−40 solar dump is exactly the label you want in the dense Benelux
    // cluster, not whichever zone happens to draw first. Clamped: spike hours
    // exceed 1000 €/MWh and deck.gl's priority range ends there.
    labelPriority: (p) => Math.min(Math.abs(p.price ?? 0), 1000),
    getColor: (zone, { byZone, scale }) => {
      const z = byZone.get(zone)
      if (!z) return null
      return scale.color(z.price_close)
    },
    alpha: { zone: 235, point: 240 },
    Legend: PriceScaleLegend,
    // The scale object is memoized on [snap, rows, pal] in index.jsx — its
    // identity IS the domain version, so a new week population repaints.
    triggers: ({ scale }) => [scale],
  },
  {
    key: 'state',
    label: 'GRID STATE',
    info: (
      'GRID STATE shades each zone by how far it sits from its own 30-day norm — CALM / ELEVATED / '
      + 'STRESSED. A deviation vs history, not a forecast, and always the latest reading (no slider: '
      + 'the state is only ever "now").'
    ),
    scrub: false,
    // State is already the color — the label only answers "which zone is that",
    // so it's the bare zone code. Cull priority = severity: a STRESSED zone's
    // name must not lose the overlap fight to a CALM neighbour.
    labelText: (p) => p.label,
    labelPriority: (p) => ({ CALM: 0, ELEVATED: 1, STRESSED: 2 })[p.state] ?? 0,
    getColor: (zone, { byZone, pal }) => {
      const z = byZone.get(zone)
      if (!z) return null
      return pal.state[z.state] || pal.mid
    },
    alpha: { zone: 215, point: 240 },
    Legend: StateLegend,
    triggers: () => [],
  },
  {
    key: 'tech',
    label: 'PRICE-SETTING TECH',
    info: (
      'PRICE-SETTING TECH shades each zone by the technology estimated to have set its latest price '
      + '— a fixed-merit-order attribution in the generation-mix fuel colours, not a cost model (a '
      + 'slate zone is in scope but has no value, and each zone carries its own hour — hover it; '
      + 'glossary in HOW TO READ).'
    ),
    // No scrubber: the estimate is computed on read for the LATEST hour only —
    // there is no per-hour matrix to scrub, and back-dating one colour across
    // the week would be a lie. Flow arcs stay on (latest-on-latest is honest).
    scrub: false,
    // As on the state fill the colour IS the answer, so the label only says
    // WHICH zone. The technology name would need ~30 characters ("Flexible
    // hydro (opportunity cost)") and belongs in the tooltip + legend.
    // No labelPriority: no technology outranks another (unlike price extremes
    // or state severity), so the default flat rank is exactly right.
    labelText: (p) => p.label,
    getColor: (zone, { extra, pal }) => {
      const row = techIndex(extra).get(zone)
      // Zone absent from the payload (missing / errored / feed still loading)
      // or a tech outside the closed set → the validated no-data slate (NOT
      // pal.mid, which composites into the context countries on the light
      // surface). The gap SHOWS, and the legend counts it.
      return (row && techRgb(row.tech)) || pal.noData
    },
    alpha: { zone: 215, point: 240 },
    Legend: TechLegend,
    tooltipLines: (zone, { extra }) => {
      const row = techIndex(extra).get(zone)
      if (!row) return ['price-setting tech: no data']
      const share = row.share_pct != null ? ` · ${row.share_pct.toFixed(0)}% of gen` : ''
      const lines = [`${row.tech_label || row.tech} · sets price (est.)${share}`]
      // Every zone is attributed at ITS OWN newest hour, and those hours
      // DIFFER across the map (the mixed-timestamp hazard) — so the hour rides
      // in the tooltip per zone, and a zone past the freshness threshold says
      // STALE instead of passing six-day-old colour off as "right now".
      lines.push(`attributed ${fmtTs(row.ts_utc)} UTC${row.stale ? ' · ⚠ STALE' : ''}`)
      // 'tension' = the price sits outside the coarse band this technology
      // would imply. REPORTED, never restyled and never reclassified — the
      // zone keeps its technology colour; the flag is the canary that the
      // static merit order is off, not a correction of it.
      if (row.consistency === 'tension') lines.push('⚠ tension — price outside the expected band')
      return lines
    },
    // The payload's identity: repaint once the lazy overview lands.
    triggers: ({ extra }) => [extra],
  },
]
