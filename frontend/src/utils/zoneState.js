// The descriptive desk state (CALM / ELEVATED / STRESSED) as the backend
// derives it — how far a zone sits from its OWN trailing norm, never a forecast.
//
// Named once because the desk rail now shows the same zone's state TWICE at the
// same time: the matrix row and the detail card directly under it. Two private
// copies of "amber means ELEVATED" is exactly how a row and its own card end up
// disagreeing about the zone the user just clicked.
//
// `code` is the one-letter carrier for the compact rail: green/amber/red dots
// alone are the textbook red-green CVD collision, and the compact table has no
// room for the word.
export const ZONE_STATE = {
  CALM: { text: 'text-green-glow', dot: 'bg-green-glow', border: 'border-green-500/30', code: 'C' },
  ELEVATED: { text: 'text-yellow-400', dot: 'bg-yellow-400', border: 'border-yellow-500/30', code: 'E' },
  STRESSED: { text: 'text-red-400', dot: 'bg-red-400', border: 'border-red-500/30', code: 'S' },
}

// Sort rank for the matrix's State column — calm first, worst last.
export const STATE_ORDER = { CALM: 0, ELEVATED: 1, STRESSED: 2 }

// The same language one level down: how far a single METRIC sits from its own
// trailing norm. Grey normal · amber elevated · red extreme, at the 2σ/3σ cuts
// the backend uses to derive the zone state above — so a red number in the card
// and a red number in the row always mean the identical thing.
export const zColor = (z) =>
  z == null ? 'text-neutral-400' : Math.abs(z) >= 3 ? 'text-red-400' : Math.abs(z) >= 2 ? 'text-yellow-400' : 'text-neutral-300'

// ── What the four numbers MEAN ────────────────────────────────────────────────
// The desk rail puts two ⓘ popovers ~200 px apart — the matrix's column legend
// and the detail card's — and they define the same four things. Written twice
// they drift, and they had: the table's copy hardcoded "its own 30-day norm"
// while the card's read the live `baseline_days`, so the table's popover could
// contradict the table's OWN footer two lines below it. Same lesson as the price
// strings that lived in three files (CLAUDE.md, "Preis-Strings leben verstreut")
// and the state colours above. Definitions live here; each popover adds only the
// sentence that is true of ITS surface.
//
// `baselineDays` comes from /power/overview's own `baseline_days` — never a
// literal, so the prose cannot outlive a backend change to the window.
export const metricGlossary = (baselineDays) => {
  const norm = baselineDays ? `${baselineDays}-day` : 'trailing'
  return {
    norm,
    state: `State: how far this zone sits from its own ${norm} norm — CALM / ELEVATED (amber) / STRESSED (red); a deviation vs history, not a forecast.`,
    dayAhead: 'Day-ahead: the auction price (€/MWh), cleared the day before for this delivery day — a settled market price, NOT a forecast. It is the DAILY MEAN across the day’s hours.',
    residual: 'Residual: demand − wind − solar (GW), the gap conventional plants must fill — what actually sets the price.',
    renewables: 'Renewables: wind + solar as a share of load, left blank when the feed is too incomplete to trust the share.',
    sigma: `σ: distance from this zone’s own ${norm} norm — amber past 2σ, red past 3σ. Descriptive, never a forecast.`,
  }
}
