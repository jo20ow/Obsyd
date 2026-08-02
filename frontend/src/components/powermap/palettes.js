// ── Theme-aware palettes ──────────────────────────────────────────────────────
// The map lives inside a themed panel; a hard-coded dark map inside the light
// desk was exactly what read as cheap. Price is a DIVERGING scale around
// 0 €/MWh (negative prices are a distinct market state, not just "cheap"):
// both poles start at a neutral midpoint and gain chroma/contrast toward their
// hue — on the dark surface they brighten, on the light surface they darken,
// so "far from zero" always means "more contrast vs the surface". Poles and
// status trios are validator-checked per surface (dark: cyan/violet ΔE 12.6;
// light: teal/violet ΔE 55.9; the light amber's 2.69:1 contrast WARN is
// relieved by the worded legend + the overview table beside the map — a darker
// amber collapses into red for deuteranopia at ΔE 3.8, so it stays).
export const PALETTES = {
  dark: {
    surface: '#06060a',
    mid: [26, 26, 36],
    posPole: [103, 232, 249], // cyan-300: expensive = bright
    negPole: [196, 181, 253], // violet-300: negative prices
    contextFill: [10, 10, 16, 255],
    contextLine: [42, 42, 58, 160],
    zoneLine: [6, 6, 10, 220],
    state: { CALM: [74, 222, 128], ELEVATED: [250, 204, 21], STRESSED: [248, 113, 113] },
    stateLegend: { CALM: '#4ade80', ELEVATED: '#facc15', STRESSED: '#f87171' },
    // IN-SCOPE zone with no value (categorical fills) — a slate-700 that is
    // NOT pal.mid: mid is the diverging price scale's MIDPOINT, never a
    // validated no-data colour, and pressed into that job it collapses into
    // the context countries (on the LIGHT surface to within ΔE 1) — an enabled
    // bidding zone missing its value then looked exactly like a neighbour that
    // has none by design. Validated (dataviz validator) at the
    // fill's own alpha 215 over #06060a — worst normal-vision pair 15.8 vs
    // Oil, 19.1 vs the context countries, ≥21 vs every other fuel; CVD ≥15.5
    // throughout. The 1.7:1 surface contrast is deliberate (no-data must stay
    // recessive) and relieved by the zone label + tooltip + legend count.
    noData: [51, 65, 85],
    label: [235, 240, 245, 230],
    labelOutline: [6, 6, 10, 255],
    highlight: [103, 232, 249, 60],
    tooltip: { background: '#0a0a12', border: '1px solid #2a2a3a', color: '#d4d4d8' },
    // Flow arcs: an ordinal load ramp (low/mid/high vs NTC) + two deliberate
    // no-signal grays (proxy = no NTC published, none = no flow reading).
    // Validator (dataviz skill) vs #06060a: trio contrast all ≥3:1, CVD ΔE 8.2
    // (≥8 target); amber↔orange normal ΔE 11.2 is adjacent-ordinal-step
    // territory and is relieved by the worded legend + exact util % in the
    // tooltip. The grays are MEANT to read gray.
    arc: { low: [34, 211, 238], mid: [251, 191, 36], high: [251, 146, 60], proxy: [148, 163, 184], none: [100, 116, 139] },
  },
  light: {
    surface: '#f4f5f7',
    mid: [226, 230, 235],
    posPole: [8, 100, 124],   // teal-900: expensive = dark/saturated
    negPole: [109, 40, 217],  // violet-700
    contextFill: [229, 231, 236, 255],
    contextLine: [203, 208, 216, 200],
    zoneLine: [255, 255, 255, 235],
    state: { CALM: [22, 163, 74], ELEVATED: [202, 138, 4], STRESSED: [220, 38, 38] },
    stateLegend: { CALM: '#16a34a', ELEVATED: '#ca8a04', STRESSED: '#dc2626' },
    // No-data on the light surface has to go DARK, not pale: the context
    // countries are already a light gray, and the two fuels it could hide
    // behind (Oil's slate, Hard Coal's warm gray) sit at L* 57 and 72 — the
    // gap above them is too narrow to clear the separation floor. slate-800
    // at alpha 215 over #f4f5f7 reads as a hole in the data: worst
    // normal-vision pair 22.0 vs Oil, 52.5 vs the context countries, ≥25 vs
    // every other fuel; CVD ≥19.8 throughout (dataviz validator).
    noData: [30, 41, 59],
    label: [24, 30, 40, 235],
    labelOutline: [255, 255, 255, 255],
    highlight: [8, 100, 124, 50],
    tooltip: { background: '#ffffff', border: '1px solid #d6dae0', color: '#1f2430' },
    // low is cyan-600 [8,145,178], NOT cyan-700 [14,116,144]: validated better —
    // 3.38:1 contrast on #f4f5f7 passes, and deutan ΔE vs the proxy gray is
    // 15.6 (cyan-700 only manages 11.4).
    // mid is amber-600 [217,119,6], NOT amber-700: the validator showed
    // amber-700 vs orange-700 at deutan ΔE 0.1 / normal 4.1 on #f4f5f7 —
    // indistinguishable. amber-600 lifts the pair to deutan 11.3 / normal
    // 12.7 (CVD target met; adjacent ordinal steps, legend + tooltip relieve).
    arc: { low: [8, 145, 178], mid: [217, 119, 6], high: [194, 65, 12], proxy: [100, 116, 139], none: [148, 163, 184] },
  },
}

export const rgbCss = ([r, g, b]) => `rgb(${r},${g},${b})`
