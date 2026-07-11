/**
 * Compact data-age chip for panel headers. Reads the `as_of`/`age_days`/`stale`
 * triple every power detail endpoint now returns (thresholds mirror
 * backend/collectors/freshness.py::SPECS via the route layer).
 *
 * A hung feed used to look identical to a healthy one — the panels only said
 * "latest {date}". Fresh data renders as a quiet date stamp; a lagging series
 * gets an amber STALE tag with its age. Dates are delivery dates in UTC.
 */
export default function FreshnessCaption({ meta }) {
  if (!meta?.as_of) return null

  if (meta.stale) {
    return (
      <span
        className="font-mono text-[9px] tracking-wide text-orange-400 border border-orange-500/30 rounded px-1.5 py-0.5"
        title={`Latest data ${meta.as_of} (UTC) — ${meta.age_days}d old, this feed may be stalled`}
      >
        STALE · {meta.age_days}d
      </span>
    )
  }

  return (
    <span
      className="font-mono text-[9px] text-neutral-600 hidden sm:inline"
      title="Delivery date of the newest data point (UTC)"
    >
      {meta.as_of}
    </span>
  )
}
