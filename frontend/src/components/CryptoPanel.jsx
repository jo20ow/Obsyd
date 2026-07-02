import { useState } from 'react'
import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts'
import { fmtDate, CHART_TOOLTIP_STYLE } from '../utils/chart'

const API = '/api'

function fmtPrice(p) {
  if (p == null) return '—'
  if (p >= 100) return `$${p.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
  if (p >= 1) return `$${p.toFixed(2)}`
  return `$${p.toFixed(4)}`
}

export default function CryptoPanel() {
  const { data, loading, error } = useFetchWithError(`${API}/crypto/prices`)
  const [selected, setSelected] = useState('BTC')
  const hist = useFetchWithError(`${API}/crypto/history?symbol=${selected}&days=90`, { deps: [selected] })

  if (error) {
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">CRYPTO // FETCH ERROR</div>
      </div>
    )
  }

  const rows = data?.data ?? []
  const histRows = hist.data?.available ? (hist.data.data ?? []) : []

  return (
    <Panel
      id="crypto"
      title="CRYPTO · SPOT (USD)"
      info="Real-time crypto spot prices and 24h change for a curated basket, from CoinGecko's free public API. The one asset class whose real-time data is genuinely free & redistributable. Click a row to chart its 90-day history. Not investment advice."
      collapsible
      headerRight={data?.date && <span className="font-mono text-[9px] text-neutral-600">CoinGecko · {data.date}</span>}
    >
      {loading && !data && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">
          Loading crypto…
        </div>
      )}

      {!data?.available && !loading && (
        <div className="px-4 py-4 font-mono text-[11px] text-neutral-500">No crypto data yet — check back shortly.</div>
      )}

      {data?.available && (
        <>
          {/* Selected-asset history chart */}
          {histRows.length > 1 && (
            <div className="px-2 pt-2">
              <div className="font-mono text-[9px] text-neutral-600 px-2 mb-1">{selected} · 90d</div>
              <ResponsiveContainer width="100%" height={90}>
                <AreaChart data={histRows} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e1e2e" />
                  <XAxis dataKey="date" tick={{ fontSize: 8, fill: '#555', fontFamily: 'monospace' }}
                    tickFormatter={fmtDate} interval="preserveStartEnd" minTickGap={50} />
                  <YAxis tick={{ fontSize: 8, fill: '#55556688', fontFamily: 'monospace' }} width={44}
                    tickFormatter={(v) => fmtPrice(v)} domain={['auto', 'auto']} />
                  <Tooltip contentStyle={CHART_TOOLTIP_STYLE}
                    formatter={(v) => [fmtPrice(Number(v)), selected]} labelFormatter={fmtDate} />
                  <Area type="monotone" dataKey="price" stroke="#22d3ee" fill="#22d3ee"
                    fillOpacity={0.06} strokeWidth={1.5} dot={false} activeDot={{ r: 3, fill: '#22d3ee' }} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Basket table */}
          <div className="divide-y divide-border/40">
            {rows.map((r) => {
              const chg = r.change_24h_pct
              const chgColor = chg == null ? 'text-neutral-500' : chg >= 0 ? 'text-green-glow' : 'text-red-400'
              const active = r.symbol === selected
              return (
                <button
                  key={r.symbol}
                  onClick={() => setSelected(r.symbol)}
                  className={`w-full flex items-center justify-between gap-3 px-4 py-2 text-left ${active ? 'bg-cyan-glow/[0.06]' : 'hover:bg-white/[0.02]'}`}
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <span className={`font-mono text-[11px] font-bold ${active ? 'text-cyan-glow' : 'text-neutral-200'}`}>{r.symbol}</span>
                    <span className="font-mono text-[9px] text-neutral-600 truncate hidden sm:inline">{r.name}</span>
                  </div>
                  <div className="flex items-center gap-3 shrink-0">
                    <span className="font-mono text-[11px] text-neutral-200">{fmtPrice(r.price_usd)}</span>
                    <span className={`font-mono text-[10px] font-bold w-16 text-right ${chgColor}`}>
                      {chg == null ? '—' : `${chg >= 0 ? '+' : ''}${chg.toFixed(1)}%`}
                    </span>
                  </div>
                </button>
              )
            })}
          </div>
          <div className="px-4 py-1.5 font-mono text-[9px] text-neutral-700">
            Source: CoinGecko (free API) · not investment advice
          </div>
        </>
      )}
    </Panel>
  )
}
