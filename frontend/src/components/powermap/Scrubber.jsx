import { fmtTs } from './constants'

// Time scrubber — slide the map through the last 7 days of day-ahead prices.
// Only rendered for fills with scrub:true (index.jsx owns that condition).
export default function Scrubber({ ts, effIdx, setIdx }) {
  return (
    <div className="flex items-center gap-2 px-4 py-2 border-t border-border">
      <span className="font-mono text-[9px] text-neutral-500 shrink-0 w-32">
        {fmtTs(ts[effIdx])}{effIdx === ts.length - 1 ? ' · LATEST' : ' UTC'}
      </span>
      <input
        type="range"
        min={0}
        max={ts.length - 1}
        value={effIdx}
        onChange={(e) => setIdx(Number(e.target.value))}
        className="flex-1 accent-cyan-500"
        aria-label="Time scrubber"
      />
      {effIdx !== ts.length - 1 && (
        <button onClick={() => setIdx(null)} className="font-mono text-[9px] text-neutral-500 hover:text-cyan-glow shrink-0">↺ live</button>
      )}
    </div>
  )
}
