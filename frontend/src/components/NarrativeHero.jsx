import useFetchWithError from '../hooks/useFetchWithError'
import { POLL_FAST_MS } from '../utils/poll'
import { composeEuropeNarrative } from '../utils/narrative'
import Provenance from './Provenance'

const API = '/api'

// "Europe right now" — an auto-composed, plain-language read of the whole continent's
// power state from /api/power/overview. Sentence composition lives in
// utils/narrative.js (shared with the landing page's live figure).
export default function NarrativeHero() {
  const { data, error } = useFetchWithError(`${API}/power/overview`, { pollMs: POLL_FAST_MS })
  // Ephemeral strip: empty is the documented normal state and stays silent —
  // but a FETCH error must not masquerade as "nothing to report".
  if (error)
    return (
      <div className="font-mono text-[9px] text-red-400 px-1 py-0.5">europe right now // fetch error</div>
    )
  const parts = data?.available ? composeEuropeNarrative(data?.zones) : null
  if (!parts) return null
  const { lead, moverText, spreadText, negText, dunkelText } = parts

  return (
    <div className="border border-border bg-surface rounded shadow-sm p-4">
      <div className="flex items-center gap-2 mb-2">
        <span className="w-1 h-4 rounded-full bg-cyan-glow" />
        <h2 className="font-mono text-[13px] font-semibold text-neutral-300">Europe right now</h2>
      </div>
      <p className="font-serif text-[15px] leading-relaxed text-neutral-400">
        <span className="text-neutral-200 font-medium">{lead}{moverText ? ' — ' : '. '}</span>
        {moverText && <>{moverText}. </>}
        {spreadText && <>{spreadText} </>}
        {negText && <>{negText} </>}
        {dunkelText && <>{dunkelText} </>}
      </p>
      <Provenance source="ENTSO-E day-ahead + load / generation" className="mt-2" />
    </div>
  )
}
