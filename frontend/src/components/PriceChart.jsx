import { useState, useEffect, useRef } from 'react'
import { createChart, ColorType, LineSeries, CandlestickSeries, LineStyle } from 'lightweight-charts'

const API = '/api'

const TIMEFRAMES = [
  { label: '1D', interval: '15min', outputsize: 96, type: 'intraday' },
  { label: '1W', interval: '1h', outputsize: 168, type: 'intraday' },
  { label: '1M', interval: '1day', outputsize: 30, type: 'intraday' },
  { label: '3M', interval: '1day_6m', outputsize: 90, type: 'hybrid' },
  { label: '1Y', interval: '1day_1y', outputsize: 365, type: 'hybrid' },
  { label: 'ALL', type: 'fred' },
]

const SYMBOLS = [
  { key: 'WTI', label: 'WTI', color: '#00e5ff', fred: 'DCOILWTICO' },
  { key: 'BRENT', label: 'Brent', color: '#00ff9d', fred: 'DCOILBRENTEU' },
  { key: 'NG', label: 'Nat Gas', color: '#a78bfa' },
  { key: 'GOLD', label: 'Gold', color: '#fbbf24' },
  { key: 'SILVER', label: 'Silver', color: '#94a3b8' },
  { key: 'COPPER', label: 'Copper', color: '#f97316' },
]

function filterByRange(data, label) {
  if (!data.length) return data
  const now = new Date()
  let cutoff
  if (label === '3M') cutoff = new Date(now.getFullYear(), now.getMonth() - 3, now.getDate())
  else if (label === '1Y') cutoff = new Date(now.getFullYear() - 1, now.getMonth(), now.getDate())
  else return data
  const cutoffStr = cutoff.toISOString().slice(0, 10)
  return data.filter((d) => d.time >= cutoffStr)
}

