/**
 * Compact data-age chip for panel headers. Reads the `as_of`/`age_days`/`stale`
 * triple every power detail endpoint now returns (thresholds mirror
 * backend/collectors/freshness.py::SPECS via the route layer).
 *
 * A hung feed used to look identical to a healthy one — the panels only said
 * "latest {date}". Fresh data renders as a quiet date stamp; a lagging series
 * gets an amber STALE tag with its age. Dates are delivery dates in UTC.
 *
 * `dense` is the desk rail's variant: the same two states one type-step down,
 * and the date stays visible instead of hiding below sm — the rail is narrow at
 * EVERY width, so `hidden sm:inline` would drop the stamp on the exact layout
 * that needs it most. It exists so the rail does not grow a third private copy
 * of this chip: ZoneDetailCard shows the as_of triple twice (the overview row's
 * in its header, the situation feed's beside the headline it belongs to), and
 * two feeds that can disagree about freshness must not disagree about how
 * freshness LOOKS.
 *
 * `age_days` is optional throughout: /power/overview rows carry `stale` and
 * `as_of` but no age, and "STALE · nulld" is worse than an unqualified STALE.
 */
// The ONE stale badge — same vocabulary wherever a lagging feed is flagged
// (panel headers, the situation hero's per-metric tags, the overview matrix).
export function StaleChip({ asOf, ageDays, dense = false, className = '' }) {
  return (
    <span
      className={`shrink-0 font-mono tracking-wide text-orange-400 border border-orange-500/30 rounded ${
        dense ? 'text-[8px] px-1 py-px' : 'text-[9px] px-1.5 py-0.5'
      } ${className}`}
      title={`Latest data ${asOf ?? 'unknown'} (UTC)${ageDays != null ? ` — ${ageDays}d old` : ''}, this feed may be stalled`}
    >
      STALE{ageDays != null ? ` · ${ageDays}d` : ''}
    </span>
  )
}

export default function FreshnessCaption({ meta, dense = false }) {
  if (!meta?.as_of) return null

  if (meta.stale) {
    return <StaleChip asOf={meta.as_of} ageDays={meta.age_days} dense={dense} />
  }

  return (
    <span
      className={`shrink-0 font-mono ${dense ? 'num text-[8px] text-neutral-700' : 'text-[9px] text-neutral-600 hidden sm:inline'}`}
      title="Delivery date of the newest data point (UTC)"
    >
      {meta.as_of}
    </span>
  )
}
