// Barrel for the map's legend rows. Legend.jsx had grown to five of them in
// one file; each now lives beside its own default export and this module keeps
// the single import site (`from './legends'`) that fills.js and index.jsx use.
//
// TWO INCOMPATIBLE SHAPES live here and a new legend has to pick one — the
// difference is invisible until you look at the rendered page:
//   • FILL legends (PriceScaleLegend, StateLegend, TechLegend) return a BARE
//     <span>. The FILLS registry renders them INSIDE the map's footer row
//     (`<fillDef.Legend />` in index.jsx), which already owns the top border,
//     the padding and the type — a <div> here would break that row apart.
//   • OVERLAY legends (FlowArcLegend, OutageLegend) return a full-width row of
//     their OWN, stacked above that footer, because an overlay toggles
//     independently of the active fill. They get that chrome by rendering
//     <LegendRow> (./LegendRow.jsx) — which is the tell: a legend that wraps
//     itself in LegendRow is an overlay legend, one that must not is a fill
//     legend. LegendRow stays out of this barrel deliberately; it is chrome for
//     the legends, not a legend the map renders.
export { default as PriceScaleLegend } from './PriceScaleLegend'
export { default as StateLegend } from './StateLegend'
export { default as TechLegend } from './TechLegend'
export { default as FlowArcLegend } from './FlowArcLegend'
export { default as OutageLegend } from './OutageLegend'
