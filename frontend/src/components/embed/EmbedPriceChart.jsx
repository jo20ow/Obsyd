import {
  ResponsiveContainer, ComposedChart, Area, XAxis, YAxis, Tooltip, CartesianGrid, ReferenceLine,
} from 'recharts'
import useFetchWithError from '../../hooks/useFetchWithError'
import { POLL_FAST_MS } from '../../utils/poll'
import { fmtHour, CHART_TOOLTIP_PROPS } from '../../utils/chart'
import EmbedFrame from './EmbedFrame'
import EmbedUnknownCard from './EmbedUnknownCard'
import { EMBED_COLORS, MSG_STYLE, daysSince, zoneLabel, METRIC_TITLES } from './embedUtils'

const API = '/api'
const TITLE = METRIC_TITLES.price

// Same idea as PowerDayAheadPanel's NegativeDot: a red marker on hours the auction
// cleared below zero (renewable oversupply) — the one visual fact worth a mark here.
function NegativeDot({ cx, cy, payload }) {
  if (payload?.price == null || payload.price >= 0 || cx == null || cy == null) return null
  return <circle cx={cx} cy={cy} r={2.5} fill={EMBED_COLORS.negative} stroke="none" />
}

/**
 * /embed/<zone>/price — today's (or the latest published) day-ahead hourly curve.
 * Reads GET /api/power/day-ahead/hourly?zone=, the same source PowerDayAheadPanel's
 * HOURLY view uses. The endpoint silently falls back to DE_LU for an unknown zone
 * key, so this checks the requested zone against the response's own `zones` list
 * (always present) rather than trust `data.zone` — an embed must never quietly
 * relabel itself onto a different zone than the URL asked for.
 */
export default function EmbedPriceChart({ zone }) {
  const url = `${API}/power/day-ahead/hourly?zone=${encodeURIComponent(zone)}`
  const { data, loading, error } = useFetchWithError(url, { deps: [zone], pollMs: POLL_FAST_MS })
  const zl = zoneLabel(zone)

  const zoneKnown = !data || !Array.isArray(data.zones) || data.zones.includes(zone)
  if (!zoneKnown) {
    return (
      <EmbedFrame zoneLabel={zl} metricTitle={TITLE}>
        <EmbedUnknownCard message={`Unknown zone "${zone}".`} />
      </EmbedFrame>
    )
  }

  if (error && !data) {
    return (
      <EmbedFrame zoneLabel={zl} metricTitle={TITLE}>
        <div style={MSG_STYLE}>Fetch error — try again shortly.</div>
      </EmbedFrame>
    )
  }

  if (!data?.available && !loading) {
    return (
      <EmbedFrame zoneLabel={zl} metricTitle={TITLE}>
        <div style={MSG_STYLE}>{data?.reason || 'No day-ahead data yet for this zone.'}</div>
      </EmbedFrame>
    )
  }

  const rows = data?.data ?? []
  const age = daysSince(data?.date)
  // Day-ahead is normally published ~1 day AHEAD of delivery (age can be negative) —
  // only flag it once the shown day is itself more than a day old, i.e. the collector
  // has stalled, not the ordinary "tomorrow's prices, published today" case.
  const stale = age != null && age > 1
  const freshness = data?.date ? { label: `Delivery day ${data.date} · UTC`, stale } : null
  const todayUtc = new Date().toISOString().slice(0, 10)
  const nowHour = data?.date === todayUtc ? new Date().getUTCHours() : null

  return (
    <EmbedFrame zoneLabel={zl} metricTitle={TITLE} freshness={freshness}>
      {loading && !data ? (
        <div style={MSG_STYLE}>Loading…</div>
      ) : (
        <ResponsiveContainer width="100%" height="100%" minHeight={80}>
          <ComposedChart data={rows} margin={{ top: 8, right: 10, bottom: 2, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={EMBED_COLORS.grid} />
            <XAxis
              dataKey="hour"
              tickFormatter={fmtHour}
              tick={{ fontSize: 9, fill: EMBED_COLORS.faint }}
              interval={3}
            />
            <YAxis tick={{ fontSize: 9, fill: EMBED_COLORS.faint }} width={32} />
            <ReferenceLine y={0} stroke="#2a2a3a" strokeDasharray="2 2" />
            {nowHour != null && (
              <ReferenceLine x={nowHour} stroke={EMBED_COLORS.accent} strokeDasharray="3 3" />
            )}
            <Tooltip
              {...CHART_TOOLTIP_PROPS}
              formatter={(v) => [v == null ? '—' : `${Number(v).toFixed(1)} €/MWh`, 'day-ahead']}
              labelFormatter={(h) => `${fmtHour(h)} UTC`}
            />
            <Area
              type="stepAfter"
              dataKey="price"
              stroke={EMBED_COLORS.accent}
              fill={EMBED_COLORS.accent}
              fillOpacity={0.14}
              strokeWidth={1.5}
              dot={<NegativeDot />}
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </EmbedFrame>
  )
}
