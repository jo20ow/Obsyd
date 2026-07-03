// "Is this high or low?" — the single biggest legibility win from the comparable
// dashboards (PortWatch / Trading Economics). A bare number teaches nothing; the
// same number placed on its own recent range does. Given a z-score (deviation vs
// the metric's ~90-day history), render a compact Low—Normal—High track with a
// marker, so magnitude reads at a glance. Descriptive, never a forecast.

// z → position across a −3σ…+3σ track (clamped).
function pos(z) {
  const p = (z + 3) / 6
  return Math.max(0, Math.min(1, p)) * 100
}

function bandLabel(z) {
  const az = Math.abs(z)
  if (az >= 3) return z > 0 ? 'unusually high' : 'unusually low'
  if (az >= 2) return z > 0 ? 'high' : 'low'
  if (az >= 1) return z > 0 ? 'a bit high' : 'a bit low'
  return 'normal'
}

function markerColor(z) {
  const az = Math.abs(z)
  if (az >= 3) return 'bg-red-400'
  if (az >= 2) return 'bg-yellow-400'
  return 'bg-green-glow'
}

export default function ReferenceBand({ z, baselineN, className = '' }) {
  if (z == null) return null
  return (
    <div className={className}>
      <div className="relative h-1.5 rounded-full overflow-hidden bg-neutral-800">
        {/* normal zone (±2σ) sits in the middle third-and-a-bit; edges read as extreme */}
        <div className="absolute inset-y-0 left-[16.6%] right-[16.6%] bg-green-glow/15" />
        <div
          className={`absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-1 h-3 rounded-sm ${markerColor(z)}`}
          style={{ left: `${pos(z)}%` }}
        />
      </div>
      <div className="flex items-center justify-between mt-0.5">
        <span className="font-mono text-[8px] text-neutral-700">low</span>
        <span className="font-mono text-[9px] text-neutral-500">
          {bandLabel(z)}{baselineN ? ` · vs ${baselineN}d` : ''}
        </span>
        <span className="font-mono text-[8px] text-neutral-700">high</span>
      </div>
    </div>
  )
}
