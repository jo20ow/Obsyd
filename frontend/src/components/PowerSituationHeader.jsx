import useFetchWithError from '../hooks/useFetchWithError'
import { InfoPopover } from './Panel'
import PanelTakeaway from './PanelTakeaway'
import ReferenceBand from './ReferenceBand'
import { zPhrase, residualPhrase } from '../utils/takeaway'

const API = '/api'

// Plain-language legend for the descriptive desk state (Posture B: a deviation
// vs the zone's own history, never a forecast). Mirrors the backend derivation.
const STATE_LEGEND =
  'How far this zone sits from its own recent history — a deviation, not a forecast. ' +
  'STRESSED: day-ahead price or residual load ≥3σ from its ~90-day norm. ' +
  'ELEVATED: ≥2σ, or a Dunkelflaute (wind+solar <15% of load) / negative-price flag. ' +
  'CALM: within ~2σ and no flags.'

// Descriptive desk state → colour. Posture B: this is "how far from normal",
// not a forecast. CALM / ELEVATED / STRESSED come straight from the backend.
const STATE_STYLE = {
  CALM: { text: 'text-green-glow', dot: 'bg-green-glow', border: 'border-green-500/30' },
  ELEVATED: { text: 'text-yellow-400', dot: 'bg-yellow-400', border: 'border-yellow-500/30' },
  STRESSED: { text: 'text-red-400', dot: 'bg-red-400', border: 'border-red-500/30' },
}

// Per-component age tag: each metric carries its own freshness so a fresh
// day-ahead price can never make a days-old residual/renewables figure look
// current (the backend flags each block separately).
function StaleTag({ comp }) {
  if (!comp?.stale) return null
  return (
    <span
      className="font-mono text-[8px] tracking-wide text-orange-400 border border-orange-500/30 rounded px-1 py-px ml-1.5 align-middle"
      title={`Latest data ${comp.as_of} — this series may be stalled`}
    >
      {comp.age_days}d old
    </span>
  )
}

function Metric({ label, value, sub, color, band, comp }) {
  return (
    <div className="min-w-0">
      <div className="font-mono text-[9px] text-neutral-600 tracking-wider uppercase">
        {label}
        <StaleTag comp={comp} />
      </div>
      <div className={`font-mono text-xl font-bold leading-tight truncate ${color || 'text-neutral-200'}`}>
        {value}
      </div>
      {sub && <div className="font-mono text-[9px] text-neutral-600 truncate mt-0.5">{sub}</div>}
      {band}
    </div>
  )
}

/**
 * The power desk's permanent top-line — the coherence keystone. Instead of six
 * unconnected panels, it joins day-ahead price → residual load → spark spread
 * into one descriptive "so what" for the selected zone (the charts below are the
 * drill-down evidence). Always-on hero across every tab.
 */
