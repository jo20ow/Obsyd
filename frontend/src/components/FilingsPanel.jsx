import { useEffect, useState } from 'react'
import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'

const API = '/api'

function fmtUsd(v) {
  if (v == null) return '—'
  const a = Math.abs(v)
  if (a >= 1e9) return `$${(v / 1e9).toFixed(2)}B`
  if (a >= 1e6) return `$${(v / 1e6).toFixed(1)}M`
  return `$${v.toLocaleString('en-US')}`
}

function FinTile({ label, m }) {
  return (
    <div className="border border-border/50 rounded px-3 py-2">
      <div className="font-mono text-[9px] text-neutral-600 uppercase tracking-wider">{label}</div>
      <div className="font-mono text-sm font-bold text-neutral-200">{fmtUsd(m?.value)}</div>
      <div className="font-mono text-[9px] text-neutral-600">{m?.fiscal_year ? `FY${m.fiscal_year}` : ''}</div>
    </div>
  )
}

export default function FilingsPanel() {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [selected, setSelected] = useState('')

  // Debounced company search over the security master.
  useEffect(() => {
    const q = query.trim()
    if (!q) return
    let alive = true
    const id = setTimeout(() => {
      fetch(`${API}/filings/search?q=${encodeURIComponent(q)}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => { if (alive && d) setResults(d.results || []) })
        .catch(() => {})
    }, 250)
    return () => { alive = false; clearTimeout(id) }
  }, [query])

  const company = useFetchWithError(`${API}/filings/company?ticker=${encodeURIComponent(selected)}`, { deps: [selected] })
  const fin = useFetchWithError(`${API}/filings/financials?ticker=${encodeURIComponent(selected)}`, { deps: [selected] })

  const shownResults = query.trim() ? results : []
  const c = selected && company.data?.available ? company.data : null
  const metrics = selected && fin.data?.available ? fin.data.metrics : null

  const pick = (ticker) => { setSelected(ticker); setQuery('') }

  return (
    <Panel
      id="filings"
      title="COMPANY FILINGS & FUNDAMENTALS"
      info="Search any US public company by ticker or name, then see its recent SEC filings (linked to sec.gov) and headline financials — from EDGAR, free & public-domain. This is the equities research layer (filings + fundamentals), not real-time quotes. Not investment advice."
      collapsible
    >
      <div className="px-4 py-3 relative">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search company — ticker or name (e.g. AAPL, Nvidia)…"
          className="w-full bg-transparent border border-border rounded px-3 py-2 font-mono text-sm text-neutral-200 placeholder:text-neutral-600 outline-none focus:border-cyan-glow/40"
        />
        {shownResults.length > 0 && (
          <div className="absolute left-4 right-4 z-20 mt-1 border border-border bg-[#0a0a12] rounded max-h-64 overflow-y-auto scrollbar-hidden shadow-xl shadow-black/50">
            {shownResults.map((r) => (
              <button
                key={r.cik + r.ticker}
                onClick={() => pick(r.ticker)}
                className="w-full flex items-center gap-3 px-3 py-1.5 text-left hover:bg-white/[0.04]"
              >
                <span className="font-mono text-[11px] font-bold text-cyan-glow w-16 shrink-0">{r.ticker}</span>
                <span className="font-mono text-[10px] text-neutral-400 truncate">{r.title}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {!selected && (
        <div className="px-4 pb-4 font-mono text-[11px] text-neutral-600">Search a company to see its filings and financials.</div>
      )}

      {c && (
        <div className="px-4 pb-4">
          <div className="flex items-baseline gap-2 mb-3">
            <span className="font-mono text-lg font-bold text-neutral-100">{c.ticker}</span>
            <span className="font-mono text-[11px] text-neutral-500 truncate">{c.name}</span>
            <span className="font-mono text-[9px] text-neutral-700">CIK {c.cik}</span>
          </div>

          {metrics && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-4">
              <FinTile label="Revenue" m={metrics.revenue} />
              <FinTile label="Net income" m={metrics.net_income} />
              <FinTile label="Total assets" m={metrics.total_assets} />
              <FinTile label="Equity" m={metrics.equity} />
            </div>
          )}

          <div className="font-mono text-[9px] text-neutral-600 uppercase tracking-wider mb-1">Recent filings</div>
          <div className="divide-y divide-border/40">
            {(c.filings ?? []).map((f) => (
              <a
                key={f.accession}
                href={f.url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-3 px-1 py-1.5 hover:bg-white/[0.02]"
              >
                <span className="font-mono text-[10px] font-bold text-cyan-glow w-16 shrink-0">{f.form}</span>
                <span className="font-mono text-[10px] text-neutral-400 w-24 shrink-0">{f.date}</span>
                <span className="font-mono text-[10px] text-neutral-600 truncate">{f.primary_doc || 'view on sec.gov ↗'}</span>
              </a>
            ))}
          </div>
          <div className="font-mono text-[9px] text-neutral-700 mt-2">Source: SEC EDGAR (public domain) · not investment advice</div>
        </div>
      )}
    </Panel>
  )
}
