import { useMemo, useState } from 'react'
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts'
import { InfoPopover } from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import { CHART_TOOLTIP_STYLE } from '../utils/chart'

const API = '/api'

const RANGES = [
  { key: '7d', label: '7D', days: 7 },
  { key: '30d', label: '30D', days: 30 },
  { key: '90d', label: '90D', days: 90 },
  { key: '1y', label: '1Y', days: 365 },
  { key: '5y', label: '5Y', days: 1826 },
]

function isoDaysAgo(n) {
  // Build a YYYY-MM-DD n days before today without Date.now gymnastics in render.
  const d = new Date()
  d.setUTCDate(d.getUTCDate() - n)
  return d.toISOString().slice(0, 10)
}

export default function SeriesExplorer() {
  const [series, setSeries] = useState('price.dayahead')
  const [zone, setZone] = useState('DE_LU')
  const [range, setRange] = useState('30d')
  const [resolution, setResolution] = useState('daily')

  const { data: catalog } = useFetchWithError(`${API}/v1/series/catalog`)

  const rangeDays = RANGES.find((r) => r.key === range)?.days ?? 30
  const start = useMemo(() => isoDaysAgo(rangeDays), [rangeDays])

  const url = `${API}/v1/series?series=${encodeURIComponent(series)}&zone=${zone}&start=${start}&resolution=${resolution}`
  const { data: resp, loading } = useFetchWithError(url, { deps: [series, zone, start, resolution] })

  const tkey = resolution === 'daily' ? 'date' : 'datetime_utc'
  const data = (resp?.data || []).map((p) => ({ t: p[tkey], value: p.value }))
  const csvUrl = `${API}/v1/series?series=${encodeURIComponent(series)}&zone=${zone}&start=${start}&resolution=${resolution}&format=csv`

  const seriesList = catalog?.series || [{ key: 'price.dayahead', unit: 'EUR/MWh' }]
  const zoneList = catalog?.zones || [{ key: 'DE_LU', label: 'DE-LU' }]
  const fmtT = (t) => (resolution === 'daily' ? t : String(t).slice(5, 16).replace('T', ' '))

  return (
    <div className="border border-border bg-surface rounded overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 border-b border-border">
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs text-neutral-500 tracking-wider">SERIES EXPLORER · /api/v1</span>
          <InfoPopover text="Query any series for any zone over the canonical hourly store via the public data API (GET /api/v1/series). Pick a series, zone, range and resolution; download the exact query as CSV. Free, official, redistributable data — descriptive, not a forecast." />
        </div>
        <a
          href={csvUrl}
          className="font-mono text-[10px] tracking-wider border border-border rounded px-2 py-1 text-neutral-400 hover:text-cyan-glow hover:border-cyan-glow/40 transition-colors"
        >
          ↓ CSV
        </a>
      </div>

      <div className="flex flex-wrap items-center gap-2 px-4 py-2.5 border-b border-border/50">
        <select value={series} onChange={(e) => setSeries(e.target.value)}
          className="font-mono text-[11px] bg-[#0a0a12] border border-border rounded px-2 py-1 text-neutral-300">
          {seriesList.map((s) => <option key={s.key} value={s.key}>{s.key}</option>)}
        </select>
        <select value={zone} onChange={(e) => setZone(e.target.value)}
          className="font-mono text-[11px] bg-[#0a0a12] border border-border rounded px-2 py-1 text-neutral-300">
          {zoneList.map((z) => <option key={z.key} value={z.key}>{z.label || z.key}</option>)}
        </select>
        <div className="flex items-center gap-1">
          {RANGES.map((r) => (
            <button key={r.key} onClick={() => setRange(r.key)}
              className={`font-mono text-[9px] px-2 py-0.5 rounded border ${range === r.key ? 'text-cyan-glow border-cyan-glow/40 bg-cyan-glow/10' : 'text-neutral-500 border-border hover:text-neutral-300'}`}>
              {r.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1">
          {['hourly', 'daily'].map((rz) => (
            <button key={rz} onClick={() => setResolution(rz)}
              className={`font-mono text-[9px] px-2 py-0.5 rounded border ${resolution === rz ? 'text-violet-300 border-violet-400/40 bg-violet-400/10' : 'text-neutral-500 border-border hover:text-neutral-300'}`}>
              {rz.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      <div className="px-2 pt-3 pb-1" style={{ minHeight: 240 }}>
        {loading && <div className="px-4 py-10 text-center font-mono text-[10px] text-neutral-600 animate-pulse">Loading…</div>}
        {!loading && resp && resp.available === false && (
          <div className="px-4 py-10 text-center font-mono text-[10px] text-neutral-600">
            {resp.reason || 'No data for this selection.'}
          </div>
        )}
        {!loading && data.length > 0 && (
          <>
            <div className="px-2 pb-2 font-mono text-[10px] text-neutral-500">
              {resp.count} points · {resp.unit || ''} · {resolution}
            </div>
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={data} margin={{ top: 5, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" />
                <XAxis dataKey="t" tickFormatter={fmtT} tick={{ fontSize: 8, fill: '#737373' }} minTickGap={40} />
                <YAxis tick={{ fontSize: 8, fill: '#737373' }} width={44} domain={['auto', 'auto']} />
                <Tooltip {...CHART_TOOLTIP_STYLE} labelFormatter={fmtT}
                  formatter={(v) => [v != null ? Number(v).toFixed(1) : '—', resp.unit || 'value']} />
                <Line type="monotone" dataKey="value" stroke="#22d3ee" dot={false} strokeWidth={1.4} />
              </LineChart>
            </ResponsiveContainer>
          </>
        )}
      </div>
    </div>
  )
}
