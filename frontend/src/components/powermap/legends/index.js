// Barrel for the map's legend rows. Legend.jsx had grown to five of them in
// one file; each now lives beside its own default export and this module keeps
// the single import site (`from './legends'`) that fills.js and index.jsx use.
export { default as PriceScaleLegend } from './PriceScaleLegend'
export { default as StateLegend } from './StateLegend'
export { default as TechLegend } from './TechLegend'
export { default as FlowArcLegend } from './FlowArcLegend'
export { default as OutageLegend } from './OutageLegend'
