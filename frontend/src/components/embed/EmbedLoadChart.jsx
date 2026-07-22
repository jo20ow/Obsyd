import {
  ResponsiveContainer, ComposedChart, Line, XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts'
import useFetchWithError from '../../hooks/useFetchWithError'
import { POLL_FAST_MS } from '../../utils/poll'
import { fmtHour, fmtTs, CHART_TOOLTIP_PROPS } from '../../utils/chart'
import EmbedFrame from './EmbedFrame'
import EmbedUnknownCard from './EmbedUnknownCard'
import { EMBED_COLORS, MSG_STYLE, zoneLabel, METRIC_TITLES } from './embedUtils'

const API = '/api'
const TITLE = METRIC_TITLES.load

/**
 * /embed/<zone>/load — today's (or yesterday's) published load actual vs. the
 * day-ahead load forecast for the same hours, from GET /api/power/live?zone= — the
 * same source EmbedGenMixChart uses, just the load lines instead of the fuel stack
 * (mirrors LiveNowPanel's two views, split into two dedicated embed widgets since
 * each /embed page renders exactly one metric).
 */
export default function EmbedLoadChart({ zone }) {
  const url = `${API}/power/live?zone=${encodeURIComponent(zone)}`
  const { data, loading, error } = useFetchWithError(url, { deps: [zone], pollMs: POLL_FAST_MS })
  const zl = data?.zone_label ?? zoneLabel(zone)

  const zoneKnown = !data || !Array.isArray(data.zones) || data.zones.includes(zone)
  if (!zoneKnown) {
    return (
      <EmbedFrame zoneLabel={zoneLabel(zone)} metricTitle={TITLE}>
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
        <div style={MSG_STYLE}>{data?.reason || 'No live data yet for this zone.'}</div>
      </EmbedFrame>
    )
  }

  const hours = data?.hours ?? []
  const showingToday = data?.showing === 'today'
  const rows = hours.map((h, i) => ({ hour: i, load: h.load, load_fc: h.load_fc }))

  const freshness = data?.available
    ? {
        label: showingToday
          ? `Live · updated ${fmtTs(data.latest_actual_ts)} (${data.lag_minutes}m lag)`
          : 'Showing yesterday — no actuals published yet today',
        stale: !!data?.stale,
      }
    : null

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
            <YAxis
              tick={{ fontSize: 9, fill: EMBED_COLORS.faint }}
              width={32}
              tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`}
            />
            <Tooltip
              {...CHART_TOOLTIP_PROPS}
              formatter={(v, name) => [v == null ? '—' : `${Math.round(v).toLocaleString()} MW`, name]}
              labelFormatter={(h) => `${fmtHour(h)} UTC`}
            />
            <Line
              type="monotone"
              dataKey="load"
              name="load"
              stroke={EMBED_COLORS.accent}
              strokeWidth={1.5}
              dot={false}
              isAnimationActive={false}
            />
            <Line
              type="monotone"
              dataKey="load_fc"
              name="load forecast"
              stroke={EMBED_COLORS.text}
              strokeDasharray="4 3"
              strokeWidth={1}
              dot={false}
              connectNulls
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </EmbedFrame>
  )
}
