import { useMemo } from 'react'
import FreshnessCaption from '../../FreshnessCaption'
import { TECH_FUEL, techHex } from '../tech'

// Categorical legend for the price-setting-tech fill: only the technologies
// actually on the map right now, each with how many zones it holds (biggest
// first — the reader's first question is "what is setting the price in Europe
// this hour"). Swatch colours come from tech.js, i.e. from the same canonical
// fuel palette the generation-mix charts use, so the legend cannot drift from
// the map. The honesty line is NOT the API's raw `note` (never rendered raw —
// PR #138 rule): the coverage count is structured here, one compact sentence
// sits in the map's ⓘ, and the full method is the HOW TO READ glossary entry.
export default function TechLegend({ extra, extraError }) {
  const { rows, shown, total, tension, staleN, gapText } = useMemo(() => {
    const zones = extra?.zones || []
    const counts = new Map() // tech -> { label, n }
    let unmapped = 0 // tech outside the closed set: painted no-data, counted as a gap
    let tensionN = 0
    let staleZones = 0
    for (const z of zones) {
      if (!Object.hasOwn(TECH_FUEL, z.tech)) {
        unmapped++
        continue // counted below, and NOT in the tallies — they stay in step with `shown`
      }
      if (z.consistency === 'tension') tensionN++
      if (z.stale) staleZones++
      const hit = counts.get(z.tech)
      if (hit) hit.n++
      else counts.set(z.tech, { label: z.tech_label || z.tech, n: 1 })
    }
    const list = [...counts]
      .map(([tech, e]) => ({ tech, ...e }))
      .sort((a, b) => b.n - a.n || a.label.localeCompare(b.label))
    // The backend keeps `missing` (no attributable hour) APART from `errors`
    // (the zone's compute raised) precisely so a failure can never pass itself
    // off as an honest data gap — compute_marginal_overview's docstring. Both
    // paint the same grey, so the legend is where that guarantee would die:
    // count them SEPARATELY. A tech outside the closed set is a UI-side gap,
    // so it joins "no data", never "failed".
    const noData = (extra?.missing?.length || 0) + unmapped
    const failed = extra?.errors?.length || 0
    // Denominator = every zone the endpoint considered (answered + missing +
    // errored) — never a hardcoded 37, the zone list grows.
    return {
      rows: list,
      shown: zones.length - unmapped,
      total: zones.length + (extra?.missing?.length || 0) + failed,
      tension: tensionN,
      staleN: staleZones,
      gapText: [noData && `${noData} no data`, failed && `${failed} failed`].filter(Boolean).join(' · '),
    }
  }, [extra])

  // A dead feed must SAY so: the map underneath is painted all no-data grey,
  // and "loading…" forever would be exactly the silent failure the desk
  // forbids. With a cached payload still on screen the fill keeps rendering
  // and the failure is reported beside it (see below) instead of blanking it.
  if (extraError && !extra) {
    return <span className="text-red-400">price-setting tech // FETCH ERROR — map shows no data</span>
  }
  if (!extra) return <span className="text-neutral-600">price-setting tech · loading…</span>
  // Nothing to show AND nothing to explain — the genuinely empty case. When
  // every zone's compute raised, `available` is false too (it is bool(zones)),
  // but `errors` holds all of them: fall THROUGH so the line below reports
  // "37 failed". An all-zones failure reported as an honest data gap is the
  // exact confusion the backend splits those two lists to prevent.
  if (rows.length === 0 && !gapText) {
    return <span className="text-neutral-600">price-setting tech · no data</span>
  }
  return (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
      {rows.map(({ tech, label, n }) => (
        <span key={tech}>
          <span style={{ color: techHex(tech) }}>■</span> {label} ×{n}
        </span>
      ))}
      <span
        className="text-neutral-600"
        title="Each zone is attributed at ITS OWN newest hour and those hours differ across the map — hover a zone for the hour it was read at."
      >
        estimated — fixed merit order, not computed costs · {shown}/{total} zones
      </span>
      {gapText && (
        <span
          className="text-neutral-600"
          title="Grey zones, kept apart: 'no data' = no attributable hour in the window; 'failed' = the zone's computation raised. A failure never counts as an honest data gap."
        >
          grey: {gapText}
        </span>
      )}
      {/* The tension tally is REPORTED, not painted: those zones keep their
          technology's colour (never restyled, never reclassified). Without it
          the caveat would only exist inside a tooltip nobody hovers. */}
      {tension > 0 && (
        <span
          className="text-neutral-600"
          title="Zones whose price sits outside the coarse band their attributed technology implies — the canary that the static merit order is off. Reported, never reclassified; hover a zone to see it."
        >
          {tension} in tension
        </span>
      )}
      {/* Per-zone staleness, which the top-level chip CANNOT carry: `as_of` is
          the newest zone across the map, so three zones stuck six days back
          would hide behind 34 current ones while painting the same saturated
          colour. Counted here, dated per zone in the tooltip. */}
      {staleN > 0 && (
        <span
          className="text-orange-400"
          title="Zones whose newest attributed hour is past the freshness threshold. They keep their technology's colour — hover one for the hour it was actually read at."
        >
          {staleN} stale
        </span>
      )}
      {/* The repo's standard as_of / STALE chip, on the payload's own
          freshness triple — best-of across zones, which is why the per-zone
          count above exists beside it. */}
      <FreshnessCaption meta={extra} />
      {/* Payload on screen but the last refresh failed — the SWR cache is
          still serving it, so the colours are real, just not newly confirmed. */}
      {extraError && <span className="text-red-400">refresh failed — showing last payload</span>}
    </span>
  )
}
