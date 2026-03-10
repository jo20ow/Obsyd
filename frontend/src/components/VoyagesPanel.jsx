import { useState, useEffect } from 'react'
import Panel, { InfoPopover } from './Panel'

const API = '/api'

const ZONE_COLORS = {
  malacca: '#00e5ff',
  hormuz: '#00ff9d',
  houston: '#a78bfa',
  cape: '#f59e0b',
  panama: '#ec4899',
  suez: '#94a3b8',
}

function formatTransitTime(hours) {
  if (!hours) return '--'
  if (hours < 24) return `${Math.round(hours)}h`
  const days = Math.floor(hours / 24)
  const rem = Math.round(hours % 24)
  return rem > 0 ? `${days}d ${rem}h` : `${days}d`
}

export default function VoyagesPanel({ onFlyToZone }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [days, setDays] = useState(30)

  useEffect(() => {
    setLoading(true)
    fetch(`${API}/voyages/recent?days=${days}&limit=50`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        setData(d)
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [days])

  const voyages = data?.voyages || []

  return (
    <Panel
      id="voyages"
      title="RECENT VOYAGES"
      info="Detected tanker transits between geofence zones. A voyage is logged when the same MMSI appears in different zones with a 6+ hour gap."
      collapsible
    >
      <div className="px-3 py-2 border-b border-border/30 flex items-center justify-between">
        <span className="font-mono text-[10px] text-neutral-600">
          {voyages.length} voyages ({days}d)
        </span>
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
      <div className="max-h-[350px] overflow-y-auto scrollbar-hidden">
        {loading && (
          <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">
            Loading voyages...
          </div>
        )}
        {!loading && voyages.length === 0 && (
          <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 italic">
            Collecting data — first voyages appear when tankers are tracked across multiple zones (typically 1-2 weeks).
          </div>
        )}
        {!loading && voyages.length > 0 && (
          <table className="w-full text-[10px] font-mono">
            <thead>
              <tr className="text-neutral-600 border-b border-border/30">
                <th className="text-left px-3 py-1.5">VESSEL</th>
                <th className="text-left px-2 py-1.5">ROUTE</th>
                <th className="text-right px-2 py-1.5">TRANSIT</th>
                <th className="text-right px-3 py-1.5">CLASS</th>
              </tr>
            </thead>
            <tbody>
              {voyages.map((v, i) => (
                <tr
                  key={i}
                  className="border-b border-border/20 hover:bg-white/[0.02] cursor-pointer transition-colors"
                  onClick={() => onFlyToZone?.(v.origin_zone)}
                  title={`Click to fly to ${v.origin_zone}`}
                >
                  <td className="px-3 py-1.5 text-neutral-300 truncate max-w-[120px]">
                    {v.ship_name || v.mmsi}
                  </td>
                  <td className="px-2 py-1.5">
                    <span style={{ color: ZONE_COLORS[v.origin_zone] || '#666' }}>
                      {v.origin_zone?.toUpperCase()}
                    </span>
                    <span className="text-neutral-600 mx-1">{'\u2192'}</span>
                    <span style={{ color: ZONE_COLORS[v.destination_zone] || '#666' }}>
                      {v.destination_zone?.toUpperCase()}
                    </span>
                  </td>
                  <td className="px-2 py-1.5 text-right text-neutral-400">
                    {formatTransitTime(v.transit_hours)}
                  </td>
                  <td className="px-3 py-1.5 text-right">
                    {v.ship_class ? (
                      <span className="text-[9px] px-1.5 py-0.5 rounded bg-white/5 text-neutral-400">
                        {v.ship_class}
                      </span>
                    ) : (
                      <span className="text-neutral-700">--</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Panel>
  )
}
