import { useState, useEffect, useMemo } from 'react'
import { createChart, LineSeries, LineStyle, ColorType } from 'lightweight-charts'
import { useRef } from 'react'
import { InfoPopover } from './Panel'

const API = '/api'

const ZONE_COLORS = {
  malacca: '#00e5ff',
  hormuz: '#00ff9d',
  houston: '#a78bfa',
  cape: '#f59e0b',
  panama: '#ec4899',
  suez: '#94a3b8',
}

const TIMEFRAMES = [
  { label: '30D', days: 30 },
  { label: '60D', days: 60 },
  { label: '90D', days: 90 },
]

const VIEW_MODES = [
  { key: 'all', label: 'ALL VESSELS' },
  { key: 'transit', label: 'IN TRANSIT' },
]

export default function ZoneActivityChart() {
  const containerRef = useRef(null)
  const chartRef = useRef(null)
  const [data, setData] = useState(null)
  const [portwatchData, setPortwatchData] = useState(null)
  const [timeframe, setTimeframe] = useState(TIMEFRAMES[2])
  const [viewMode, setViewMode] = useState('all')
  const [hiddenZones, setHiddenZones] = useState(new Set())
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    Promise.all([
      fetch(`${API}/vessels/zone-history?days=${timeframe.days}`).then((r) => (r.ok ? r.json() : null)),
      fetch(`${API}/portwatch/chokepoints`).then((r) => (r.ok ? r.json() : null)),
    ])
      .then(([zh, pw]) => {
        setData(zh)
        setPortwatchData(pw)
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [timeframe])

  const zones = useMemo(() => (data?.zones ? Object.keys(data.zones) : []), [data])

  const toggleZone = (z) => {
    setHiddenZones((prev) => {
      const next = new Set(prev)
      next.has(z) ? next.delete(z) : next.add(z)
      return next
    })
  }

  // Render chart
  useEffect(() => {
    if (!containerRef.current || !data?.zones) return

    if (chartRef.current) {
      chartRef.current.remove()
      chartRef.current = null
    }

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#0a0a0f' },
        textColor: '#555566',
        fontFamily: 'JetBrains Mono, monospace',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: '#1a1a25', style: LineStyle.Dotted },
        horzLines: { color: '#1a1a25', style: LineStyle.Dotted },
      },
      rightPriceScale: { borderColor: '#1e1e2e' },
      timeScale: { borderColor: '#1e1e2e' },
      handleScroll: true,
      handleScale: true,
    })

    for (const [zone, points] of Object.entries(data.zones)) {
      if (hiddenZones.has(zone)) continue
      const color = ZONE_COLORS[zone] || '#666'
      const series = chart.addSeries(LineSeries, {
        color,
        lineWidth: 2,
        title: zone.toUpperCase(),
        priceFormat: { type: 'price', precision: 0, minMove: 1 },
      })
      series.setData(
        points.map((p) => {
          const total = p.tanker_count || 0
          const anchored = p.slow_movers || 0
          const value = viewMode === 'transit' ? Math.max(0, total - anchored) : total
          return { time: p.date, value }
        })
      )
    }

    chart.timeScale().fitContent()
    chartRef.current = chart

    const handleResize = () => {
      chart.applyOptions({ width: containerRef.current.clientWidth })
    }
    const observer = new ResizeObserver(handleResize)
    observer.observe(containerRef.current)

    return () => {
      observer.disconnect()
      chart.remove()
      chartRef.current = null
    }
  }, [data, hiddenZones, viewMode])

  return (
    <div className="border border-border bg-surface rounded">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs text-neutral-500">ZONE VESSEL ACTIVITY</span>
          <InfoPopover text="Daily unique AIS-reporting vessels per geofence zone. Includes anchored, in-port, and transiting vessels. Toggle 'In Transit' to exclude anchored vessels (SOG < 0.5 kn)." />
        </div>
        <div className="flex items-center gap-2">
          {/* Zone toggles */}
          <div className="flex items-center gap-1.5">
            {zones.map((z) => (
              <button
                key={z}
                onClick={() => toggleZone(z)}
                className={`font-mono text-[10px] px-1.5 py-0.5 rounded transition-colors ${
                  hiddenZones.has(z)
                    ? 'text-neutral-700 line-through'
                    : 'text-neutral-300'
                }`}
                style={{ borderBottom: hiddenZones.has(z) ? 'none' : `2px solid ${ZONE_COLORS[z] || '#666'}` }}
              >
                {z.toUpperCase()}
              </button>
            ))}
          </div>
          {/* View mode */}
          <div className="flex items-center gap-1 ml-2 border-l border-border pl-2">
            {VIEW_MODES.map((vm) => (
              <button
                key={vm.key}
                onClick={() => setViewMode(vm.key)}
                className={`font-mono text-[10px] px-2 py-0.5 rounded transition-colors ${
                  viewMode === vm.key
                    ? 'bg-cyan-glow/20 text-cyan-glow'
                    : 'text-neutral-600 hover:text-neutral-400'
                }`}
              >
                {vm.label}
              </button>
            ))}
          </div>
          {/* Timeframe */}
          <div className="flex items-center gap-1 ml-2">
            {TIMEFRAMES.map((tf) => (
              <button
                key={tf.label}
                onClick={() => setTimeframe(tf)}
                className={`font-mono text-[10px] px-2 py-0.5 rounded transition-colors ${
                  timeframe.label === tf.label
                    ? 'bg-cyan-glow/20 text-cyan-glow'
                    : 'text-neutral-600 hover:text-neutral-400'
                }`}
              >
                {tf.label}
              </button>
            ))}
          </div>
          {loading && <span className="font-mono text-[10px] text-neutral-600 animate-pulse">...</span>}
        </div>
      </div>
      <div className="relative">
        <span className="absolute left-1 top-1/2 -translate-y-1/2 -rotate-90 font-mono text-[9px] text-neutral-600 tracking-wider whitespace-nowrap pointer-events-none z-10 origin-center">
          VESSELS IN ZONE (AIS)
        </span>
        <div ref={containerRef} className="h-[250px] w-full" />
      </div>
      {!loading && zones.length === 0 && (
        <div className="px-4 py-4 font-mono text-[10px] text-neutral-600 text-center">
          No zone activity data yet. Data populates after daily aggregation runs.
        </div>
      )}
      <div className="px-4 py-1.5 border-t border-border">
        <span className="font-mono text-[9px] text-neutral-600 leading-relaxed">
          Unique AIS-reporting vessels per day within zone boundaries. Includes anchored, in-port, and transiting vessels. Not equivalent to transit counts.
        </span>
      </div>
    </div>
  )
}
