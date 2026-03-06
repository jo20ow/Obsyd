import { useState, useEffect, useRef, useCallback } from 'react'
import { createChart, ColorType, LineSeries, CandlestickSeries, LineStyle } from 'lightweight-charts'

const API = '/api'

const TIMEFRAMES = [
  { label: '1D', interval: '15min', outputsize: 96, type: 'intraday' },
  { label: '1W', interval: '1h', outputsize: 168, type: 'intraday' },
  { label: '1M', interval: '4h', outputsize: 180, type: 'intraday' },
  { label: '3M', type: 'weekly' },
  { label: '1Y', type: 'weekly' },
]

const SYMBOLS = [
  { key: 'WTI', label: 'WTI', color: '#00e5ff' },
  { key: 'BRENT', label: 'Brent', color: '#00ff9d' },
  { key: 'NG', label: 'Nat Gas', color: '#a78bfa' },
  { key: 'GOLD', label: 'Gold', color: '#fbbf24' },
]

function toChartData(rows, seriesId) {
  return rows
    .filter((r) => r.series_id === seriesId && r.value != null)
    .map((r) => ({ time: r.period, value: r.value }))
    .sort((a, b) => (a.time < b.time ? -1 : 1))
}

function filterWeeklyByRange(data, label) {
  if (!data.length) return data
  const now = new Date()
  let cutoff
  if (label === '3M') {
    cutoff = new Date(now.getFullYear(), now.getMonth() - 3, now.getDate())
  } else {
    cutoff = new Date(now.getFullYear() - 1, now.getMonth(), now.getDate())
  }
  const cutoffStr = cutoff.toISOString().slice(0, 10)
  return data.filter((d) => d.time >= cutoffStr)
}

const EIA_MAP = {
  WTI: 'PET.RWTC.W',
  BRENT: 'PET.RBRTE.W',
  NG: 'NG.RNGWHHD.W',
}