export default function PowerSituationHeader({ zone = 'DE_LU' }) {
  const { data, loading } = useFetchWithError(`${API}/power/situation?zone=${zone}`, { deps: [zone] })

  if (loading && !data) {
    return (
      <div className="border border-border bg-surface rounded px-4 py-5 animate-pulse">
        <div className="h-3 w-40 bg-neutral-800 rounded mb-4" />
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          {[0, 1, 2, 3].map((i) => <div key={i} className="h-10 bg-neutral-800/60 rounded" />)}
        </div>
      </div>
    )
  }

  if (!data?.available) {
    return (
      <div className="border border-border bg-surface rounded px-4 py-5">
        <div className="font-mono text-[10px] text-neutral-600 tracking-wider">// POWER SITUATION</div>
        <div className="font-mono text-xs text-neutral-500 mt-2">
          No power data for {data?.zone_label || zone} yet — check back shortly.
        </div>
      </div>
    )
  }

  const st = STATE_STYLE[data.state] || STATE_STYLE.CALM
  const { price, grid, spark, flags = [] } = data

  const priceColor = price.z != null && Math.abs(price.z) >= 2 ? st.text : 'text-neutral-200'
  const residColor = grid.z != null && Math.abs(grid.z) >= 2 ? st.text : 'text-neutral-200'
  const sparkColor = spark.spark_spread == null ? 'text-neutral-500'
    : spark.spark_spread >= 0 ? 'text-green-glow' : 'text-orange-400'

  return (
    <div className={`border ${st.border} bg-surface rounded overflow-hidden`}>
      {/* Header bar: label + zone + state */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-mono text-[10px] text-neutral-500 tracking-wider">// POWER SITUATION</span>
          <span className="font-mono text-[10px] text-cyan-glow px-1.5 py-0.5 border border-cyan-glow/20 rounded">
            {data.zone_label}
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className={`w-1.5 h-1.5 rounded-full ${st.dot} ${data.state === 'STRESSED' ? 'animate-pulse' : ''}`} />
          <span className={`font-mono text-[11px] font-bold tracking-wider ${st.text}`}>{data.state}</span>
          <InfoPopover text={STATE_LEGEND} />
          {data.stale ? (
            // Show the age of the OLDEST lagging component — top-level age_days
            // tracks the newest date, which reads "0d" when only one series hangs.
            (() => {
              const worst = Math.max(
                ...[price, grid, spark].filter((c) => c?.stale).map((c) => c.age_days ?? 0), 0)
              return (
                <span
                  className="font-mono text-[9px] tracking-wide text-orange-400 border border-orange-500/30 rounded px-1 py-0.5"
                  title="At least one series is lagging — see the metric tags below"
                >
                  STALE · {worst}d
                </span>
              )
            })()
          ) : (
            data.as_of && <span className="font-mono text-[9px] text-neutral-600 hidden sm:inline">{data.as_of}</span>
          )}
        </div>
      </div>

      {/* Headline + plain-language take-away + metrics */}
      <div className="px-4 py-4">
        <div className="font-mono text-xs text-neutral-400 leading-relaxed mb-3">{data.headline}</div>

        <PanelTakeaway
          tone={data.state === 'STRESSED' ? 'alert' : data.state === 'ELEVATED' ? 'warn' : 'info'}
          className="mb-4"
        >
          {(() => {
            const drivers = []
            if (price.z != null && Math.abs(price.z) >= 1.5) drivers.push(`the day-ahead price is ${zPhrase(price.z)}`)
            if (grid.z != null && Math.abs(grid.z) >= 1.5) drivers.push(`residual load is ${residualPhrase(grid.z)}`)
            if (grid.dunkelflaute) drivers.push('wind + solar are covering under 15% of demand (Dunkelflaute)')
            if (price.negative) drivers.push('there are hours of negative day-ahead prices')
            if (data.state === 'CALM' || drivers.length === 0)
              return `${data.zone_label} power is within its normal range — nothing unusual to act on.`
            return `${data.zone_label} power looks ${data.state.toLowerCase()} because ${drivers.slice(0, 2).join(', and ')}.`
          })()}
        </PanelTakeaway>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <Metric
            label="Day-ahead"
            value={price.close != null ? `€${price.close.toFixed(0)}` : '—'}
            sub="EUR/MWh"
            color={priceColor}
            comp={price}
            band={price.z != null && <ReferenceBand z={price.z} baselineN={price.baseline_n} className="mt-1.5" />}
          />
          <Metric
            label="Residual load"
            value={grid.residual_gw != null ? `${grid.residual_gw.toFixed(0)} GW` : '—'}
            sub="load − wind − solar"
            color={residColor}
            comp={grid}
            band={grid.z != null && <ReferenceBand z={grid.z} baselineN={grid.baseline_n} className="mt-1.5" />}
          />
          <Metric
            label="Spark spread"
            value={spark.spark_spread != null ? `${spark.spark_spread >= 0 ? '+' : ''}€${spark.spark_spread.toFixed(0)}` : '—'}
            sub={spark.available ? 'CCGT margin' : 'no data yet'}
            color={sparkColor}
            comp={spark}
          />
          <Metric
            label="Renewables"
            comp={grid}
            value={
              grid.renewable_share_reliable === false ? '—'
                : grid.renewable_share != null ? `${(grid.renewable_share * 100).toFixed(0)}%` : '—'
            }
            sub={
              grid.renewable_share_reliable === false ? 'coverage limited'
                : grid.dunkelflaute ? 'Dunkelflaute' : 'of load'
            }
            color={
              grid.renewable_share_reliable === false ? 'text-neutral-500'
                : grid.dunkelflaute ? 'text-yellow-400' : 'text-neutral-200'
            }
          />
        </div>

        {/* Flags */}
        {flags.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-4">
            {flags.map((f) => (
              <span
                key={f.key}
                className="font-mono text-[9px] tracking-wide text-yellow-400 border border-yellow-500/30 rounded px-1.5 py-0.5"
              >
                {f.label}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
