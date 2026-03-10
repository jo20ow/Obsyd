import { useState, useEffect, useMemo } from 'react'
import Panel from './Panel'

const API = '/api'

const ZONE_COLORS = {
  malacca: '#00e5ff',
  hormuz: '#00ff9d',
  houston: '#a78bfa',
  cape: '#f59e0b',
  panama: '#ec4899',
  suez: '#94a3b8',
}

const ZONE_ORDER = ['hormuz', 'malacca', 'suez', 'panama', 'houston', 'cape']

export default function FlowMatrixPanel() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [days, setDays] = useState(30)

  useEffect(() => {
    setLoading(true)
    fetch(`${API}/voyages/flow-matrix?days=${days}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        setData(d)
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [days])

  const { zones, matrix, maxVal } = useMemo(() => {
    if (!data?.matrix) return { zones: [], matrix: {}, maxVal: 0 }
    const m = data.matrix
    // Extract all zones from the matrix keys
    const zoneSet = new Set()
    for (const key of Object.keys(m)) {
      const [origin, dest] = key.split('\u2192')
      zoneSet.add(origin)
      zoneSet.add(dest)
    }
    // Sort by predefined order
    const sorted = [...zoneSet].sort((a, b) => {
      const ia = ZONE_ORDER.indexOf(a)
      const ib = ZONE_ORDER.indexOf(b)
      if (ia === -1 && ib === -1) return a.localeCompare(b)
      if (ia === -1) return 1
      if (ib === -1) return -1
      return ia - ib
    })
    const max = Math.max(1, ...Object.values(m))
    return { zones: sorted, matrix: m, maxVal: max }
  }, [data])

  const totalVoyages = useMemo(() => {
    return Object.values(matrix).reduce((sum, v) => sum + v, 0)
  }, [matrix])

  function cellColor(val) {
    if (!val) return 'transparent'
    const intensity = Math.min(val / maxVal, 1)
    return `rgba(0, 229, 255, ${0.1 + intensity * 0.5})`
  }

  return (
    <Panel
      id="flow-matrix"
      title="TRADE FLOW MATRIX"
      info="Tanker movement counts between chokepoint zones. Darker cells = more transits. Based on AIS geofence detections."
      collapsible
      headerRight={
        <span className="font-mono text-[10px] text-neutral-600">{totalVoyages} transits</span>
      }
    >
      <div className="px-3 py-2 border-b border-border/30 flex items-center justify-end">
        <div className="flex items-center gap-1">
          {[7, 30, 90].map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`font-mono text-[10px] px-2 py-0.5 rounded transition-colors ${
                days === d
                  ? 'bg-cyan-glow/20 text-cyan-glow'
                  : 'text-neutral-600 hover:text-neutral-400'
              }`}
            >
              {d}D
            </button>
          ))}
        </div>
      </div>
      {loading && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">
          Loading flow data...
        </div>
      )}
      {!loading && zones.length === 0 && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 italic">
          Building flow data — requires multi-zone vessel observations.
        </div>
      )}
      {!loading && zones.length > 0 && (
        <div className="overflow-x-auto scrollbar-hidden">
          <table className="w-full text-[10px] font-mono border-collapse">
            <thead>
              <tr>
                <th className="px-2 py-1.5 text-left text-neutral-600 border-b border-border/30">
                  FROM \ TO
                </th>
                {zones.map((z) => (
                  <th
                    key={z}
                    className="px-2 py-1.5 text-center border-b border-border/30"
                    style={{ color: ZONE_COLORS[z] || '#888' }}
                  >
                    {z.slice(0, 3).toUpperCase()}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {zones.map((origin) => (
                <tr key={origin} className="border-b border-border/10">
                  <td
                    className="px-2 py-1.5 font-medium"
                    style={{ color: ZONE_COLORS[origin] || '#888' }}
                  >
                    {origin.slice(0, 3).toUpperCase()}
                  </td>
                  {zones.map((dest) => {
                    const key = `${origin}\u2192${dest}`
                    const val = matrix[key] || 0
                    const isSelf = origin === dest
                    return (
                      <td
                        key={dest}
                        className="px-2 py-1.5 text-center"
                        style={{
                          backgroundColor: isSelf ? '#0a0a0f' : cellColor(val),
                          color: val > 0 ? '#e0e0e0' : '#333',
                        }}
                      >
                        {isSelf ? '\u2014' : val || ''}
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}
