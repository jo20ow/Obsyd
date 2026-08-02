// The Suspense fallback for every lazily-loaded map (PowerMap, VesselMap,
// AtlasMap). An ELEMENT, not a component: it is only ever passed as
// `<Suspense fallback={MAP_FALLBACK}>`, and it holds no state or props.
// Shared because it was drifting between App.jsx and EuropeDesk.jsx.
export const MAP_FALLBACK = (
  <div className="border border-border bg-surface rounded px-4 py-8 text-center font-mono text-xs text-neutral-500">
    Loading map…
  </div>
)
