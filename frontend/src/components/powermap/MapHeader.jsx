import { InfoPopover } from '../Panel'
import { FILLS } from './fills'

// The map's chrome row: title + ⓘ + every toggle (view, fill, overlays).
// Split out of index.jsx, which had grown to hold the container logic AND a
// ~1800-character single-line ⓘ string that gained a sentence per fill and per
// overlay. Nothing here holds state — the container still owns it.

// Header toggle chip — the ONE source for the active/idle button styling
// (ZONES/POINTS, the fill buttons, FLOWS, LINE OUTAGES, LABELS).
function ToggleButton({ active, onClick, title, children }) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={`font-mono text-[9px] px-2 py-0.5 rounded border ${
        active ? 'text-cyan-glow border-cyan-glow/40 bg-cyan-glow/10' : 'text-neutral-500 border-border hover:text-neutral-300'
      }`}
    >
      {children}
    </button>
  )
}

// ── The overlay registry ──────────────────────────────────────────────────────
// ONE list, two consumers: it renders the toggle buttons AND supplies the ⓘ
// entries below. It used to be an object read positionally (.flows / .outages),
// which quietly reintroduced the very "central string someone must remember to
// edit" that reading `info` off FILLS had just removed — a third overlay would
// have shipped a button with no explanation. Same shape as a FILLS entry
// (key/label/info) so both lists render through the same code.
const OVERLAYS = [
  {
    key: 'flows',
    label: 'FLOWS',
    title: 'Cross-border flow arcs (latest hour)',
    info: (
      'Arcs = the latest cross-border flow per border: the faint end exports, the solid end '
      + 'imports; width ∝ GW, colour = how loaded the border is vs its offered day-ahead capacity '
      + '(grey = no NTC published or no reading — drawn thin and faint as context). They always '
      + 'show the latest hour and hide while you scrub the past — click one for the border detail '
      + 'below.'
    ),
  },
  {
    key: 'outages',
    label: 'LINE OUTAGES',
    title: 'Transmission lines out or de-rated now, or starting within 30 days (ENTSO-E A78)',
    info: (
      'A dashed chord on every border with a transmission asset out or de-rated now (tight dash) '
      + 'or starting within 30 days (sparse dash); colour = forced/unplanned vs planned '
      + 'maintenance. The chords stay put while you scrub — an outage is a window, not an hour '
      + '(glossary in HOW TO READ).'
    ),
  },
  {
    key: 'labels',
    label: 'LABELS',
    title: 'Per-zone labels (overlapping labels hide — zoom in to reveal)',
    // Only offered where a label can exist: the TextLayer is a zones-view layer,
    // and a fill without `labelText` has nothing to write. `when` keeps that
    // condition here rather than as a separate branch in the button row, so the
    // registry stays the ONE list — the point of it is that no toggle can ship
    // without its ⓘ entry, which is exactly what a hand-rolled branch allowed.
    when: ({ view, fillDef }) => view === 'zones' && Boolean(fillDef.labelText),
    info: (
      'Names each zone with its value ("DE-LU 74"; the grid-state fill writes the zone code '
      + 'alone). Labels that would collide hide, keeping the extreme prices — zoom in to reveal '
      + 'the rest.'
    ),
  },
]

// ── The ⓘ copy ────────────────────────────────────────────────────────────────
// STRUCTURED, per the PR #138 rule: a term list, not a wall of prose. It is
// composed, never hand-written — one entry per fill and per overlay, read off
// the two registries, so a new fill or overlay ships its own explanation beside
// its colours instead of growing a central string. (It was briefly a ~1,900-char
// single paragraph, which is exactly what that rule exists to prevent.)
const INTRO = (
  'Real bidding-zone geometry (SE1–SE4, NO1–NO5, Italian sub-zones), shaded by whichever fill is '
  + 'active. Flat unlabelled shapes = neighbouring countries, no data by design.'
)
const CREDITS = 'Zone geometry © Electricity Maps contributors (AGPL). Data: ENTSO-E. Descriptive, not a forecast.'

const MAP_INFO = (
  <div className="space-y-2">
    <p className="text-neutral-400 leading-snug">{INTRO}</p>
    <dl className="space-y-1.5">
      {[...FILLS, ...OVERLAYS].map(({ key, label, info }) => (
        <div key={key}>
          <dt className="text-cyan-glow/90">{label}</dt>
          <dd className="text-neutral-400 leading-snug">{info}</dd>
        </div>
      ))}
    </dl>
    <div className="pt-1 border-t border-border/40 text-neutral-500">{CREDITS}</div>
  </div>
)

export default function MapHeader({ view, setView, fill, setFill, fillDef, overlays, setOverlays }) {
  const toggleOverlay = (key) => setOverlays((o) => ({ ...o, [key]: !o[key] }))
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 border-b border-border">
      <div className="flex items-center gap-2 min-w-0">
        <span className="font-mono text-[12px] font-semibold text-neutral-300">Europe · power map</span>
        <InfoPopover text={MAP_INFO} wide />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1">
          {[['zones', 'ZONES'], ['points', 'POINTS']].map(([v, l]) => (
            <ToggleButton key={v} active={view === v} onClick={() => setView(v)}>{l}</ToggleButton>
          ))}
        </div>
        <div className="flex items-center gap-1">
          {FILLS.map((m) => (
            <ToggleButton key={m.key} active={fill === m.key} onClick={() => setFill(m.key)}>{m.label}</ToggleButton>
          ))}
        </div>
        {OVERLAYS.filter((o) => !o.when || o.when({ view, fillDef })).map((o) => (
          <ToggleButton
            key={o.key}
            active={overlays[o.key]}
            onClick={() => toggleOverlay(o.key)}
            title={o.title}
          >
            {o.label}
          </ToggleButton>
        ))}
      </div>
    </div>
  )
}
