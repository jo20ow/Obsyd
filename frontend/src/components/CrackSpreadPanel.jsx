import { useState, useEffect, useRef } from 'react'
import { createChart, ColorType } from 'lightweight-charts'
import Panel from './Panel'

const API = '/api'

const INFO_TEXT =
  'The 3:2:1 crack spread measures refinery profitability. ' +
  '3 barrels of crude produce 2 barrels of gasoline (RBOB) and 1 barrel of diesel (Heating Oil). ' +
  'High spread = strong refining margins = bullish crude demand. ' +
  'Low spread = weak margins = potential crude demand destruction.'

const TIMEFRAMES = [
  { label: '1M', days: 30 },
  { label: '3M', days: 90 },
  { label: '1Y', days: 365 },
]

export default function CrackSpreadPanel() {
  const [data, setData] = useState(null)
  const [liveData, setLiveData] = useState(null)
  const [timeframe, setTimeframe] = useState(TIMEFRAMES[2])
  const chartContainerRef = useRef(null)
  const chartRef = useRef(null)

  // Fetch live crack spread (Pro auth required)
  useEffect(() => {
    fetch(`${API}/signals/crack-spread`, { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : null))
      .then(setLiveData)
      .catch(() => {})
  }, [])

  // Fetch historical data (Pro endpoint)
  useEffect(() => {
    fetch(`${API}/signals/crack-spreads?days=${timeframe.days}`, { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : null))
      .then(setData)
      .catch(() => {})
  }, [timeframe])

  // Chart rendering
  useEffect(() => {
    if (!data?.history?.length || !chartContainerRef.current) return

    // Clean up previous chart
    if (chartRef.current) {
      chartRef.current.remove()
      chartRef.current = null
    }

    const chart = createChart(chartContainerRef.current, {
      width: chartContainerRef.current.clientWidth,
      height: 220,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#525252',
        fontSize: 10,
        fontFamily: 'ui-monospace, monospace',
      },
      grid: {
        vertLines: { color: '#1a1a2e' },
        horzLines: { color: '#1a1a2e' },
      },
      crosshair: {
        vertLine: { color: '#333', width: 1 },
        horzLine: { color: '#333', width: 1 },
      },
      rightPriceScale: {
        borderColor: '#1a1a2e',
      },
      timeScale: {
        borderColor: '#1a1a2e',
        timeVisible: false,
      },
    })

    chartRef.current = chart

    // 3-2-1 Crack Spread (cyan, main line)
    const s321 = chart.addSeries({
      type: 'Line',
      color: '#22d3ee',
      lineWidth: 2,
      title: '3-2-1',
      priceFormat: { type: 'price', precision: 2 },
    })
    s321.setData(
      data.history.map((d) => ({ time: d.date, value: d.three_two_one }))
    )

    // Gasoline Crack (green)
    const sGas = chart.addSeries({
      type: 'Line',
      color: '#34d399',
      lineWidth: 1,
      title: 'Gasoline',
      priceFormat: { type: 'price', precision: 2 },
    })
    sGas.setData(
      data.history.map((d) => ({ time: d.date, value: d.gasoline_crack }))
    )

    // Heating Oil Crack (amber)
    const sHO = chart.addSeries({
      type: 'Line',
      color: '#f59e0b',
      lineWidth: 1,
      title: 'HO',
      priceFormat: { type: 'price', precision: 2 },
    })
    sHO.setData(
      data.history.map((d) => ({ time: d.date, value: d.heating_oil_crack }))
    )

    chart.timeScale().fitContent()

    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        chart.applyOptions({ width: entry.contentRect.width })
      }
    })
    resizeObserver.observe(chartContainerRef.current)

    return () => {
      resizeObserver.disconnect()
      if (chartRef.current) {
        chartRef.current.remove()
        chartRef.current = null
      }
    }
  }, [data])

  const live = data?.current || liveData
  if (!live && !data) return null
  if (live?.error && !data) return null

  const spread = live?.spread_321
  const avg30 = live?.avg_30d
  const pctVs30 = avg30 ? (((spread - avg30) / avg30) * 100).toFixed(1) : null
  const pctColor = pctVs30 && parseFloat(pctVs30) >= 0 ? 'text-emerald-400' : 'text-red-400'

  const gasCrack = live ? (live.rbob_barrel || live.rbob * 42) - live.wti : null
  const hoCrack = live ? (live.ho_barrel || live.ho * 42) - live.wti : null

  const headerRight = (
    <div className="flex items-center gap-1">
      {TIMEFRAMES.map((tf) => (
        <button
          key={tf.label}
          onClick={() => setTimeframe(tf)}
          className={`px-1.5 py-0.5 text-[9px] font-mono rounded transition-colors ${
            timeframe.label === tf.label
              ? 'bg-cyan-glow/20 text-cyan-glow'
              : 'text-neutral-600 hover:text-neutral-400'
          }`}
        >
          {tf.label}
        </button>
      ))}
    </div>
  )

  return (
    <Panel id="crack-spread" title="3:2:1 CRACK SPREAD" info={INFO_TEXT} collapsible headerRight={headerRight}>
      <div className="px-4 py-3 font-mono text-xs space-y-3">
        {/* Current values */}
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-end gap-4">
            <div>
              <div className="text-[9px] text-neutral-600 tracking-wider mb-0.5">3-2-1 SPREAD</div>
              <span className="text-xl font-bold text-cyan-glow">
                ${spread != null ? spread.toFixed(2) : '---'}
              </span>
              <span className="text-neutral-500 ml-1">/bbl</span>
              {pctVs30 && (
                <span className={`ml-2 text-[10px] font-semibold ${pctColor}`}>
                  {parseFloat(pctVs30) >= 0 ? '+' : ''}{pctVs30}% vs 30d
                </span>
              )}
            </div>
          </div>
          <div className="flex gap-4 text-[10px]">
            <div className="text-center">
              <div className="text-neutral-600 tracking-wider">GASOLINE</div>
              <div className="text-emerald-400 font-bold">
                ${gasCrack != null ? gasCrack.toFixed(2) : '---'}
              </div>
            </div>
            <div className="text-center">
              <div className="text-neutral-600 tracking-wider">HEATING OIL</div>
              <div className="text-amber-400 font-bold">
                ${hoCrack != null ? hoCrack.toFixed(2) : '---'}
              </div>
            </div>
          </div>
        </div>

        {/* Chart */}
        {data?.history?.length > 0 && (
          <div className="border-t border-border pt-2">
            <div ref={chartContainerRef} />
            <div className="flex gap-4 mt-1 text-[9px] text-neutral-600">
              <span><span className="inline-block w-3 h-px bg-cyan-400 mr-1 align-middle" />3-2-1</span>
              <span><span className="inline-block w-3 h-px bg-emerald-400 mr-1 align-middle" />Gasoline</span>
              <span><span className="inline-block w-3 h-px bg-amber-400 mr-1 align-middle" />Heating Oil</span>
            </div>
          </div>
        )}

        {/* Components detail */}
        <div className="border-t border-border pt-2 space-y-1 text-[10px]">
          <div className="flex justify-between">
            <span className="text-neutral-500">WTI</span>
            <span className="text-neutral-300">${live?.wti} /bbl</span>
          </div>
          <div className="flex justify-between">
            <span className="text-neutral-500">RBOB</span>
            <span className="text-neutral-300">
              ${live?.rbob} /gal
              <span className="text-neutral-600 ml-1">(${live?.rbob_barrel || (live?.rbob * 42)?.toFixed(2)} /bbl)</span>
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-neutral-500">HO</span>
            <span className="text-neutral-300">
              ${live?.ho} /gal
              <span className="text-neutral-600 ml-1">(${live?.ho_barrel || (live?.ho * 42)?.toFixed(2)} /bbl)</span>
            </span>
          </div>
        </div>

        {live?.percentile_1y != null && (
          <div className="text-[9px] text-neutral-600 border-t border-border pt-2">
            {live.percentile_1y}th percentile vs 1Y range
          </div>
        )}
      </div>
    </Panel>
  )
}
