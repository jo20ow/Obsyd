import { useEffect, useState } from 'react'
import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import { POLL_SLOW_MS } from '../utils/poll'
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, ReferenceLine,
} from 'recharts'
import { fmtDate, CHART_TOOLTIP_PROPS } from '../utils/chart'

const API = '/api'

// A drag fires dozens of onChange ticks; only the settled value is worth a network round trip.
const DEBOUNCE_MS = 300
const MIN_PCT = 35
const MAX_PCT = 63
// Shown only until the first response lands and echoes the real backend default — never sent
// to the API (userPct/committedPct stay null until the user actually touches the slider).
const FALLBACK_DISPLAY_PCT = 50

/**
 * PPA / asset-modelling knob on top of the desk's own spark spread: an owner's actual CCGT
 * doesn't necessarily run at the desk's EU-fleet-average 50% efficiency assumption, and the
 * heat rate moves the spread (and the break-even carbon price) linearly. This panel re-runs
 * backend/routes/power.py::get_spark_spread server-side at whatever efficiency the slider is
 * set to — the raw TTF gas price never reaches the browser either way (ICE Endex licensing);
 * only the derived, efficiency-adjusted arithmetic does.
 */
export default function SparkCalculatorPanel({ zone = 'DE_LU' }) {
  // null = the user hasn't touched the slider yet, so the request carries no override and the
  // backend applies its own settings.gas_ccgt_efficiency — the slider's displayed position is
  // then derived straight from that response (see `displayPct` below), never adopted via an
  // effect: adjusting state to mirror a prop/response belongs in render, not in a setState-on-
  // mount effect (that cascades an extra render for no benefit here).
  const [userPct, setUserPct] = useState(null)
  const [committedPct, setCommittedPct] = useState(null)

  // Debounce the slider → fetch: don't refetch on every drag tick, only once it settles. This
  // IS a legitimate effect (syncing a timer, not deriving state), and setState happens inside
  // the timeout callback, not synchronously in the effect body.
  useEffect(() => {
    if (userPct == null) return
    const t = setTimeout(() => setCommittedPct(userPct), DEBOUNCE_MS)
    return () => clearTimeout(t)
  }, [userPct])

  const efficiency = committedPct != null ? (committedPct / 100).toFixed(2) : null
  const url = `${API}/power/spark-spread?zone=${zone}&days=365${efficiency != null ? `&efficiency=${efficiency}` : ''}`
  const { data, loading, error } = useFetchWithError(
    url, { deps: [zone, efficiency], pollMs: POLL_SLOW_MS },
  )

  // The slider's displayed position: whatever the user chose, else the backend's own default
  // once it's known, else a placeholder while the very first request is in flight.
  const displayPct = userPct ?? (data?.efficiency != null ? Math.round(data.efficiency * 100) : FALLBACK_DISPLAY_PCT)

  const zoneLabel = data?.zone === 'DE_LU' ? 'DE-LU' : (data?.zone ?? zone)

  if (error && !data) {
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">SPARK CALCULATOR // FETCH ERROR</div>
      </div>
    )
  }
  // Never vanish silently: an unavailable zone (no overlapping power/TTF price days — the
  // spark route computes live per zone, but that overlap isn't guaranteed everywhere) still
  // gets a calm explanation, not a blank space.
  if (!loading && !data?.available) {
    return (
      <div className="border border-border bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-neutral-500">
          SPARK CALCULATOR · {zoneLabel} — {data?.reason || 'no overlapping power/TTF price days yet.'}
        </div>
      </div>
    )
  }

  const rows = data?.data ?? []
  const latest = data?.latest

  return (
    <Panel
      id="power-spark-calculator"
      title={`SPARK CALCULATOR · ${zoneLabel}`}
      info="Re-runs the dirty spark spread at a CCGT efficiency you choose, instead of the desk's fixed fleet-average assumption. Realised prices, not a forecast."
      freshness={data}
      collapsible
      headerRight={
        latest?.dirty_spark_spread != null && (
          <div className="flex items-center gap-3 font-mono text-[10px]">
            <span className="text-neutral-500">
              Spread <span className="text-neutral-200">
                {latest.dirty_spark_spread >= 0 ? '+' : ''}{latest.dirty_spark_spread.toFixed(1)} €/MWh
              </span>
            </span>
            <span className="text-neutral-600">@ {displayPct}% eff.</span>
          </div>
        )
      }
    >
      <div className="px-4 py-2.5 border-b border-border/30">
        <label className="flex items-center gap-3 font-mono text-[10px] text-neutral-400">
          <span className="shrink-0 w-40">CCGT efficiency: {displayPct}%</span>
          <input
            type="range"
            min={MIN_PCT}
            max={MAX_PCT}
            step={1}
            value={displayPct}
            onChange={(e) => setUserPct(Number(e.target.value))}
            className="flex-1 accent-cyan-500"
          />
          <span className="shrink-0 w-24 text-right text-neutral-600">
            {MIN_PCT}–{MAX_PCT}%
          </span>
        </label>
      </div>

      {loading && !data ? (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">
          Computing spark spread…
        </div>
      ) : (
        <>
          {latest && (
            <div className="px-4 py-3 border-b border-border/30">
              <div className="flex items-baseline gap-4 flex-wrap">
                <div>
                  <div className="font-mono text-2xl font-bold text-neutral-100">
                    {latest.dirty_spark_spread >= 0 ? '+' : ''}{latest.dirty_spark_spread.toFixed(1)}
                  </div>
                  <div className="font-mono text-[9px] text-neutral-600">EUR/MWh dirty spark</div>
                </div>
                <div>
                  <div className={`font-mono text-2xl font-bold ${latest.breakeven_eua_eur_t != null ? 'text-amber-400' : 'text-neutral-700'}`}>
                    {latest.breakeven_eua_eur_t != null ? `€${latest.breakeven_eua_eur_t.toFixed(0)}` : '—'}
                  </div>
                  <div className="font-mono text-[9px] text-neutral-600">
                    /t CO₂ break-even{latest.breakeven_eua_eur_t == null ? ' (already negative)' : ''}
                  </div>
                </div>
                <div className="font-mono text-[10px] text-neutral-500">
                  <span className="text-neutral-600">HEAT-RATE</span>{' '}
                  <span className="text-neutral-300">{latest.heat_rate?.toFixed(3)}</span>
                </div>
              </div>
            </div>
          )}

          {rows.length > 1 && (
            <div className="px-2 py-2">
              <ResponsiveContainer width="100%" height={160}>
                <LineChart data={rows} margin={{ top: 4, right: 8, bottom: 2, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" />
                  <XAxis dataKey="date" tick={{ fontSize: 8, fill: '#737373' }}
                    tickFormatter={fmtDate} minTickGap={50} />
                  <YAxis tick={{ fontSize: 8, fill: '#737373' }} width={34} />
                  <ReferenceLine y={0} stroke="#525252" strokeDasharray="4 4" />
                  <Tooltip
                    {...CHART_TOOLTIP_PROPS}
                    labelFormatter={fmtDate}
                    formatter={(v) => [`${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(1)} €/MWh`, 'Dirty spark']}
                  />
                  <Line type="monotone" dataKey="dirty_spark_spread" name="Dirty spark"
                    stroke="#fb923c" strokeWidth={1.4} dot={false} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}

          <div className="px-4 pb-3 font-mono text-[9px] text-neutral-500 leading-relaxed">
            Dirty spark (excl. CO₂) — EUA prices are licence-blocked; breakeven EUA shows what
            CO₂ price would zero the spread. Realised day-ahead prices, not a forecast: moving the
            slider re-derives the same historical record at a different heat rate, it does not
            project anything forward.
          </div>
        </>
      )}
    </Panel>
  )
}
