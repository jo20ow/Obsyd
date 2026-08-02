import { useMemo } from 'react'
import { rgbCss } from './palettes'
import { UTIL_MID, UTIL_HIGH } from './constants'
import { TECH_FUEL, techHex } from './tech'

/* Flow-arc legend — swatches read straight from pal.arc (they cannot
   drift from the layer) and thresholds from UTIL_MID/UTIL_HIGH. Only the
   ■ carries the series color; the label text stays neutral ink (amber-600
   text at 9px would sit at ~2.9:1 on the light surface). Only shown while
   the arcs themselves are on (index.jsx couples it to overlays.flows). */
export function FlowArcLegend({ pal, atLatest }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 px-4 py-1.5 border-t border-border font-mono text-[9px] text-neutral-600">
      <span>flows: width ∝ GW (√, caps at 5)</span>
      <span><span style={{ color: rgbCss(pal.arc.low) }}>■</span> &lt;{UTIL_MID}%</span>
      <span><span style={{ color: rgbCss(pal.arc.mid) }}>■</span> {UTIL_MID}–{UTIL_HIGH}%</span>
      <span><span style={{ color: rgbCss(pal.arc.high) }}>■</span> ≥{UTIL_HIGH}% of NTC</span>
      <span><span style={{ color: rgbCss(pal.arc.proxy) }}>■</span> no NTC (p95)</span>
      <span><span style={{ color: rgbCss(pal.arc.none) }}>■</span> no reading</span>
      <span>grey = context (thin)</span>
      <span>solid end = importer</span>
      {!atLatest && <span className="text-neutral-500">hidden while scrubbing — arcs show latest only</span>}
    </div>
  )
}

// Price scale row. The bar is the week's CDF axis (left = cheapest observed
// hour, right = most expensive): equal bar length = equal share of the week's
// all-zone hours — that IS the equal-frequency contract. Every stop and tick
// comes from the scale object itself, so the legend cannot drift from the map.
// Ticks under the bar name the p10/p25/p50/p75/p90 prices; endpoints are the
// observed min/max (no clamp — ranks are outlier-robust by construction). The
// zero marker sits at 0 €'s share of the population.
export function PriceScaleLegend({ scale }) {
  // Stops carry their own CDF position (the doubled stop at the zero share
  // renders as a CSS hard edge — see makeQuantileScale.stops).
  const gradient = `linear-gradient(90deg, ${scale
    .stops(12)
    .map(({ pos, rgb }) => `${rgbCss(rgb)} ${(pos * 100).toFixed(2)}%`)
    .join(', ')})`
  const q = scale.quantiles
  // Flat weeks collapse neighbouring quantiles onto the same rounded label —
  // render each label once (first position wins) instead of stacking "82 82".
  const ticks = []
  if (q) {
    for (const [p, v] of [[10, q.p10], [25, q.p25], [50, q.p50], [75, q.p75], [90, q.p90]]) {
      const label = v.toFixed(0)
      if (!ticks.length || ticks[ticks.length - 1].label !== label) ticks.push({ p, label })
    }
  }
  return (
    <span
      className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5"
      title="Fixed equal-frequency scale across the shown week: colors spread by rank among all zones × hours, not by € distance — tooltips carry exact €. Ticks mark the p10/p25/median/p75/p90 prices."
    >
      <span className="text-neutral-500">{scale.lo.toFixed(0)}</span>
      <span className="flex flex-col gap-px">
        <span className="relative block h-2 w-40 rounded overflow-hidden" style={{ background: gradient }}>
          {scale.negShare > 0 && (
            <span
              className="absolute top-0 h-2 w-px bg-neutral-400"
              style={{ left: `${scale.negShare * 100}%` }}
              title="0 €/MWh"
            />
          )}
        </span>
        {ticks.length > 0 && (
          <span className="relative block h-2.5 w-40 text-[8px] leading-none text-neutral-500">
            {ticks.map(({ p, label }) => (
              <span key={p} className="absolute -translate-x-1/2" style={{ left: `${p}%` }} title={`p${p}`}>
                {label}
              </span>
            ))}
          </span>
        )}
      </span>
      <span className="text-neutral-500">{scale.hi.toFixed(0)} €/MWh</span>
      <span className="text-neutral-600">equal-frequency scale · week&apos;s all-zone hours</span>
      {scale.negShare > 0 && <span className="text-violet-300">violet = negative</span>}
    </span>
  )
}

// Categorical legend for the price-setting-tech fill: only the technologies
// actually on the map right now, each with how many zones it holds (biggest
// first — the reader's first question is "what is setting the price in Europe
// this hour"). Swatch colours come from tech.js, i.e. from the same canonical
// fuel palette the generation-mix charts use, so the legend cannot drift from
// the map. The honesty line is NOT the API's raw `note` (never rendered raw —
// PR #138 rule): the coverage count is structured here, one compact sentence
// sits in the map's ⓘ, and the full method is the HOW TO READ glossary entry.
export function TechLegend({ extra, extraError }) {
  const { rows, shown, total, tension, gapText } = useMemo(() => {
    const zones = extra?.zones || []
    const counts = new Map() // tech -> { label, n }
    let unmapped = 0 // tech outside the closed set: painted no-data, counted as a gap
    let tensionN = 0
    for (const z of zones) {
      if (!TECH_FUEL[z.tech]) {
        unmapped++
        continue // counted below, and NOT in `tension` — it stays in step with `shown`
      }
      if (z.consistency === 'tension') tensionN++
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
  if (extra.available === false || rows.length === 0) {
    return <span className="text-neutral-600">price-setting tech · no data</span>
  }
  return (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
      {rows.map(({ tech, label, n }) => (
        <span key={tech}>
          <span style={{ color: techHex(tech) }}>■</span> {label} ×{n}
        </span>
      ))}
      <span className="text-neutral-600">
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
      {/* Payload on screen but the last refresh failed — the SWR cache is
          still serving it, so the colours are real, just not newly confirmed. */}
      {extraError && <span className="text-red-400">refresh failed — showing last payload</span>}
    </span>
  )
}

// Grid-state trio for the state fill.
export function StateLegend({ pal }) {
  return (
    <span className="flex items-center gap-3">
      <span style={{ color: pal.stateLegend.CALM }}>■ CALM</span>
      <span style={{ color: pal.stateLegend.ELEVATED }}>■ ELEVATED</span>
      <span style={{ color: pal.stateLegend.STRESSED }}>■ STRESSED</span>
    </span>
  )
}