export default function PriceChart({ data }) {
  const containerRef = useRef(null)
  const chartRef = useRef(null)
  const seriesRef = useRef(null)

  const [timeframe, setTimeframe] = useState(TIMEFRAMES[3])
  const [symbol, setSymbol] = useState(SYMBOLS[0])
  const [chartStyle, setChartStyle] = useState('candle') // 'candle' | 'line'
  const [intradayData, setIntradayData] = useState(null)
  const [intradayProxy, setIntradayProxy] = useState(null)
  const [fredData, setFredData] = useState(null)
  const [loading, setLoading] = useState(false)

  // Fetch intraday/OHLCV data (yfinance) for 1D/1W/1M and hybrid 3M/1Y candle mode
  useEffect(() => {
    const needsIntraday = timeframe.type === 'intraday' || (timeframe.type === 'hybrid' && chartStyle === 'candle')
    if (!needsIntraday) {
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
      .catch(() => {
        setIntradayData(null)
        setLoading(false)
      })
  }, [timeframe, symbol, chartStyle])

  // Fetch FRED daily data for line mode (fred + hybrid) via dedicated /chart endpoint
  useEffect(() => {
    const needsFred = timeframe.type === 'fred' || (timeframe.type === 'hybrid' && chartStyle === 'line')
    if (!needsFred) return
    if (fredData) return // only fetch once
    setLoading(true)
    fetch(`${API}/prices/chart`)
      .then((r) => (r.ok ? r.json() : {}))
      .then((d) => {
        setFredData(d)
        setLoading(false)
      })
      .catch(() => {
        setFredData(null)
        setLoading(false)
      })
  }, [timeframe, chartStyle])

  // Render chart
  useEffect(() => {
    if (!containerRef.current) return

    const isIntraday = (timeframe.type === 'intraday' || (timeframe.type === 'hybrid' && chartStyle === 'candle')) && intradayData?.length > 0
    const isFred = (timeframe.type === 'fred' || (timeframe.type === 'hybrid' && chartStyle === 'line')) && fredData

    if (!isIntraday && !isFred) return

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
        timeVisible: isIntraday && timeframe.label !== '1M' && timeframe.type !== 'hybrid',
      },
      handleScroll: true,
      handleScale: true,
    })

    if (isIntraday) {
      const candles = intradayData.map((d) => ({
        time: Math.floor(new Date(d.datetime).getTime() / 1000),
        open: d.open,
        high: d.high,
        low: d.low,
        close: d.close,
      }))

      if (chartStyle === 'candle') {
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
        series.setData(candles)
        seriesRef.current = series
      } else {
        const lineData = candles.map((c) => ({ time: c.time, value: c.close }))
        const series = chart.addSeries(LineSeries, {
          color: symbol.color, lineWidth: 2, title: symbol.label,
          priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
        })
        series.setData(lineData)
        seriesRef.current = series
      }
    } else if (isFred) {
      const fredId = symbol.fred
      if (fredId && fredData[fredId]) {
        const chartData = filterByRange(fredData[fredId], timeframe.label)

        if (chartStyle === 'line' || isFred) {
          const series = chart.addSeries(LineSeries, {
            color: symbol.color, lineWidth: 2, title: symbol.label,
            priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
          })
          if (chartData.length) series.setData(chartData)
          seriesRef.current = series
        }

        // Companion line
        if (symbol.key === 'WTI' && fredData['DCOILBRENTEU']) {
          const s2 = chart.addSeries(LineSeries, {
            color: '#00ff9d', lineWidth: 1, title: 'Brent',
            priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
          })
          s2.setData(filterByRange(fredData['DCOILBRENTEU'], timeframe.label))
        } else if (symbol.key === 'BRENT' && fredData['DCOILWTICO']) {
          const s2 = chart.addSeries(LineSeries, {
            color: '#00e5ff', lineWidth: 1, title: 'WTI',
            priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
          })
          s2.setData(filterByRange(fredData['DCOILWTICO'], timeframe.label))
        }
      } else {
        // No FRED data for this symbol — show WTI+Brent
        if (fredData['DCOILWTICO']) {
          const s1 = chart.addSeries(LineSeries, {
            color: '#00e5ff', lineWidth: 2, title: 'WTI',
            priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
          })
          s1.setData(filterByRange(fredData['DCOILWTICO'], timeframe.label))
        }
        if (fredData['DCOILBRENTEU']) {
          const s2 = chart.addSeries(LineSeries, {
            color: '#00ff9d', lineWidth: 2, title: 'Brent',
            priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
          })
          s2.setData(filterByRange(fredData['DCOILBRENTEU'], timeframe.label))
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
  }, [data, timeframe, symbol, intradayData, fredData, chartStyle])

  const isIntraday = timeframe.type === 'intraday' || (timeframe.type === 'hybrid' && chartStyle === 'candle')

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

        <div className="flex items-center gap-2">
          {/* Chart style toggle */}
          <div className="flex items-center border border-border rounded overflow-hidden">
            <button
              onClick={() => setChartStyle('candle')}
              title="Candlestick"
              className={`px-1.5 py-0.5 text-[10px] font-mono transition-colors ${
                chartStyle === 'candle'
                  ? 'bg-white/10 text-neutral-200'
                  : 'text-neutral-600 hover:text-neutral-400'
              }`}
            >
              &#x2583;&#x2581;&#x2583;
            </button>
            <button
              onClick={() => setChartStyle('line')}
              title="Line"
              className={`px-1.5 py-0.5 text-[10px] font-mono transition-colors ${
                chartStyle === 'line'
                  ? 'bg-white/10 text-neutral-200'
                  : 'text-neutral-600 hover:text-neutral-400'
              }`}
            >
              &#x2571;&#x2572;&#x2571;
            </button>
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
      </div>
      {isIntraday && intradayProxy && (
        <div className="px-4 py-1.5 font-mono text-[10px] text-orange-400/80 bg-orange-400/5 border-b border-orange-400/10">
          Intraday via {intradayProxy} — price action only, not spot price
        </div>
      )}
      <div ref={containerRef} className="h-[350px] w-full" />
      {isIntraday && !intradayData && !loading && (
        <div className="px-4 py-2 font-mono text-[10px] text-neutral-600">
          No intraday data available. Try 3M/1Y/ALL for daily prices.
        </div>
      )}
    </div>
  )
}
