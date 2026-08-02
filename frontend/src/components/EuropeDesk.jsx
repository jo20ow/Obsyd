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

// The EUROPE tab — the desk's front door, lifted out of App.jsx so the layout
// that makes it a *map-first desk* lives in one file instead of inline in the
// 900-line tab switch.
//
// THE SPLIT: the map is the subject, not a sidecar. It takes the RIGHT column
// (2fr, tall, and sticky on lg so it stays in view while you read past it); the
// all-zones matrix rides a narrow LEFT rail (compact variant) and is the map's
// index. Below lg the split collapses and the MAP COMES FIRST (order-1) —
// on a phone the map is still the thing you came for, and nothing sticks.
// Everything below the grid (radar, borders, hydro, charts, orientation) stays
// full width: they are the drill-down, read in sequence, not beside the map.
const PowerMap = lazy(() => import('./powermap'))

const MAP_FALLBACK = (
  <div className="border border-border bg-surface rounded px-4 py-8 text-center font-mono text-xs text-neutral-500">
    Loading map…
  </div>
)

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
      <h2 className="font-mono text-[15px] font-semibold text-neutral-200">European power desk · all zones</h2>

      <div className="grid grid-cols-1 lg:grid-cols-[minmax(340px,1fr)_2fr] gap-3 items-start">
        <div className="order-2 lg:order-1 min-w-0 space-y-3">
          <ErrorBoundary name="power-overview">
            <PowerOverviewMatrix compact selectedZone={focusZone} onSelect={setFocusZone} />
          </ErrorBoundary>
          {/* Temporary until PR 9's zone detail card: a row click no longer
              jumps tabs, so the jump needs a door of its own — otherwise the
              only way from "that zone looks odd" to its desk is the pills. */}
          <button
            onClick={() => { setEnergyZone(focusZone); goToTab('energy') }}
            className="w-full border border-border bg-surface rounded px-3 py-2 font-mono text-[11px] text-neutral-400 hover:text-cyan-glow hover:border-cyan-glow/40 text-left"
          >
            Open zone <span className="text-neutral-200">{focusLabel}</span> <span className="text-cyan-glow">→</span>
          </button>
        </div>
        {/* Sticky against the WINDOW: <main> has no inner scroll container, and
            body's overflow-x:hidden propagates to the viewport (so body itself
            stays `visible` and clips nothing) — verified, nothing here clips.
            HONEST CAVEAT: this pins NOTHING today. A sticky grid item travels
            inside its grid AREA, and the map is the TALLER column (976 px vs
            the rail's 549 at 1500×1000), so the row is exactly the map's own
            height and there is no room to move. Measured, not assumed. It is
            left in because it is free and self-activating: the day the rail
            outgrows the map, the map pins for the difference. Making it pin for
            real would mean putting the panels below INTO the left column (a
            true two-pane desk) — a different layout, not this one. */}
        <div className="order-1 lg:order-2 min-w-0 lg:sticky lg:top-3">
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
