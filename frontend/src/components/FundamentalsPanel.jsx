import { useState, useEffect } from 'react'
import { SkeletonCard } from './Skeleton'

const API = '/api'

const SERIES_CONFIG = {
  'PET.WPULEUS3.W': { label: 'REFINERY UTIL', unit: '%', decimals: 1, warnBelow: 85 },
  'PET.WCRIMUS2.W': { label: 'CRUDE IMPORTS', unit: 'kbd', decimals: 0 },
  'PET.WCREXUS2.W': { label: 'CRUDE EXPORTS', unit: 'kbd', decimals: 0 },
  'PET.WCSSTUS1.W.SPR': { label: 'SPR', unit: 'Mb', decimals: 0 },
}

function UtilGauge({ value }) {
  const pct = Math.min(100, Math.max(0, value))
  const color =
    value >= 90 ? '#00ff9d' : value >= 85 ? '#00e5ff' : value >= 80 ? '#ffbb00' : '#ff5050'

  return (
    <div className="mt-1.5">
      <div className="w-full h-2 bg-neutral-800 rounded-full overflow-hidden">
        <div
          className="h-2 rounded-full transition-all"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
      <div className="flex justify-between mt-0.5">
        <span className="font-mono text-[8px] text-neutral-700">0%</span>
        <span className="font-mono text-[8px] text-neutral-700">100%</span>
      </div>
    </div>
  )
}

function NetFlowBar({ imports, exports }) {
  if (!imports || !exports) return null
  const net = imports - exports
  const total = imports + exports
  const importPct = total > 0 ? (imports / total) * 100 : 50

  return (
    <div className="mt-1.5">
      <div className="w-full h-2 bg-neutral-800 rounded-full overflow-hidden flex">
        <div
          className="h-2 bg-red-400/80 transition-all"
          style={{ width: `${importPct}%` }}
        />
        <div
          className="h-2 bg-green-glow/80 transition-all"
          style={{ width: `${100 - importPct}%` }}
        />
      </div>
      <div className="flex justify-between mt-0.5">
        <span className="font-mono text-[8px] text-red-400/60">IMP</span>
        <span
          className={`font-mono text-[8px] ${
            net > 0 ? 'text-red-400/60' : 'text-green-glow/60'
          }`}
        >
          NET: {net > 0 ? '+' : ''}{net.toFixed(0)} kbd
        </span>
        <span className="font-mono text-[8px] text-green-glow/60">EXP</span>
      </div>
    </div>
  )
}

export default function FundamentalsPanel() {
  const [data, setData] = useState(undefined)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetch(`${API}/prices/eia/fundamentals`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setData)
      .catch((e) => { console.error('FundamentalsPanel fetch:', e); setError(e.message) })
  }, [])

  if (error) return (
    <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
      <div className="font-mono text-[10px] text-red-400">FUNDAMENTALS // FETCH ERROR</div>
    </div>
  )

  if (data === undefined) return <SkeletonCard lines={4} />
  if (!data) return null

  const getLatest = (seriesId) => {
    const rows = data[seriesId] || []
    return rows.length > 0 ? rows[0] : null
  }

  const getPrev = (seriesId) => {
    const rows = data[seriesId] || []
    return rows.length > 1 ? rows[1] : null
  }

  const util = getLatest('PET.WPULEUS3.W')
  const imports = getLatest('PET.WCRIMUS2.W')
  const exports = getLatest('PET.WCREXUS2.W')
  const spr = getLatest('PET.WCSSTUS1.W.SPR')

  const hasData = util || imports || exports || spr
  if (!hasData) return null

  return (
    <div className="border border-border bg-surface rounded px-4 py-3">
      <div className="font-mono text-[10px] text-neutral-600 mb-2 tracking-wider">
        US OIL FUNDAMENTALS // EIA
      </div>

      <div className="grid grid-cols-2 gap-3">
        {/* Refinery Utilization */}
        {util && (
          <div className="border border-border bg-surface-light rounded px-3 py-2">
            <div className="flex items-center justify-between mb-0.5">
              <span className="font-mono text-[10px] text-neutral-500">REFINERY UTIL</span>
              <span className="font-mono text-[9px] text-neutral-600">{util.period}</span>
            </div>
            <div className="flex items-end gap-2">
              <span
                className={`font-mono text-lg font-bold ${
                  util.value < 80
                    ? 'text-red-400'
                    : util.value < 85
                    ? 'text-yellow-400'
                    : 'text-cyan-glow'
                }`}
              >
                {util.value.toFixed(1)}
                <span className="text-xs text-neutral-500 ml-0.5">%</span>
              </span>
              <WoWChange current={util} prev={getPrev('PET.WPULEUS3.W')} unit="pp" />
            </div>
            <UtilGauge value={util.value} />
          </div>
        )}

        {/* SPR */}
        {spr && (
          <div className="border border-border bg-surface-light rounded px-3 py-2">
            <div className="flex items-center justify-between mb-0.5">
              <span className="font-mono text-[10px] text-neutral-500">SPR</span>
              <span className="font-mono text-[9px] text-neutral-600">{spr.period}</span>
            </div>
            <div className="flex items-end gap-2">
              <span className="font-mono text-lg font-bold text-cyan-glow">
                {(spr.value / 1000).toFixed(1)}
                <span className="text-xs text-neutral-500 ml-0.5">Mb</span>
              </span>
              <WoWChange current={spr} prev={getPrev('PET.WCSSTUS1.W.SPR')} unit="Kb" abs />
            </div>
          </div>
        )}

        {/* Import/Export */}
        {(imports || exports) && (
          <div className="col-span-2 border border-border bg-surface-light rounded px-3 py-2">
            <div className="flex items-center justify-between mb-0.5">
              <span className="font-mono text-[10px] text-neutral-500">CRUDE TRADE BALANCE</span>
              <span className="font-mono text-[9px] text-neutral-600">
                {imports?.period || exports?.period}
              </span>
            </div>
            <div className="flex items-center justify-between">
              {imports && (
                <div>
                  <span className="font-mono text-[9px] text-neutral-600">IMP </span>
                  <span className="font-mono text-sm font-bold text-red-400">
                    {(imports.value / 1000).toFixed(1)}
                    <span className="text-xs text-neutral-500 ml-0.5">Mbd</span>
                  </span>
                </div>
              )}
              {exports && (
                <div>
                  <span className="font-mono text-[9px] text-neutral-600">EXP </span>
                  <span className="font-mono text-sm font-bold text-green-glow">
                    {(exports.value / 1000).toFixed(1)}
                    <span className="text-xs text-neutral-500 ml-0.5">Mbd</span>
                  </span>
                </div>
              )}
            </div>
            <NetFlowBar
              imports={imports?.value}
              exports={exports?.value}
            />
          </div>
        )}
      </div>
    </div>
  )
}

function WoWChange({ current, prev, unit, abs: absMode }) {
  if (!prev || prev.value == null || current.value == null) return null

  const diff = current.value - prev.value
  if (diff === 0) return null

  const display = absMode
    ? `${diff > 0 ? '+' : ''}${diff.toFixed(0)} ${unit}`
    : `${diff > 0 ? '+' : ''}${diff.toFixed(1)} ${unit}`

  return (
    <span
      className={`font-mono text-[10px] ${
        diff > 0 ? 'text-green-glow' : 'text-red-400'
      }`}
    >
      {display}
    </span>
  )
}
