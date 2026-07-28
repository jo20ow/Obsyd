import Panel from './Panel'
import PanelTakeaway from './PanelTakeaway'
import useFetchWithError from '../hooks/useFetchWithError'
import { POLL_SLOW_MS } from '../utils/poll'
import { fuelColor } from '../utils/fuels'

const API = '/api'

// Each merit band rendered through a representative ENTSO-E B-code so the colors
// come from the ONE canonical fuel palette (utils/fuels.js) — never invented hues.
const TECH_PSR = {
  must_run_renewables: 'B19', // wind onshore cyan — the face of the must-run block
  nuclear: 'B14',
  lignite: 'B02',
  hard_coal: 'B05',
  gas: 'B04',
  oil: 'B06',
  hydro_flex: 'B10',
}

const TECH_SHORT = {
  must_run_renewables: 'Renewables/must-run',
  nuclear: 'Nuclear',
  lignite: 'Lignite',
  hard_coal: 'Hard coal',
  gas: 'Gas',
  oil: 'Oil',
  hydro_flex: 'Flex hydro',
}

// Stable stack/legend order, cheapest band first (mirrors backend MERIT_BANDS).
const TECH_ORDER = ['must_run_renewables', 'nuclear', 'lignite', 'hard_coal', 'gas', 'oil', 'hydro_flex']

// "2026-07-27T18:00:00+00:00" → "Jul 27 18:00" (all timestamps are UTC)
function fmtHour(iso) {
  const d = new Date(iso)
  return `${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })} ${String(d.getUTCHours()).padStart(2, '0')}:00`
}

/**
 * Which technology is (estimated to be) setting the day-ahead price, hour by
 * hour. An ESTIMATE by construction: the backend assumes the conventional
 * merit-order ORDER (no fuel/CO2/efficiency data exists to compute a real one)
 * and attributes each hour to the most expensive band that meaningfully
 * dispatches — see backend/power/marginal.py. Dimmed cells are "tension" hours
 * whose price sits outside the technology's coarse expected band: reported,
 * never reclassified.
 */
