import { useState, useCallback, lazy, Suspense } from 'react'
import useZones from '../hooks/useZones'
import ErrorBoundary from './ErrorBoundary'
import NarrativeHero from './NarrativeHero'
import PowerOverviewMatrix from './PowerOverviewMatrix'
import InsightsStrip from './InsightsStrip'
import BordersPanel from './BordersPanel'
import HydroReservoirPanel from './HydroReservoirPanel'
import LiveCharts from './LiveCharts'
import HowToRead from './HowToRead'
import { MAP_FALLBACK } from './MapFallback'
import { DESK_COLUMN_H } from './powermap/constants'

// The EUROPE tab — the desk's front door, lifted out of App.jsx so the layout
// that makes it a *map-first desk* lives in one file instead of inline in the
// 900-line tab switch.
//
// THE SPLIT: the map is the subject, not a sidecar. It takes the RIGHT column
// (2fr); the all-zones matrix rides a narrow LEFT rail (compact variant) and is
// the map's index. BOTH columns are exactly DESK_COLUMN_H tall and are flex
// columns, so the row has ONE height and neither column invents its own: the
// map's canvas and the rail's table are both `flex-1 min-h-0` and simply take
// what is left after their own chrome. That is why there is no sticky here —
// equal columns mean a sticky one could never travel — and why the rail shows
// ~30 of 37 zones instead of inner-scrolling at 14 beside 400 px of dead space.
// PR 9's detail card inherits the same budget instead of picking another vh.
// Below lg the split collapses, NOTHING is force-fitted to the viewport, and the
// MAP COMES FIRST (order-1) — on a phone it is still the thing you came for.
// Everything below the grid (radar, borders, hydro, charts, orientation) stays
// full width: they are the drill-down, read in sequence, not beside the map.
const PowerMap = lazy(() => import('./powermap'))

export default function EuropeDesk({ energyZone, setEnergyZone, goToTab }) {
  // Map arc / outage chord click → border detail: the focus travels as a prop to
  // BordersPanel (opens the row, expands + scrolls the panel). `ts` makes every
  // click a NEW signal, so re-clicking the same border still scrolls/expands.
  // Stable callback — PowerMap's layers memo depends on it. Lives here, not in
  // App: the EUROPE tab is the only place a border can be clicked.
  const [borderFocus, setBorderFocus] = useState(null)
  const onBorderSelect = useCallback((a, b) => setBorderFocus({ a, b, ts: Date.now() }), [])

  // The zone the SPLIT is looking at — table row ⇄ map outline, both ways.
  // Deliberately NOT the global `energyZone`: selecting here is "show me this
  // zone on the map", a look, not a navigation. It FOLLOWS the global zone
  // (so it is never empty on arrival, and picks up the async server default),
  // but a look here never writes back. Jumping the whole desk is the explicit
  // "Open zone →" button below (PR 9 grows it into a detail card).
  // Render-phase sync, the repo's derive-from-props pattern (cf. Panel's
  // expandSignal) — no effect, so the rail never paints the wrong zone first.
  const [focusZone, setFocusZone] = useState(energyZone)
  const [seenZone, setSeenZone] = useState(energyZone)
  if (energyZone !== seenZone) {
    setSeenZone(energyZone)
    setFocusZone(energyZone)
  }

  const { zones } = useZones()
  const focusLabel = zones.find((z) => z.key === focusZone)?.label || focusZone

  return (
    <div className="space-y-3">
      <ErrorBoundary name="narrative">
        <NarrativeHero />
      </ErrorBoundary>
      <div className="grid grid-cols-1 lg:grid-cols-[minmax(340px,1fr)_2fr] gap-3 items-start">
        {/* The rail is a flex COLUMN of the same height as the map card: the
            table takes the space (flex-1 min-h-0 — see PowerOverviewMatrix's
            `compact`), the button stays parked at the bottom. `gap-3` rather
            than space-y so the button keeps its spacing as a flex item. */}
        <div className={`order-2 lg:order-1 min-w-0 flex flex-col gap-3 ${DESK_COLUMN_H}`}>
          <ErrorBoundary name="power-overview">
            <PowerOverviewMatrix compact selectedZone={focusZone} onSelect={setFocusZone} />
          </ErrorBoundary>
          {/* Temporary until PR 9's zone detail card: a row click no longer
              jumps tabs, so the jump needs a door of its own — otherwise the
              only way from "that zone looks odd" to its desk is the pills.
              shrink-0: the button is chrome, the table is what yields. */}
          <button
            onClick={() => { setEnergyZone(focusZone); goToTab('energy') }}
            className="shrink-0 w-full border border-border bg-surface rounded px-3 py-2 font-mono text-[11px] text-neutral-400 hover:text-cyan-glow hover:border-cyan-glow/40 text-left"
          >
            Open zone <span className="text-neutral-200">{focusLabel}</span> <span className="text-cyan-glow">→</span>
          </button>
        </div>
        <div className="order-1 lg:order-2 min-w-0">
          <ErrorBoundary name="power-map">
            <Suspense fallback={MAP_FALLBACK}>
              <PowerMap
                tall
                onBorderSelect={onBorderSelect}
                selectedZone={focusZone}
                onZoneSelect={setFocusZone}
              />
            </Suspense>
          </ErrorBoundary>
        </div>
      </div>

      <ErrorBoundary name="insights">
        <InsightsStrip onMore={() => goToTab('alerts')} />
      </ErrorBoundary>
      {/* The border layer: prices × flows. A zone map shows WHERE power is
          expensive; only the borders show whether the market is coupled. */}
      <ErrorBoundary name="borders">
        <BordersPanel focus={borderFocus} />
      </ErrorBoundary>
      <ErrorBoundary name="hydro">
        <HydroReservoirPanel />
      </ErrorBoundary>
      <ErrorBoundary name="live-charts">
        <LiveCharts />
      </ErrorBoundary>
      <ErrorBoundary name="how-to-read">
        <HowToRead />
      </ErrorBoundary>
    </div>
  )
}
