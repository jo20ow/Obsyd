import {
  ResponsiveContainer, ComposedChart, Area, XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts'
import useFetchWithError from '../../hooks/useFetchWithError'
import { POLL_FAST_MS } from '../../utils/poll'
import { fmtHour, fmtTs, CHART_TOOLTIP_PROPS } from '../../utils/chart'
import { fuelColor, fuelLabel, sortFuels } from '../../utils/fuels'
import EmbedFrame from './EmbedFrame'
import EmbedUnknownCard from './EmbedUnknownCard'
import { EMBED_COLORS, MSG_STYLE, zoneLabel, METRIC_TITLES } from './embedUtils'

const API = '/api'
const TITLE = METRIC_TITLES.genmix

/**
 * /embed/<zone>/genmix — today's (or yesterday's, honestly labeled) stacked
 * generation mix by fuel, from GET /api/power/live?zone= — the same near-real-time
 * read LiveNowPanel's GENERATION view uses, simplified to just the mix (no load
 * lines; see EmbedLoadChart for those). `zone` is checked against the response's
 * own `zones` list — the endpoint itself silently resolves an unknown zone to the
 * default, so the embed layer is the one place that must not let that pass quietly.
 */
export default function EmbedGenMixChart({ zone }) {
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
  const seenFuels = new Set()
  for (const h of hours) for (const k of Object.keys(h.gen || {})) seenFuels.add(k)
  const fuels = sortFuels([...seenFuels])
  const rows = hours.map((h, i) => ({ hour: i, ...h.gen }))

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
      ) : fuels.length === 0 ? (
        <div style={MSG_STYLE}>No generation-mix data for this zone yet.</div>
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
              formatter={(v, name) => [v == null ? '—' : `${Math.round(v).toLocaleString()} MW`, fuelLabel(name)]}
              labelFormatter={(h) => `${fmtHour(h)} UTC`}
            />
            {fuels.map((fuel) => (
              <Area
                key={fuel}
                type="monotone"
                dataKey={fuel}
                name={fuel}
                stackId="gen"
                stroke={fuelColor(fuel)}
                fill={fuelColor(fuel)}
                fillOpacity={0.4}
                strokeWidth={1}
                dot={false}
                isAnimationActive={false}
              />
            ))}
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </EmbedFrame>
  )
}