export default function MarginalTechPanel({ zone = 'DE_LU' }) {
  const { data, loading, error } = useFetchWithError(`${API}/power/marginal?zone=${zone}`, { deps: [zone], pollMs: POLL_SLOW_MS })

  if (error && !data) {
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">PRICE-SETTING TECH // FETCH ERROR</div>
      </div>
    )
  }

  if (!data?.available && !loading) {
    return (
      <div className="border border-border bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-neutral-500">
          PRICE-SETTING TECH (ESTIMATED) — {data?.reason || 'no data for this zone yet.'}
        </div>
      </div>
    )
  }

  const hourly = data?.hourly ?? []
  const daily = data?.daily ?? []
  // Attributed hours per UTC day, from the hourly rows — so a partial edge day
  // (one attributed hour rendering as a 100% bar) says how thin it really is.
  const hoursByDay = {}
  for (const h of hourly) {
    const day = h.ts_utc.slice(0, 10)
    hoursByDay[day] = (hoursByDay[day] || 0) + 1
  }
  const summary = data?.summary
  const shares = summary?.share_of_hours ?? {}
  const techsPresent = TECH_ORDER.filter((t) => shares[t] != null)
  const topTech = techsPresent.reduce((a, b) => ((shares[a] ?? 0) >= (shares[b] ?? 0) ? a : b), techsPresent[0])

  return (
    <Panel
      id="marginal-tech"
      title="PRICE-SETTING TECH (ESTIMATED)"
      freshness={data}
      info={data?.method || 'Technology-level estimate from a fixed conventional merit order — the order is assumed, not computed (no fuel, CO2 or efficiency data exists in this repo). Descriptive, not a model of the SDAC auction and not a forecast.'}
      collapsible
      headerRight={
        summary != null && (
          <span className="font-mono text-[9px] text-neutral-600">
            {summary.attributed_hours}h · {summary.consistent_pct}% consistent
          </span>
        )
      }
    >
      {loading && !data ? (
        <div className="px-4 py-4 font-mono text-[10px] text-neutral-600 animate-pulse">Loading attribution…</div>
      ) : (
        <>
          {summary && topTech && (
            <div className="px-4 py-3 border-b border-border/30">
              <PanelTakeaway tone="info">
                {`${TECH_SHORT[topTech]} is the estimated price-setter in ${shares[topTech]}% of the last ${summary.attributed_hours} attributed hours; the price sat inside the assumed band in ${summary.consistent_pct}% of them.`}
              </PanelTakeaway>
            </div>
          )}

          {/* Hourly strip — one cell per attributed hour, colored by the estimated
              price-setting band. Tension hours render dimmed, with the caption below. */}
          {hourly.length > 0 && (
            <div className="px-4 pt-3">
              <div className="flex h-7 rounded-sm overflow-hidden">
                {hourly.map((h) => (
                  <div
                    key={h.ts_utc}
                    className="flex-1 min-w-[1px]"
                    style={{
                      backgroundColor: fuelColor(TECH_PSR[h.tech]),
                      opacity: h.consistency === 'tension' ? 0.35 : 1,
                    }}
                    title={`${fmtHour(h.ts_utc)} UTC · €${h.price}/MWh · ${h.tech_label} (${h.share_pct}%, ${Math.round(h.mw)} MW)${h.consistency === 'tension' ? ' · TENSION: price outside this band' : ''}`}
                  />
                ))}
              </div>
              <div className="flex justify-between font-mono text-[8px] text-neutral-600 pt-0.5">
                <span>{fmtHour(hourly[0].ts_utc)}</span>
                <span>{fmtHour(hourly[hourly.length - 1].ts_utc)} UTC</span>
              </div>
              <div className="font-mono text-[9px] text-neutral-600 pt-1">
                Dimmed hours = price outside the technology&apos;s coarse expected band (&quot;tension&quot;) — reported, never reclassified.
              </div>
            </div>
          )}

          {/* Legend */}
          <div className="flex flex-wrap gap-x-3 gap-y-1 px-4 pt-2">
            {techsPresent.map((t) => (
              <span key={t} className="inline-flex items-center gap-1 font-mono text-[9px] text-neutral-500">
                <span className="w-2 h-2 rounded-sm inline-block" style={{ backgroundColor: fuelColor(TECH_PSR[t]) }} />
                {TECH_SHORT[t]}
              </span>
            ))}
          </div>

          {/* Daily share-of-hours stacked bars over the window */}
          {daily.length > 0 && (
            <div className="px-4 pt-3 pb-3 space-y-1">
              <div className="font-mono text-[9px] text-neutral-600 uppercase tracking-wider">Share of attributed hours per day</div>
              {daily.map((d) => (
                <div key={d.date} className="flex items-center gap-2">
                  <span className="font-mono text-[9px] text-neutral-500 w-16 shrink-0">{d.date.slice(5)}</span>
                  <div className="flex h-3 flex-1 rounded-sm overflow-hidden">
                    {TECH_ORDER.filter((t) => d.shares[t] != null).map((t) => (
                      <div
                        key={t}
                        style={{ width: `${d.shares[t]}%`, backgroundColor: fuelColor(TECH_PSR[t]) }}
                        title={`${d.date} (${hoursByDay[d.date] ?? 0}h attributed) · ${TECH_SHORT[t]}: ${d.shares[t]}% of attributed hours`}
                      />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="px-4 pb-3 font-mono text-[9px] text-neutral-700 leading-relaxed">
            Estimated from a fixed conventional merit order — no fuel, CO2 or per-plant efficiency data exists here, so the order is assumed, not computed. Flexible hydro bids opportunity cost; imports can set the price with no domestic technology marginal at all. Not a model of the auction, not a forecast.
          </div>
        </>
      )}
    </Panel>
  )
}
