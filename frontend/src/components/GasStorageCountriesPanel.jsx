import { useState } from 'react'
import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import { useViewState } from '../context/ViewStateContext'
import { rangeDays } from '../utils/ranges'
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, ReferenceLine,
} from 'recharts'
import { fmtDate, CHART_TOOLTIP_STYLE } from '../utils/chart'

const API = '/api'

/**
 * Storage per country — the level a power desk can act on.
 *
 * "EU storage is 51% full" averages a full Germany with an empty Ukraine, and gas does not
 * flow freely across those borders. These rows were inside every payload we fetched since
 * 2023 and were thrown away at read time.
 *
 * TWh is deliberately shown only beside that country's OWN working gas volume: coverage is a
 * property of who reports to GIE, so a cross-country sum would be an absolute we cannot
 * completely capture. Fill % is a ratio inside one complete row, so it compares safely.
 */
export default function GasStorageCountriesPanel() {
  const { range } = useViewState()
  const [selected, setSelected] = useState(null)
  const { data, loading, error } = useFetchWithError(
    `${API}/gas/storage/countries?days=${rangeDays(range)}`, { deps: [range] },
  )

  if (error) {
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">STORAGE BY COUNTRY // FETCH ERROR</div>
      </div>
    )
  }
  if (!data?.available && !loading) {
    return (
      <div className="border border-border bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-neutral-500">
          STORAGE BY COUNTRY — {data?.reason || 'no per-country AGSI data yet.'}
        </div>
      </div>
    )
  }

  const latest = data?.latest ?? []
  const history = (data?.data ?? []).filter((r) => r.country === selected)
  const shown = selected && history.length > 1 ? history : null

  return (
    <Panel
      id="gas-storage-countries"
      freshness={data}
      title="GAS STORAGE BY COUNTRY · AGSI"
      info={data?.note || 'Fill % per country. Gas is not fungible across borders — the EU average hides which country is actually short.'}
      collapsible
      headerRight={
        latest.length > 0 && (
          <span className="font-mono text-[10px] text-neutral-600">{latest.length} countries</span>
        )
      }
    >
      {loading && !data ? (
        <div className="px-4 py-4 font-mono text-[10px] text-neutral-600 animate-pulse">Loading countries…</div>
      ) : (
        <>
          <div className="px-2 py-2 overflow-x-auto">
            <table className="w-full font-mono text-[11px]">
              <thead>
                <tr className="text-[9px] text-neutral-600 uppercase tracking-wider">
                  <th className="text-left px-2 py-1">Country</th>
                  <th className="text-right px-2 py-1">Fill</th>
                  <th className="px-2 py-1"></th>
                  <th className="text-right px-2 py-1" title="In storage, beside this country's own working gas volume">Stock / capacity</th>
                  <th className="text-right px-2 py-1" title="Maximum daily withdrawal — how fast this country can actually draw on it">Max draw</th>
                </tr>
              </thead>
              <tbody>
                {latest.map((r) => {
                  const pct = r.fill_pct
                  const isSel = selected === r.country
                  return (
                    <tr
                      key={r.country}
                      onClick={() => setSelected(isSel ? null : r.country)}
                      className={`border-t border-border/30 cursor-pointer hover:bg-white/[0.02] ${isSel ? 'bg-white/[0.03]' : ''}`}
                    >
                      <td className="px-2 py-1.5 text-neutral-300">
                        {r.name || r.country}
                        {r.region === 'ne' && (
                          <span className="ml-1.5 text-[9px] text-amber-500/70" title="Non-EU reporter">NON-EU</span>
                        )}
                      </td>
                      <td className={`px-2 py-1.5 text-right ${
                        pct == null ? 'text-neutral-700' : pct < 30 ? 'text-orange-400' : 'text-neutral-200'
                      }`}>
                        {pct != null ? `${pct.toFixed(1)}%` : '—'}
                      </td>
                      <td className="px-2 py-1.5 w-28">
                        {pct != null && (
                          <div className="h-1.5 bg-neutral-900 rounded-sm">
                            <div
                              className="h-1.5 rounded-sm"
                              style={{
                                width: `${Math.max(0, Math.min(100, pct))}%`,
                                background: pct < 30 ? '#fb923c' : '#22d3ee',
                                opacity: 0.75,
                              }}
                            />
                          </div>
                        )}
                      </td>
                      <td className="px-2 py-1.5 text-right text-neutral-400">
                        {r.stock_twh != null ? `${r.stock_twh.toFixed(1)}` : '—'}
                        {r.working_gas_twh != null && (
                          <span className="text-neutral-700"> / {r.working_gas_twh.toFixed(0)} TWh</span>
                        )}
                      </td>
                      <td className="px-2 py-1.5 text-right text-neutral-500">
                        {r.withdrawal_capacity_gwh != null
                          ? `${(r.withdrawal_capacity_gwh / 1000).toFixed(1)} TWh/d`
                          : '—'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {shown && (
            <div className="px-2 pb-2">
              <div className="px-2 pb-1 font-mono text-[9px] text-neutral-600 uppercase tracking-wider">
                {selected} · fill % over time
              </div>
              <ResponsiveContainer width="100%" height={130}>
                <LineChart data={shown} margin={{ top: 4, right: 8, bottom: 2, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" />
                  <XAxis dataKey="date" tickFormatter={fmtDate} tick={{ fontSize: 8, fill: '#737373' }} minTickGap={50} />
                  <YAxis domain={[0, 100]} tick={{ fontSize: 8, fill: '#737373' }} width={30}
                    tickFormatter={(v) => `${v}%`} />
                  <ReferenceLine y={30} stroke="#fb923c" strokeDasharray="4 4" strokeOpacity={0.4} />
                  <Tooltip contentStyle={CHART_TOOLTIP_STYLE} labelFormatter={fmtDate}
                    formatter={(v) => [v == null ? '—' : `${Number(v).toFixed(1)}%`, 'fill']} />
                  <Line type="monotone" dataKey="fill_pct" stroke="#22d3ee" strokeWidth={1.4}
                    dot={false} connectNulls isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}

          <div className="px-4 pb-2 font-mono text-[9px] text-neutral-700 leading-relaxed">
            Click a country for its history. No cross-country total is shown on purpose: coverage
            is a property of who reports to GIE, and the EU aggregate above is the only complete
            one. TWh is only readable beside a country&apos;s own capacity — a full Austria holds
            less than a quarter-full Ukraine.
          </div>
        </>
      )}
    </Panel>
  )
}
