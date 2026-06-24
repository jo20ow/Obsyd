import { InfoPopover } from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'

const API = '/api'

// Descriptive-by-design: OBSYD is an anomaly radar, not a predictor. This badge
// reports the HISTORICAL co-movement between a signal and a market series as
// transparency/context — never as a forecast or trade edge. A significant
// relationship is shown for honesty (including negative/inverse ones), not as a
// recommendation. `targetLabel` names the reference series (Brent for
// oil/maritime, TTF for the gas residual).
const method = (targetLabel) =>
  `Historical co-movement: rank correlation between this signal and the ` +
  `subsequent ${targetLabel} move, with Newey-West HAC significance for overlapping ` +
  'windows. "No historical association" means enough data but no statistically ' +
  'significant relationship (p > 0.05). This is descriptive context from a single ' +
  'historical regime — NOT a forecast, signal, or recommendation.'

/**
 * Compact, honest track-record strip for a signal panel.
 * Reads /api/validation/scorecards (shared SWR cache, fetched once) and renders
 * the card for `signal` at `horizon`. Renders nothing until scorecards exist.
 */
export default function TrackRecordBadge({ signal, horizon = 7, targetLabel = 'Brent' }) {
  const { data } = useFetchWithError(`${API}/validation/scorecards`)
  if (!data?.available) return null

  const cards = data.signals?.[signal]
  if (!cards) return null
  const card = cards.find((c) => c.horizon_days === horizon)
  if (!card) return null

  const minN = data.min_confident_n ?? 30
  let label
  let tone

  if (!card.confident) {
    label = `Historical context: building — n=${card.n}/${minN}`
    tone = 'text-neutral-600 border-neutral-700/50'
  } else {
    const significant = card.p_value != null && card.p_value <= 0.05
    if (significant && card.ic != null) {
      const sign = card.ic >= 0 ? '+' : ''
      label = `Historical co-movement ${sign}${card.ic.toFixed(2)} · ${horizon}d · n=${card.n} · context, not a forecast`
      tone = card.ic >= 0 ? 'text-green-glow border-green-glow/30' : 'text-orange-400 border-orange-400/30'
    } else {
      label = `No historical association · ${horizon}d · n=${card.n}`
      tone = 'text-neutral-500 border-neutral-700/50'
    }
  }

  return (
    <div className="px-4 py-1.5 border-t border-border/30 flex items-center gap-1.5">
      <span className={`font-mono text-[9px] px-1.5 py-0.5 rounded border ${tone}`}>{label}</span>
      <InfoPopover text={method(targetLabel)} />
    </div>
  )
}
