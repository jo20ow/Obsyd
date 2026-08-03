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
