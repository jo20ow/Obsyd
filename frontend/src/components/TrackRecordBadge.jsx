import { InfoPopover } from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'

const API = '/api'

// Honest-by-design: this never shows a green "edge" unless the forward-return
// relationship is statistically significant. Sufficient data with no signal
// reads as "no measured edge", not a win.
const METHOD =
  'Track record: rank correlation (IC) between this signal and the forward ' +
  'Brent return, with Newey-West HAC significance for overlapping windows. ' +
  '"No measured edge" means enough data but not statistically significant ' +
  '(p > 0.05). Single-regime sample — informational, not a forecast.'

/**
 * Compact, honest track-record strip for a signal panel.
 * Reads /api/validation/scorecards (shared SWR cache, fetched once) and renders
 * the card for `signal` at `horizon`. Renders nothing until scorecards exist.
 */
export default function TrackRecordBadge({ signal, horizon = 7 }) {
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
    label = `Track record: building — n=${card.n}/${minN}`
    tone = 'text-neutral-600 border-neutral-700/50'
  } else {
    const significant = card.p_value != null && card.p_value <= 0.05
    if (significant && card.ic != null) {
      const sign = card.ic >= 0 ? '+' : ''
      label = `Track record: IC ${sign}${card.ic.toFixed(2)} · ${horizon}d · n=${card.n} · p=${card.p_value.toFixed(2)}`
      tone = card.ic >= 0 ? 'text-green-glow border-green-glow/30' : 'text-orange-400 border-orange-400/30'
    } else {
      label = `No measured edge · ${horizon}d · n=${card.n}`
      tone = 'text-neutral-500 border-neutral-700/50'
    }
  }

  return (
    <div className="px-4 py-1.5 border-t border-border/30 flex items-center gap-1.5">
      <span className={`font-mono text-[9px] px-1.5 py-0.5 rounded border ${tone}`}>{label}</span>
      <InfoPopover text={METHOD} />
    </div>
  )
}
