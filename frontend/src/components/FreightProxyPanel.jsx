import { useState, useEffect, useRef } from 'react'
import { createChart, ColorType } from 'lightweight-charts'
import Panel from './Panel'

const API = '/api'

export default function FreightProxyPanel() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [tmData, setTmData] = useState(null)
  const chartRef = useRef(null)
  const containerRef = useRef(null)

  useEffect(() => {
    Promise.all([
      fetch(`${API}/analytics/freight-proxy?days=90`).then((r) => (r.ok ? r.json() : null)),
      fetch(`${API}/analytics/tonne-miles?days=90`).then((r) => (r.ok ? r.json() : null)),
    ])
      .then(([fp, tm]) => {
        setData(fp)
        setTmData(tm)
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [])

  // Chart
  useEffect(() => {
    if (!data?.history?.length || !containerRef.current) return
    if (chartRef.current) {
      chartRef.current.remove()
      chartRef.current = null
    }

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 200,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#525252',
        fontSize: 10,
        fontFamily: 'ui-monospace, monospace',
      },
      grid: { vertLines: { color: '#1a1a2e' }, horzLines: { color: '#1a1a2e' } },
      rightPriceScale: { borderColor: '#1a1a2e', scaleMargins: { top: 0.1, bottom: 0.1 } },
      leftPriceScale: { visible: true, borderColor: '#1a1a2e', scaleMargins: { top: 0.1, bottom: 0.1 } },
      timeScale: { borderColor: '#1a1a2e', timeVisible: false },
    })

    // Freight proxy (right axis)
    const freightSeries = chart.addSeries({
      type: 'Line',
      color: '#22d3ee',
      lineWidth: 2,
      title: 'Freight Proxy',
      priceScaleId: 'right',
      priceFormat: { type: 'price', precision: 1 },
    })
    freightSeries.setData(
      data.history.map((d) => ({ time: d.date, value: d.index }))
    )

    // Rerouting index (left axis) — from tonne-miles cape_share
    if (tmData?.history?.length) {
      const reroutingSeries = chart.addSeries({
        type: 'Line',
        color: '#f59e0b',
        lineWidth: 1,
        lineStyle: 2,
        title: 'Cape Share %',
        priceScaleId: 'left',
        priceFormat: { type: 'percent' },
      })
      reroutingSeries.setData(
        tmData.history.map((d) => ({ time: d.date, value: (d.cape_share || 0) * 100 }))
      )
    }

    chart.timeScale().fitContent()

    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) chart.applyOptions({ width: entry.contentRect.width })
    })
    ro.observe(containerRef.current)

    chartRef.current = chart
    return () => {
      ro.disconnect()
      if (chartRef.current) chartRef.current.remove()
    }
  }, [data, tmData])

  if (!data?.available && !loading) return null

  const current = data?.current
  const div = current?.divergence

  return (
    <Panel
      id="freight-proxy"
      title="IMPLIED FREIGHT INDEX"
      info="Equal-weighted tanker equity index (FRO, STNG, DHT, INSW) as a proxy for freight rates. Compared against Cape rerouting share to detect financial-physical divergence."
      collapsible
      headerRight={
        current && (
          <span className="font-mono text-[10px] text-cyan-glow">
            {current.index?.toFixed(1)}
          </span>
        )
      }
    >
      {loading ? (
        <div className="px-4 py-4">
          <div className="w-1.5 h-1.5 rounded-full bg-cyan-glow/50 animate-pulse inline-block mr-2" />
          <span className="font-mono text-[10px] text-neutral-500 animate-pulse">Loading...</span>
        </div>
      ) : (
        <>
          {/* Divergence alert */}
          {div === 'FREIGHT_PROXY_DIVERGENCE' && (
            <div className="mx-4 mt-3 px-3 py-2 border border-orange-400/30 bg-orange-400/5 rounded">
              <span className="font-mono text-[10px] text-orange-400">
                {'\u26A0'} Financial markets diverging from physical signals — tanker equities falling despite elevated rerouting
              </span>
            </div>
          )}
          {div === 'FREIGHT_PROXY_LEADS' && (
            <div className="mx-4 mt-3 px-3 py-2 border border-cyan-glow/30 bg-cyan-glow/5 rounded">
              <span className="font-mono text-[10px] text-cyan-glow">
                Tanker equities rising ahead of physical signals — financial markets may be front-running a disruption
              </span>
            </div>
          )}

          {/* Stats */}
          {current && (
            <div className="px-4 py-3 flex flex-wrap gap-4">
              <div>
                <div className="font-mono text-[9px] text-neutral-600">INDEX</div>
                <span className="font-mono text-lg font-bold text-cyan-glow">
                  {current.index?.toFixed(1)}
                </span>
              </div>
              {current.brent_corr_30d != null && (
                <div>
                  <div className="font-mono text-[9px] text-neutral-600">BRENT CORR (30D)</div>
                  <span className="font-mono text-sm font-bold text-neutral-300">
                    r={current.brent_corr_30d?.toFixed(2)}
                  </span>
                </div>
              )}
              {current.rerouting_corr_30d != null && (
                <div>
                  <div className="font-mono text-[9px] text-neutral-600">REROUTING CORR</div>
                  <span className="font-mono text-sm font-bold text-neutral-300">
                    r={current.rerouting_corr_30d?.toFixed(2)}
                  </span>
                </div>
              )}
              {/* Component tickers */}
              {current.components && (
                <div className="flex gap-2 items-end">
                  {Object.entries(current.components).map(([ticker, pct]) =>
                    pct != null ? (
                      <span
                        key={ticker}
                        className={`font-mono text-[9px] ${pct >= 0 ? 'text-green-glow/70' : 'text-red-400/70'}`}
                      >
                        {ticker} {pct >= 0 ? '+' : ''}{pct.toFixed(1)}%
                      </span>
                    ) : null
                  )}
                </div>
              )}
            </div>
          )}

          {/* Chart */}
          {data?.history?.length > 1 && <div ref={containerRef} className="px-2 pb-2" />}
        </>
      )}
    </Panel>
  )
}
