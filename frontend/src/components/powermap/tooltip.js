// getTooltip builder for the three pickable shapes: border arc ({html}), scatter
// point (text), zone polygon (text). Returns the function DeckGL calls.
//
// The ACTIVE fill may append its own lines to a zone's tooltip through the
// registry's optional `tooltipLines(zone, ctx)` (fills.js) — e.g. the
// price-setting technology and its tension flag. Dispatched, never branched on
// a fill key, and `fillCtx` is the very ctx that fill colors with, so tooltip
// and colour can never tell different stories.
export function makeTooltip(byZone, pal, fillDef, fillCtx) {
  const TIP_STYLE = { ...pal.tooltip, fontFamily: 'monospace', fontSize: '11px', padding: '6px 8px' }
  const fillLines = (zone) => {
    const lines = fillDef?.tooltipLines?.(zone, fillCtx) || []
    return lines.length ? `\n${lines.join('\n')}` : ''
  }
  return ({ object }) => {
    if (!object) return null
    if (object.zone_a && object.zone_b) { // a border arc — richer, so {html}
      const label = object.label || `${object.zone_a}↔${object.zone_b}`
      const [la, lb] = label.split('↔')
      const mw = object.latest_flow_mw
      const dir = mw == null || mw === 0
        ? 'no current flow reading'
        : `${mw > 0 ? la : lb} → ${mw > 0 ? lb : la} · ${(Math.abs(mw) / 1000).toFixed(1)} GW`
      const util = object.capacity_source === 'ntc'
        ? (object.util_latest_pct != null
          ? `util ${object.util_latest_pct.toFixed(0)}% of NTC (offered capacity — can exceed 100%)`
          : null)
        : (object.at_rail_pct != null
          ? `at rail ${object.at_rail_pct.toFixed(0)}% (no NTC published — own p95)`
          : null)
      const now = object.latest_spread == null
        ? null
        : object.expensive_side == null
          ? 'now: coupled'
          : `now: €${Math.abs(object.latest_spread).toFixed(0)} · ${object.expensive_side === object.zone_a ? la : lb} dearer`
      const coupled = object.convergence_pct != null
        ? `coupled ${object.convergence_pct.toFixed(0)}% of hrs` : null
      // Only our own API values are interpolated — no user-controlled strings.
      const html = [
        `<div style="font-weight:600">${label}</div>`,
        `<div>${dir}</div>`,
        util && `<div>${util}</div>`,
        now && `<div>${now}</div>`,
        coupled && `<div>${coupled}</div>`,
        '<div style="opacity:.55">click → border detail</div>',
      ].filter(Boolean).join('')
      return { html, style: TIP_STYLE }
    }
    // A point IS a zone (POINTS view) — it carries the same fill, so it earns
    // the same fill lines.
    if (object.position && object.zone) { // a scatter point
      const price = object.price != null ? `${object.price.toFixed(1)} €/MWh` : 'n/a'
      return {
        text: `${object.label} · ${object.state || ''}\nDay-ahead: ${price}${fillLines(object.zone)}`,
        style: TIP_STYLE,
      }
    }
    const zone = object.properties?.zone
    if (!zone) return null // neighbouring country — context only
    const z = byZone.get(zone)
    // No overview row — but the active fill may still know something about
    // this zone (its feed is a different endpoint with its own coverage).
    if (!z) return { text: `${zone}\nno data yet${fillLines(zone)}`, style: TIP_STYLE }
    const price = z.price_close != null ? `${z.price_close.toFixed(1)} €/MWh` : 'n/a'
    return {
      text: `${z.zone_label || zone} · ${z.state || ''}\nDay-ahead: ${price}\nResidual z: ${z.residual_z != null ? z.residual_z.toFixed(1) : 'n/a'}${fillLines(zone)}`,
      style: TIP_STYLE,
    }
  }
}
