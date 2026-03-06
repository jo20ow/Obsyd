export function SkeletonCard({ lines = 3 }) {
  return (
    <div className="border border-border bg-surface rounded px-4 py-3">
      <div className="animate-pulse space-y-3">
        {Array.from({ length: lines }).map((_, i) => (
          <div
            key={i}
            className="h-4 bg-neutral-800 rounded"
            style={{ width: `${85 - i * 15}%` }}
          />
        ))}
      </div>
    </div>
  )
}

export function SkeletonChart() {
  return (
    <div className="border border-border bg-surface rounded px-4 py-3">
      <div className="animate-pulse">
        <div className="h-3 bg-neutral-800 rounded w-1/3 mb-3" />
        <div className="h-48 bg-neutral-800/50 rounded" />
      </div>
    </div>
  )
}
