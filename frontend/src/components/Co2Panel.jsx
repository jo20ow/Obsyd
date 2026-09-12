import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import { POLL_SLOW_MS } from '../utils/poll'
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
} from 'recharts'
import { fmtTs, CHART_TOOLTIP_STYLE, useChartTheme } from '../utils/chart'

const API = '/api'

function zoneLabel(zone) {
  return zone === 'DE_LU' ? 'DE-LU' : zone
}

/**
 * Estimated CO₂ intensity of the zone's own generation (co2.intensity.* —
 * published mix × IPCC/Electricity-Maps technology factors). Two lines on
 * purpose: lifecycle is the cross-country comparison standard, direct is what
 * national inventories count — the gap between them IS a finding (it is the
 * fuel chain). Estimated and production-based, and the panel says so.
 */
export default function Co2Panel({ zone = 'DE_LU' }) {
  const url = `${API}/power/co2?zone=${zone}&hours=168`
  const { data, loading, error } = useFetchWithError(url, {
    deps: [zone],
    pollMs: POLL_SLOW_MS,
  })
  const ct = useChartTheme()
  const zl = zoneLabel(zone)

  if (error) {
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">CO₂ INTENSITY // FETCH ERROR</div>
      </div>
    )
  }

  // Never vanish silently — a zone whose mix has not derived yet says so.
  if (!data?.available && !loading) {
    return (
      <div className="border border-border bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-neutral-500">
          CO₂ INTENSITY · {zl} — {data?.reason || 'not computed yet.'}
        </div>
      </div>
    )
  }

  const rows = (data?.hourly ?? []).map((p) => ({
    x: p.ts_utc,
    lifecycle: p.lifecycle,
    direct: p.direct,
  }))
  const latest = data?.latest?.lifecycle

  return (
    <Panel
      source="Derived · ENTSO-E A75 mix × IPCC AR5 / Electricity Maps factors"
      id="power-co2"
      freshness={data}
      title={`CO₂ INTENSITY · ${zl} (EST.)`}
      info="Estimated carbon intensity of this zone's OWN generation: the published fuel mix times per-technology emission factors (lifecycle = IPCC AR5 Annex III medians; direct = the operational set — both via Electricity Maps' open factor table). Production-based, so imports are not traced; technology-average, so plant-level truth can deviate ±10–20%. The gap between the two lines is the fuel chain (construction, extraction, methane slip). API series: co2.intensity.lifecycle / .direct. Estimated, not measured — and never a forecast."
      collapsible
      headerRight={
        latest != null && (
          <span className="font-mono text-[10px] text-neutral-400">
            {Math.round(latest)} g/kWh
          </span>
        )
      }
    >
      {loading && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">
          Loading CO₂ intensity…
        </div>
      )}

      {!loading && data?.available && (
        <>
          <div className="px-4 pt-2">
            <ResponsiveContainer width="100%" height={160}>
              <LineChart data={rows} margin={{ top: 4, right: 8, bottom: 2, left: 0 }}>
                <XAxis dataKey="x" tickFormatter={fmtTs} tick={ct.tick} minTickGap={60} />
                <YAxis tick={ct.tick} width={44} domain={[0, 'auto']} />
                <Tooltip
                  contentStyle={CHART_TOOLTIP_STYLE}
                  formatter={(v, name) => [`${Math.round(v)} g/kWh`, name]}
                  labelFormatter={fmtTs}
                />
                <Legend wrapperStyle={{ fontSize: 9, fontFamily: 'inherit' }} iconSize={8} />
                <Line
                  type="monotone"
                  name="lifecycle"
                  dataKey="lifecycle"
                  stroke={ct.accent}
                  strokeWidth={1.2}
                  dot={false}
                  isAnimationActive={false}
                />
                <Line
                  type="monotone"
                  name="direct"
                  dataKey="direct"
                  stroke={ct.ink}
                  strokeWidth={1}
                  strokeDasharray="4 3"
                  dot={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <div className="px-4 py-2 font-mono text-[9px] text-neutral-700">
            last 7d hourly · estimated, production-based (imports not traced) · gap between the
            lines = fuel-chain emissions
          </div>
        </>
      )}
    </Panel>
  )
}