export default function PriceChart({ data }) {
  const containerRef = useRef(null)
  const chartRef = useRef(null)
  const seriesRef = useRef(null)

  const [timeframe, setTimeframe] = useState(TIMEFRAMES[3]) // 3M default
  const [symbol, setSymbol] = useState(SYMBOLS[0]) // WTI default
  const [intradayData, setIntradayData] = useState(null)
  const [intradayProxy, setIntradayProxy] = useState(null) // e.g. "USO ETF"
  const [loading, setLoading] = useState(false)

  // Fetch intraday data when timeframe/symbol change
  useEffect(() => {
    if (timeframe.type !== 'intraday') {
      setIntradayData(null)
      return
    }
    setLoading(true)
    fetch(`${API}/prices/intraday?symbol=${symbol.key}&interval=${timeframe.interval}&outputsize=${timeframe.outputsize}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        setIntradayData(d?.data?.length ? d.data : null)
        setIntradayProxy(d?.is_proxy ? d.proxy_symbol : null)
        setLoading(false)
      })
      .catch((e) => {
        console.error('Intraday fetch:', e)
        setIntradayData(null)
        setLoading(false)
      })
  }, [timeframe, symbol])

  // Render chart
  useEffect(() => {
    if (!containerRef.current) return

    // Determine what data to show
    const isIntraday = timeframe.type === 'intraday' && intradayData?.length > 0
    const isWeekly = timeframe.type === 'weekly'

    // For weekly without EIA data, or intraday loading/empty: skip render
    if (!isIntraday && !isWeekly) return
    if (isWeekly && data.length === 0) return

    // Clean up previous chart
    if (chartRef.current) {
      chartRef.current.remove()
      chartRef.current = null
      seriesRef.current = null
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
      crosshair: {
        vertLine: { color: '#00e5ff33', labelBackgroundColor: '#12121a' },
        horzLine: { color: '#00e5ff33', labelBackgroundColor: '#12121a' },
      },
      rightPriceScale: {
        borderColor: '#1e1e2e',
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
      timeScale: {
        borderColor: '#1e1e2e',
        timeVisible: isIntraday,
      },
      handleScroll: true,
      handleScale: true,
    })

    if (isIntraday) {
      // Candlestick chart for intraday
      const series = chart.addSeries(CandlestickSeries, {
        upColor: '#00ff9d',
        downColor: '#ff5050',
        borderUpColor: '#00ff9d',
        borderDownColor: '#ff5050',
        wickUpColor: '#00ff9d',
        wickDownColor: '#ff5050',
        title: symbol.label,
        priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
      })

      const candles = intradayData.map((d) => ({
        time: Math.floor(new Date(d.datetime).getTime() / 1000),
        open: d.open,
        high: d.high,
        low: d.low,
        close: d.close,
      }))
      series.setData(candles)
      seriesRef.current = series
    } else {
      // Line chart for weekly EIA data
      if (symbol.key === 'GOLD') {
        // Gold has no EIA data, show WTI+Brent
        const wtiSeries = chart.addSeries(LineSeries, {
          color: '#00e5ff', lineWidth: 2, title: 'WTI',
          priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
        })
        const brentSeries = chart.addSeries(LineSeries, {
          color: '#00ff9d', lineWidth: 2, title: 'Brent',
          priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
        })
        const wtiData = filterWeeklyByRange(toChartData(data, 'PET.RWTC.W'), timeframe.label)
        const brentData = filterWeeklyByRange(toChartData(data, 'PET.RBRTE.W'), timeframe.label)
        if (wtiData.length) wtiSeries.setData(wtiData)
        if (brentData.length) brentSeries.setData(brentData)
      } else {
        const eiaId = EIA_MAP[symbol.key]
        if (eiaId) {
          const series = chart.addSeries(LineSeries, {
            color: symbol.color, lineWidth: 2, title: symbol.label,
            priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
          })
          const chartData = filterWeeklyByRange(toChartData(data, eiaId), timeframe.label)
          if (chartData.length) series.setData(chartData)
          seriesRef.current = series
        }

        // Also show the other oil line for WTI/Brent
        if (symbol.key === 'WTI') {
          const brentSeries = chart.addSeries(LineSeries, {
            color: '#00ff9d', lineWidth: 1, title: 'Brent',
            priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
          })
          const brentData = filterWeeklyByRange(toChartData(data, 'PET.RBRTE.W'), timeframe.label)
          if (brentData.length) brentSeries.setData(brentData)
        } else if (symbol.key === 'BRENT') {
          const wtiSeries = chart.addSeries(LineSeries, {
            color: '#00e5ff', lineWidth: 1, title: 'WTI',
            priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
          })
          const wtiData = filterWeeklyByRange(toChartData(data, 'PET.RWTC.W'), timeframe.label)
          if (wtiData.length) wtiSeries.setData(wtiData)
        }
      }
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
      seriesRef.current = null
    }
  }, [data, timeframe, symbol, intradayData])

  const isIntraday = timeframe.type === 'intraday'

  return (
    <div className="border border-border bg-surface rounded">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border flex-wrap gap-2">
        {/* Symbol selector */}
        <div className="flex items-center gap-2">
          {SYMBOLS.map((s) => (
            <button
              key={s.key}
              onClick={() => setSymbol(s)}
              className={`font-mono text-xs px-2 py-0.5 rounded transition-colors ${
                symbol.key === s.key
                  ? 'bg-white/10 text-neutral-200'
                  : 'text-neutral-500 hover:text-neutral-300'
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>

        {/* Timeframe buttons */}
        <div className="flex items-center gap-1">
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
          {loading && (
            <span className="font-mono text-[10px] text-neutral-600 animate-pulse ml-1">...</span>
          )}
        </div>
      </div>
      {isIntraday && intradayProxy && (
        <div className="px-4 py-1.5 font-mono text-[10px] text-orange-400/80 bg-orange-400/5 border-b border-orange-400/10">
          Intraday via {intradayProxy} — price action only, not spot price
        </div>
      )}
      <div ref={containerRef} className="h-[350px] w-full" />
      {isIntraday && !intradayData && !loading && (
        <div className="px-4 py-2 font-mono text-[10px] text-neutral-600">
          Intraday data requires Twelve Data API key. Showing weekly EIA data for 3M/1Y.
        </div>
      )}
    </div>
  )
}
