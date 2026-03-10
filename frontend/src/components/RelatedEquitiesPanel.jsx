import { useState, useEffect, useMemo } from 'react'
import Panel from './Panel'

const API = '/api'

const INFO_TEXT =
  'Energy equities with 30-day WTI and 90-day Brent correlation. ' +
  'Grouped by sector: Majors, E&P, Services, Tanker, LNG. ' +
  'Sorted by WTI correlation by default. ' +
  'Correlation: not causation. Not investment advice.'

const SECTOR_ORDER = ['Majors', 'E&P', 'Services', 'Tanker', 'LNG']
const SECTOR_COLORS = {
  Majors: 'text-cyan-glow',
  'E&P': 'text-emerald-400',
  Services: 'text-violet-400',
  Tanker: 'text-amber-400',
  LNG: 'text-rose-400',
}

function corrColor(val) {
  if (val == null) return 'text-neutral-600'
  if (val >= 0.7) return 'text-emerald-400 font-bold'
  if (val >= 0.4) return 'text-emerald-400/70'
  if (val >= 0) return 'text-neutral-400'
  if (val >= -0.4) return 'text-red-400/70'
  return 'text-red-400 font-bold'
}

function fmtCap(val) {
  if (val == null) return '---'
  if (val >= 1e12) return `$${(val / 1e12).toFixed(1)}T`
  if (val >= 1e9) return `$${(val / 1e9).toFixed(0)}B`
  if (val >= 1e6) return `$${(val / 1e6).toFixed(0)}M`
  return `$${val.toLocaleString()}`
}

export default function RelatedEquitiesPanel() {
  const [data, setData] = useState(null)
  const [sortKey, setSortKey] = useState('wti_corr_30d')
  const [sortAsc, setSortAsc] = useState(false)
  const [groupMode, setGroupMode] = useState(true)

  useEffect(() => {
    fetch(`${API}/signals/equities`, { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (d) setData(d)
      })
      .catch(() => {})
  }, [])

  const equities = data?.equities || []

  const sorted = useMemo(() => {
    if (!equities.length) return []
    const copy = [...equities]
    copy.sort((a, b) => {
      const av = a[sortKey] ?? -999
      const bv = b[sortKey] ?? -999
      return sortAsc ? av - bv : bv - av
    })
    return copy
  }, [equities, sortKey, sortAsc])

  const grouped = useMemo(() => {
    if (!groupMode) return { All: sorted }
    const groups = {}
    for (const s of SECTOR_ORDER) {
      const items = sorted.filter((e) => e.sector === s)
      if (items.length) groups[s] = items
    }
    return groups
  }, [sorted, groupMode])

  function handleSort(key) {
    if (sortKey === key) {
      setSortAsc(!sortAsc)
    } else {
      setSortKey(key)
      setSortAsc(false)
    }
  }

  const sortArrow = (key) => (sortKey === key ? (sortAsc ? ' \u25B4' : ' \u25BE') : '')

  if (!data) return null
  if (equities.length === 0) return null

  const headerRight = (
    <button
      onClick={() => setGroupMode(!groupMode)}
      className={`px-1.5 py-0.5 text-[9px] font-mono rounded transition-colors ${
        groupMode ? 'bg-cyan-glow/20 text-cyan-glow' : 'text-neutral-600 hover:text-neutral-400'
      }`}
    >
      {groupMode ? 'GROUPED' : 'FLAT'}
    </button>
  )

  return (
    <Panel id="related-equities" title="RELATED EQUITIES" info={INFO_TEXT} collapsible headerRight={headerRight}>
      <div className="px-4 py-3 font-mono text-xs">
        {/* Table header */}
        <div className="grid grid-cols-[60px_1fr_70px_60px_60px_60px_70px] gap-1 text-[9px] text-neutral-600 tracking-wider mb-2 border-b border-border pb-1">
          <button className="text-left hover:text-neutral-400" onClick={() => handleSort('ticker')}>
            TICKER{sortArrow('ticker')}
          </button>
          <span>NAME</span>
          <button className="text-right hover:text-neutral-400" onClick={() => handleSort('price')}>
            PRICE{sortArrow('price')}
          </button>
          <button className="text-right hover:text-neutral-400" onClick={() => handleSort('change_pct')}>
            CHG%{sortArrow('change_pct')}
          </button>
          <button className="text-right hover:text-neutral-400" onClick={() => handleSort('wti_corr_30d')}>
            WTI{sortArrow('wti_corr_30d')}
          </button>
          <button className="text-right hover:text-neutral-400" onClick={() => handleSort('brent_corr_90d')}>
            BRENT{sortArrow('brent_corr_90d')}
          </button>
          <button className="text-right hover:text-neutral-400" onClick={() => handleSort('market_cap')}>
            MCAP{sortArrow('market_cap')}
          </button>
        </div>

        {/* Rows grouped by sector */}
        {Object.entries(grouped).map(([sector, items]) => (
          <div key={sector} className="mb-2">
            {groupMode && (
              <div className={`text-[9px] tracking-wider mb-1 ${SECTOR_COLORS[sector] || 'text-neutral-500'}`}>
                {sector.toUpperCase()}
              </div>
            )}
            {items.map((eq) => (
              <div
                key={eq.ticker}
                className="grid grid-cols-[60px_1fr_70px_60px_60px_60px_70px] gap-1 py-0.5 hover:bg-white/[0.02] transition-colors items-center"
              >
                <span className={`font-bold ${SECTOR_COLORS[eq.sector] || 'text-cyan-glow'}`}>{eq.ticker}</span>
                <span className="text-neutral-500 truncate text-[10px]">{eq.name}</span>
                <span className="text-right text-neutral-300">${eq.price?.toFixed(2) ?? '---'}</span>
                <span className={`text-right font-semibold ${(eq.change_pct ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                  {eq.change_pct != null ? `${eq.change_pct >= 0 ? '+' : ''}${eq.change_pct.toFixed(1)}%` : '---'}
                </span>
                <span className={`text-right ${corrColor(eq.wti_corr_30d)}`}>
                  {eq.wti_corr_30d != null ? eq.wti_corr_30d.toFixed(2) : '---'}
                </span>
                <span className={`text-right ${corrColor(eq.brent_corr_90d)}`}>
                  {eq.brent_corr_90d != null ? eq.brent_corr_90d.toFixed(2) : '---'}
                </span>
                <span className="text-right text-neutral-500 text-[10px]">{fmtCap(eq.market_cap)}</span>
              </div>
            ))}
          </div>
        ))}

        <div className="text-[9px] text-neutral-700 border-t border-border pt-2 mt-1">
          {data.date && <span>Data: {data.date} &middot; </span>}
          {equities.length} equities &middot; WTI corr: 30d returns &middot; Brent corr: 90d returns &middot; Not investment advice.
        </div>
      </div>
    </Panel>
  )
}
