import useFetchWithError from '../hooks/useFetchWithError'
import { POLL_FAST_MS } from '../utils/poll'
import Provenance from './Provenance'

const API = '/api'

// "Europe right now" — an auto-composed, plain-language read of the whole continent's
// power state from /api/power/overview. Template-based (NO LLM), deterministic. This is
// Obsyd's core differentiator vs gridstatus: it *reads* the grid, charts are evidence.
const eur = (v) => (v == null ? '—' : `€${Math.round(v)}`)
const sig = (z) => `${z >= 0 ? '+' : ''}${z.toFixed(1)}σ`
const list = (zs, n = 3) => zs.slice(0, n).map((z) => z.zone_label || z.zone).join(', ')

export default function NarrativeHero() {
  const { data } = useFetchWithError(`${API}/power/overview`, { pollMs: POLL_FAST_MS })
  const zones = data?.zones || []
  if (!data?.available || zones.length === 0) return null

  const withPrice = zones.filter((z) => z.price_close != null)
  const stressed = zones.filter((z) => z.state === 'STRESSED')
  const elevated = zones.filter((z) => z.state === 'ELEVATED')
  const negatives = withPrice.filter((z) => z.price_close < 0)
  const dunkel = zones.filter((z) => z.dunkelflaute)

  const byPrice = [...withPrice].sort((a, b) => a.price_close - b.price_close)
  const cheap = byPrice[0]
  const dear = byPrice[byPrice.length - 1]
  const spread = cheap && dear ? dear.price_close - cheap.price_close : null

  const movers = withPrice
    .filter((z) => z.price_z != null && Math.abs(z.price_z) >= 1.5)
    .sort((a, b) => Math.abs(b.price_z) - Math.abs(a.price_z))
    .slice(0, 2)

  const lead = stressed.length
    ? `European power is stressed in ${stressed.length} zone${stressed.length > 1 ? 's' : ''} right now`
    : elevated.length
      ? `European power is mostly calm, with ${elevated.length} zone${elevated.length > 1 ? 's' : ''} running elevated`
      : 'European power is calm across the board right now'

  const moverText = movers.length
    ? movers.map((z) => `${z.zone_label || z.zone} at ${eur(z.price_close)}/MWh (${sig(z.price_z)} vs its norm${z.residual_z != null && z.residual_z >= 1 ? ', on high residual load' : ''})`).join('; ')
    : ''

  const spreadText = spread != null && cheap.zone !== dear.zone
    ? `Cheapest ${cheap.zone_label || cheap.zone} ${eur(cheap.price_close)} · priciest ${dear.zone_label || dear.zone} ${eur(dear.price_close)} — a ${eur(spread)}/MWh spread across the continent.`
    : ''

  const negText = negatives.length
    ? `Renewable oversupply is pushing ${list(negatives)} below €0.`
    : ''

  const dunkelText = dunkel.length
    ? `Dunkelflaute flagged in ${list(dunkel)} — thermal plants carrying the grid.`
    : ''

  return (
    <div className="border border-border bg-surface rounded shadow-sm p-4">
      <div className="flex items-center gap-2 mb-2">
        <span className="w-1 h-4 rounded-full bg-cyan-glow" />
        <h2 className="font-mono text-[13px] font-semibold text-neutral-300">Europe right now</h2>
      </div>
      <p className="font-mono text-[14px] leading-relaxed text-neutral-400">
        <span className="text-neutral-200 font-medium">{lead}{moverText ? ' — ' : '.'}</span>
        {moverText && <>{moverText}. </>}
        {spreadText && <>{spreadText} </>}
        {negText && <>{negText} </>}
        {dunkelText && <>{dunkelText} </>}
      </p>
      <Provenance source="ENTSO-E day-ahead + load / generation" className="mt-2" />
    </div>
  )
}
