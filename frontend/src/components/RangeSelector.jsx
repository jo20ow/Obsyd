/**
 * RangeSelector — the global date-range control, sibling of ZoneSelector in the
 * shell. One canonical vocabulary (7D..5Y) drives every range-aware panel via
 * ViewStateContext, so the user sets the window ONCE and the whole desk follows —
 * the fix for "same week across price / residual / gen-mix" needing three clicks.
 */
import { useViewState } from '../context/ViewStateContext'
import { RANGES } from '../utils/ranges'

export default function RangeSelector() {
  const { range, setRange } = useViewState()
  return (
    <div className="flex items-center gap-1" role="group" aria-label="Date range">
      {RANGES.map((r) => (
        <button
          key={r.key}
          onClick={() => setRange(r.key)}
          className={`font-mono text-[9px] px-2 py-0.5 rounded border ${
            range === r.key
              ? 'text-cyan-glow border-cyan-glow/40 bg-cyan-glow/10'
              : 'text-neutral-500 border-border hover:text-neutral-300'
          }`}
        >
          {r.label}
        </button>
      ))}
    </div>
  )
}
