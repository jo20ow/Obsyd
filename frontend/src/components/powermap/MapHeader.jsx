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

// ── The ⓘ copy ────────────────────────────────────────────────────────────────
// COMPOSED, not hand-written as one blob: what the map is, then ONE sentence
// per fill (read straight off the FILLS registry, so a new fill ships its own
// sentence with its colours instead of someone remembering to edit a string
// here), then one per overlay, then the credits. Kept out of the JSX so the
// header row stays readable.
const INTRO = (
  'Real bidding-zone geometry (SE1–SE4, NO1–NO5, Italian sub-zones), shaded by whichever fill is '
  + 'active — the day-ahead price, the grid state, or the technology setting the price. '
  + 'Flat unlabelled shapes = neighbouring countries, no data by design.'
)
// Overlays are owned by this header's toggles (not by the FILLS registry), so
// their sentences live here, one per toggle.
const OVERLAY_INFO = {
  flows: (
    'FLOWS arcs = the latest cross-border flow per border: the faint end exports, the solid end '
    + 'imports; width ∝ GW, colour = how loaded the border is vs its offered day-ahead capacity '
    + '(grey = no NTC published or no reading — drawn thin and faint as context); they always show '
    + 'the latest hour and hide while you scrub the past — click one for the border detail below.'
  ),
  outages: (
    'LINE OUTAGES draws a dashed chord on every border with a transmission asset out or de-rated '
    + 'now (tight dash) or starting within 30 days (sparse dash); colour = forced/unplanned vs '
    + 'planned maintenance, and the chords stay put while you scrub — an outage is a window, not an '
    + 'hour (glossary in HOW TO READ).'
  ),
}
const CREDITS = 'Zone geometry © Electricity Maps contributors (AGPL). Data: ENTSO-E. Descriptive, not a forecast.'

const MAP_INFO = [
  INTRO,
  ...FILLS.map((f) => f.info).filter(Boolean),
  OVERLAY_INFO.flows,
  OVERLAY_INFO.outages,
  CREDITS,
].join(' ')

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
        <ToggleButton
          active={overlays.flows}
          onClick={() => toggleOverlay('flows')}
          title="Cross-border flow arcs (latest hour)"
        >
          FLOWS
        </ToggleButton>
        <ToggleButton
          active={overlays.outages}
          onClick={() => toggleOverlay('outages')}
          title="Transmission lines out or de-rated now, or starting within 30 days (ENTSO-E A78)"
        >
          LINE OUTAGES
        </ToggleButton>
        {view === 'zones' && fillDef.labelText && (
          <ToggleButton
            active={overlays.labels}
            onClick={() => toggleOverlay('labels')}
            title="Per-zone labels (overlapping labels hide — zoom in to reveal)"
          >
            LABELS
          </ToggleButton>
        )}
      </div>
    </div>
  )
}
