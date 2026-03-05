import { useEffect, useRef } from 'react'
import { createChart, ColorType, LineSeries, LineStyle } from 'lightweight-charts'

function toChartData(rows, seriesId) {
  return rows
    .filter((r) => r.series_id === seriesId && r.value != null)
    .map((r) => ({ time: r.period, value: r.value }))
    .sort((a, b) => (a.time < b.time ? -1 : 1))
}

export default function PriceChart({ data }) {
  const containerRef = useRef(null)
  const chartRef = useRef(null)

  useEffect(() => {
    if (!containerRef.current || data.length === 0) return

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
        timeVisible: false,
      },
      handleScroll: true,
      handleScale: true,
    })

    const wtiSeries = chart.addSeries(LineSeries, {
      color: '#00e5ff',
      lineWidth: 2,
      title: 'WTI',
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    })

    const brentSeries = chart.addSeries(LineSeries, {
      color: '#00ff9d',
      lineWidth: 2,
      title: 'Brent',
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    })

    const wtiData = toChartData(data, 'PET.RWTC.W')
    const brentData = toChartData(data, 'PET.RBRTE.W')

    if (wtiData.length > 0) wtiSeries.setData(wtiData)
    if (brentData.length > 0) brentSeries.setData(brentData)

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
  }, [data])

  return (
    <div className="border border-border bg-surface rounded">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border">
        <div className="font-mono text-xs text-neutral-500">
          CRUDE OIL SPOT PRICES // WEEKLY
        </div>
        <div className="flex items-center gap-4 font-mono text-xs">
          <span className="flex items-center gap-1.5">
            <span className="w-3 h-0.5 bg-cyan-glow inline-block" />
            <span className="text-neutral-400">WTI</span>
          </span>
          <span className="flex items-center gap-1.5">
            <span className="w-3 h-0.5 bg-green-glow inline-block" />
            <span className="text-neutral-400">Brent</span>
          </span>
        </div>
      </div>
      <div ref={containerRef} className="h-[350px] w-full" />
    </div>
  )
}
