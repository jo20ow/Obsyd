import { useState, useCallback, lazy, Suspense } from 'react'
import useZones from '../hooks/useZones'
import ErrorBoundary from './ErrorBoundary'
import NarrativeHero from './NarrativeHero'
import PowerOverviewMatrix from './PowerOverviewMatrix'
import ZoneDetailCard from './ZoneDetailCard'
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
// equal columns mean a sticky one could never travel — and why the rail's table
// takes what is left rather than picking a vh of its own. Under it the rail now
// answers the click it collects: the border chip's reserved slot (28 px, fixed
// — the chip appearing must never cost the table a row), then the zone detail
// card (342 px) — both `shrink-0`, so they come out of the SAME budget and the
// table simply yields the rows they cost. Measured across all 37 zones ×
// {1500×1000, 1280×800, 1024×768}: card 342 px for every zone, column overflow
// 0 px everywhere, 13 rows fully visible at 1500×1000 and 6 at both smaller
// sizes. The one thing that moves it is the flag row: the synthetic worst case
// (3 flags + a 2-line headline, more than any zone carries today) grows the
// card to 363/385 px and costs the table ONE row — still no overflow, because
// the table is the flex child that yields. Nothing else about a zone may.
// Below lg the split collapses, NOTHING is force-fitted to the viewport, and the
// MAP COMES FIRST (order-1) — on a phone it is still the thing you came for.
// Everything below the grid (radar, borders, hydro, charts, orientation) stays
// full width: they are the drill-down, read in sequence, not beside the map.
const PowerMap = lazy(() => import('./powermap'))

export default function EuropeDesk({ energyZone, setEnergyZone, goToTab }) {
  // Map arc / outage chord click → border detail: the focus travels as a prop to
  // BordersPanel, which opens the row and un-collapses the panel. `ts` makes
  // every click a NEW signal, so re-clicking the same border still expands.
  // Stable callback — PowerMap's layers memo depends on it. Lives here, not in
  // App: the EUROPE tab is the only place a border can be clicked.
  //
  // `scroll: false` is the whole point: the panel is two screens down, so
  // auto-scrolling there yanked the MAP out from under the hand that clicked
  // the arc — a click on the subject of the page should not navigate away from
  // it. The row is prepared anyway; the chip below offers the trip.
  const [borderFocus, setBorderFocus] = useState(null)
  const onBorderSelect = useCallback(
    (a, b) => setBorderFocus({ a, b, ts: Date.now(), scroll: false }), [])
  // Which focus the user has already dealt with (clicked through or dismissed).
  const [chipDoneTs, setChipDoneTs] = useState(null)
  const showChip = Boolean(borderFocus && borderFocus.ts !== chipDoneTs)

  // The zone the SPLIT is looking at — table row ⇄ map outline, both ways.
  // Deliberately NOT the global `energyZone`: selecting here is "show me this
  // zone on the map", a look, not a navigation. It FOLLOWS the global zone
  // (so it is never empty on arrival, and picks up the async server default),
  // but a look here never writes back. Jumping the whole desk is the explicit
  // "Open zone →" button in the detail card's header, and nothing else.
  // Render-phase sync, the repo's derive-from-props pattern (cf. Panel's
  // expandSignal) — no effect, so the rail never paints the wrong zone first.
  const [focusZone, setFocusZone] = useState(energyZone)
  const [seenZone, setSeenZone] = useState(energyZone)
  if (energyZone !== seenZone) {
    setSeenZone(energyZone)
    setFocusZone(energyZone)
  }

  const { zones } = useZones()
  const zoneLabel = (key) => zones.find((z) => z.key === key)?.label || key

  return (
    <div className="space-y-3">
      <ErrorBoundary name="narrative">
        <NarrativeHero />
      </ErrorBoundary>
      <div className="grid grid-cols-1 lg:grid-cols-[minmax(340px,1fr)_2fr] gap-3 items-start">
        {/* The rail is a flex COLUMN of the same height as the map card: the
            table takes the space (flex-1 min-h-0 — see PowerOverviewMatrix's
            `compact`), the detail card is parked at the bottom on a bounded,
            `shrink-0` budget of its own. `gap-3` rather than space-y so the
            card keeps its spacing as a flex item. */}
        <div className={`order-2 lg:order-1 min-w-0 flex flex-col gap-3 ${DESK_COLUMN_H}`}>
          <ErrorBoundary name="power-overview">
            <PowerOverviewMatrix compact selectedZone={focusZone} onSelect={setFocusZone} />
          </ErrorBoundary>
          {/* Border-click feedback, on a slot that is ALWAYS this tall: the chip
              appearing must not cost the table a row, and the empty state is
              not dead space — it is where the flow arcs say they are clickable.
              In the RAIL, not under the map: the desk column runs past the fold
              by design (see DESK_COLUMN_H), so anything anchored to the map's
              bottom edge would land ~250 px below the viewport — measured — and
              the one thing a click MUST produce is feedback you can see. */}
          <div className="shrink-0 h-7 flex items-center">
            {showChip ? (
              <div className="inline-flex items-center gap-1 max-w-full border border-cyan-glow/40 bg-surface rounded pl-2 pr-1 py-1">
                <button
                  onClick={() => {
                    document.getElementById('panel-power-borders')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                    setChipDoneTs(borderFocus.ts)
                  }}
                  className="font-mono text-[9px] text-neutral-300 hover:text-cyan-glow transition-colors truncate"
                >
                  {zoneLabel(borderFocus.a)}↔{zoneLabel(borderFocus.b)} opened in Borders <span className="text-cyan-glow">· view ↓</span>
                </button>
                <button
                  onClick={() => setChipDoneTs(borderFocus.ts)}
                  title="Dismiss" aria-label="Dismiss"
                  className="shrink-0 px-1 font-mono text-[10px] text-neutral-600 hover:text-neutral-300"
                >
                  ×
                </button>
              </div>
            ) : (
              <span className="font-mono text-[9px] text-neutral-700 truncate">
                Click a flow arc or outage line on the map to open its border.
              </span>
            )}
          </div>
          {/* The answer to a row/map click — and the only door out of the tab
              ("Open zone →"), since a click here is a look, not a navigation. */}
          <ErrorBoundary name="zone-detail">
            <ZoneDetailCard
              zone={focusZone}
              onOpenZone={() => { setEnergyZone(focusZone); goToTab('energy') }}
            />
          </ErrorBoundary>
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
