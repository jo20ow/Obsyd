import EmbedFrame from './EmbedFrame'
import EmbedUnknownCard from './EmbedUnknownCard'
import EmbedPriceChart from './EmbedPriceChart'
import EmbedGenMixChart from './EmbedGenMixChart'
import EmbedLoadChart from './EmbedLoadChart'
import { VALID_METRICS, zoneLabel } from './embedUtils'

/**
 * /embed/<ZONE>/<metric> — top of the embeddable-widget tree (Task P10). Rendered
 * OUTSIDE ViewStateProvider by App.jsx (no zone/range spine, no URL rewriting, no
 * Dashboard boot fetches) — an iframe widget is a single fixed zone+metric, not a
 * navigable desk.
 *
 * The metric is checked here (a closed, static set — no fetch needed). The zone is
 * checked by whichever chart component ends up rendering, against ITS OWN fetch's
 * `zones` list — the underlying /api/power/* endpoints silently resolve an unknown
 * zone key to DE_LU, so per-metric validation against the real response is the only
 * way to avoid ever showing a wrong zone under a confidently-labeled iframe.
 */
export default function EmbedPage({ zone, metric }) {
  if (!zone) {
    return (
      <EmbedFrame zoneLabel="—" metricTitle="Unknown">
        <EmbedUnknownCard message="No zone specified — use /embed/<ZONE>/<metric>." />
      </EmbedFrame>
    )
  }

  if (!metric) {
    return (
      <EmbedFrame zoneLabel={zoneLabel(zone)} metricTitle="Unknown">
        <EmbedUnknownCard message="No metric specified — use /embed/<ZONE>/<metric>." />
      </EmbedFrame>
    )
  }

  if (!VALID_METRICS.includes(metric)) {
    return (
      <EmbedFrame zoneLabel={zoneLabel(zone)} metricTitle="Unknown">
        <EmbedUnknownCard message={`Unknown metric "${metric}".`} />
      </EmbedFrame>
    )
  }

  if (metric === 'price') return <EmbedPriceChart zone={zone} />
  if (metric === 'genmix') return <EmbedGenMixChart zone={zone} />
  return <EmbedLoadChart zone={zone} />
}
