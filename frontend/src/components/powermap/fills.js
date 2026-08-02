// Fill registry — one entry per choropleth fill mode. The header buttons render
// from this list and index.jsx branches on the `fill` key; `scrub` says whether
// the time scrubber applies (grid state is always live). Structural scaffolding
// only for now: the color functions themselves stay in index.jsx's zoneFill/
// pointFill closures — later PRs move them in here.
export const FILLS = [
  { key: 'price', label: 'DAY-AHEAD €/MWh', scrub: true },
  { key: 'state', label: 'GRID STATE', scrub: false },
]
