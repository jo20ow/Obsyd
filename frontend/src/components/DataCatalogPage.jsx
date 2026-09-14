import { useMemo, useState } from 'react'
import DocShell, { SectionLabel } from './doc/DocShell'
import useFetchWithError from '../hooks/useFetchWithError'

const API = '/api'

/**
 * /data — the browsable dataset catalog (the provider survey's "dataset page
 * as a product surface", step 1). One row per series with its label, key,
 * unit and one-line description from the data-dictionary layer; each links to
 * its own /data/<key> page. Search covers label, key and description.
 */
export default function DataCatalogPage() {
  const { data, loading } = useFetchWithError(`${API}/v1/series/catalog`)
  const [q, setQ] = useState('')

  const groups = useMemo(() => {
    const series = data?.series || []
    const needle = q.trim().toLowerCase()
    const hit = (s) =>
      !needle ||
      s.key.toLowerCase().includes(needle) ||
      s.label.toLowerCase().includes(needle) ||
      (s.description || '').toLowerCase().includes(needle)
    const byGroup = new Map()
    for (const g of data?.groups || []) byGroup.set(g.key, { ...g, items: [] })
    for (const s of series) {
      if (!hit(s)) continue
      if (!byGroup.has(s.group)) byGroup.set(s.group, { key: s.group, label: s.group, items: [] })
      byGroup.get(s.group).items.push(s)
    }
    return [...byGroup.values()].filter((g) => g.items.length > 0)
  }, [data, q])

  return (
    <DocShell maxWidth="max-w-4xl">
      <div className="flex items-baseline justify-between gap-4 mb-2">
        <SectionLabel>DATA CATALOG</SectionLabel>
        <a href="/api/v1/series/catalog" className="font-mono text-[10px] text-neutral-600 hover:text-cyan-glow">
          JSON: /api/v1/series/catalog ↗
        </a>
      </div>
      <p className="text-[13px] text-neutral-400 leading-relaxed max-w-2xl mb-5">
        Every queryable series with its contract — what it is, where it comes from, how it
        arrives, and under which licence. {data?.series_count ? `${data.series_count} series` : ''}
        {data?.coverage?.from ? ` · record since ${String(data.coverage.from).slice(0, 10)}` : ''}.
      </p>
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="search series… (e.g. negative, co2, flow)"
        className="w-full max-w-md bg-surface border border-border rounded px-3 py-1.5 font-mono text-[12px] text-neutral-200 placeholder:text-neutral-600 focus:border-cyan-glow/40 outline-none mb-8"
      />

      {loading && !data && (
        <div className="font-mono text-[11px] text-neutral-500 animate-pulse">Loading catalog…</div>
      )}

      {groups.map((g) => (
        <section key={g.key} className="mb-8">
          <div className="smallcaps font-mono text-[10px] text-neutral-500 mb-2">{g.label}</div>
          <div className="border border-border bg-surface rounded divide-y divide-border/50">
            {g.items.map((s) => (
              <a
                key={s.key}
                href={`/data/${encodeURIComponent(s.key)}`}
                className="block px-4 py-2.5 hover:bg-white/[0.03] transition-colors"
              >
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
                  <span className="text-[13px] text-neutral-200">{s.label}</span>
                  <span className="font-mono text-[10px] text-cyan-glow/80">{s.key}</span>
                  <span className="font-mono text-[10px] text-neutral-600 ml-auto">{s.unit || ''}</span>
                </div>
                {s.description && (
                  <div className="text-[12px] text-neutral-500 leading-snug mt-0.5">{s.description}</div>
                )}
              </a>
            ))}
          </div>
        </section>
      ))}
    </DocShell>
  )
}
