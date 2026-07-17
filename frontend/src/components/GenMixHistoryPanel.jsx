import { useMemo } from 'react'
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid, Legend,
} from 'recharts'
import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import { useViewState } from '../context/ViewStateContext'
import { rangeStart } from '../utils/ranges'
import { CHART_TOOLTIP_PROPS } from '../utils/chart'
import { fuelColor, sortFuels } from '../utils/fuels'

const API = '/api'

export default function GenMixHistoryPanel({ zone = 'DE_LU' }) {
  const { range } = useViewState()
  // Monthly buckets need >= 1y to be meaningful, so a short global range floors here.
  const start = rangeStart(range, 365)

  const url = `${API}/v1/genmix?zone=${zone}&start=${start}&resolution=monthly`
  const { data: resp, loading } = useFetchWithError(url, { deps: [zone, start] })

  // Convert MW → GW for readability without mutating source rows.
  const { chart, fuels } = useMemo(() => {
    const f = resp?.fuels || []
    const chart = (resp?.data || []).map((row) => {
      const o = { t: row.t }
      for (const fuel of f) if (row[fuel] != null) o[fuel] = Math.round((row[fuel] / 1000) * 10) / 10
      return o
    })
    return { chart, fuels: sortFuels(f) }
  }, [resp])

  if (!loading && (!resp?.available || fuels.length === 0)) return null

  return (
    <Panel
      id="genmix-history"
      title="GENERATION MIX · over time"
      info="Monthly-mean generation by fuel (ENTSO-E A75), stacked over years — the energy transition per zone. GW. Descriptive, from the official record."
      collapsible
      downloadUrl={`${url}&format=csv`}
      headerRight={<span className="font-mono text-[9px] text-neutral-600">GW · ≥1Y</span>}
    >
      {loading && <div className="px-4 py-8 text-center font-mono text-[10px] text-neutral-600 animate-pulse">Loading…</div>}
      {chart.length > 0 && (
        <div className="px-2 pt-2 pb-1">
          <ResponsiveContainer width="100%" height={260}>
            <AreaChart data={chart} margin={{ top: 5, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" />
              <XAxis dataKey="t" tick={{ fontSize: 8, fill: '#737373' }} minTickGap={30} />
              <YAxis tick={{ fontSize: 8, fill: '#737373' }} width={34} unit="" />
              <Tooltip {...CHART_TOOLTIP_PROPS} formatter={(v, n) => [`${Number(v).toFixed(1)} GW`, n]} />
              <Legend wrapperStyle={{ fontSize: 8, fontFamily: 'monospace' }} iconSize={7} />
              {fuels.map((f) => (
                <Area key={f} type="monotone" dataKey={f} stackId="1" stroke={fuelColor(f)}
                  fill={fuelColor(f)} fillOpacity={0.65} strokeWidth={0.5} />
              ))}
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </Panel>
  )
}
