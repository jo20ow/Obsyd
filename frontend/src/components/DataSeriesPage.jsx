import { useMemo, useState } from 'react'
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts'
import DocShell, { CodeBlock, LinkButton, SectionLabel } from './doc/DocShell'
import useFetchWithError from '../hooks/useFetchWithError'
import { CHART_TOOLTIP_PROPS, useChartTheme } from '../utils/chart'

const API = '/api'

function SpecCell({ label, children }) {
  return (
    <div className="bg-surface p-4">
      <div className="smallcaps text-[11px] text-neutral-500 mb-1">{label}</div>
      <div className="text-[12px] text-neutral-200 break-words leading-relaxed">{children}</div>
    </div>
  )
}

/**
 * /data/<series> — one page per series: the Ember/gridstatus "dataset page"
 * pattern. Contract (description/source/cadence/licence/caveat from the
 * catalog's data-dictionary layer), per-zone coverage, a live preview, and
 * every exit ramp: CSV/JSON/Parquet links, copy-as-curl/Python, open in the
 * chart builder. Premium series never reach this page for free sessions —
 * the catalog omits them, so the page honestly says "unknown series".
 */
export default function DataSeriesPage({ seriesKey }) {
  const ct = useChartTheme()
  const { data: catalog, loading } = useFetchWithError(`${API}/v1/series/catalog`)
  const [copied, setCopied] = useState(null)

  const entry = useMemo(
    () => (catalog?.series || []).find((s) => s.key === seriesKey),
    [catalog, seriesKey],
  )
  const coverage = useMemo(
    () => (catalog?.coverage_by_series || [])
      .filter((r) => r.series === seriesKey)
      .sort((a, b) => String(a.from).localeCompare(String(b.from))),
    [catalog, seriesKey],
  )
  const previewZone = useMemo(() => {
    const zones = coverage.map((r) => r.zone)
    return zones.includes('DE_LU') ? 'DE_LU' : zones[0]
  }, [coverage])

  const previewUrl = previewZone
    ? `${API}/v1/series?series=${seriesKey}&zone=${previewZone}&resolution=daily&start=${new Date(Date.now() - 180 * 86400e3).toISOString().slice(0, 10)}`
    : null
  const { data: preview } = useFetchWithError(previewUrl, { deps: [previewUrl] })
  const chart = (preview?.data || []).map((p) => ({ x: p.date, v: p.value }))

  const zoneForSnippets = previewZone || 'DE_LU'
  const base = `https://obsyd.dev/api/v1/series?series=${seriesKey}&zone=${zoneForSnippets}`
  const curlSnippet = `curl "${base}&start=2024-01-01&format=csv" -o ${seriesKey.replace(/\./g, '_')}_${zoneForSnippets}.csv`
  const pySnippet = `from obsyd import Obsyd\ndf = Obsyd().series("${seriesKey}", "${zoneForSnippets}", start="2024-01-01")`

  const copy = (label, text) => {
    navigator.clipboard?.writeText(text)
    setCopied(label)
    setTimeout(() => setCopied(null), 1500)
  }

  if (!loading && catalog && !entry) {
    return (
      <DocShell maxWidth="max-w-2xl">
        <SectionLabel className="mb-4">DATA</SectionLabel>
        <div className="bg-surface border border-border rounded p-6">
          <p className="text-[13px] text-neutral-400 mb-4">
            No series named <code className="font-mono text-[12px] text-neutral-200">{seriesKey}</code>{' '}
            in the catalog.
          </p>
          <LinkButton href="/data" primary>Browse the catalog →</LinkButton>
        </div>
      </DocShell>
    )
  }

  return (
    <DocShell maxWidth="max-w-4xl">
      <div className="flex items-baseline justify-between gap-4 mb-2">
        <SectionLabel>
          <a href="/data" className="hover:text-cyan-glow">DATA</a> · {entry?.group?.toUpperCase() || '…'}
        </SectionLabel>
        <span className="font-mono text-[10px] text-neutral-600">{entry?.unit}</span>
      </div>

      <h1 className="font-display text-2xl sm:text-3xl font-semibold text-neutral-100 mb-1">
        {entry?.label || seriesKey}
      </h1>
      <button
        onClick={() => copy('key', seriesKey)}
        title="Copy series key"
        className="font-mono text-[12px] text-cyan-glow/90 hover:text-cyan-glow mb-4"
      >
        {seriesKey} {copied === 'key' ? '✓' : '⧉'}
      </button>

      {entry?.description && (
        <p className="text-[14px] text-neutral-300 leading-relaxed max-w-2xl mb-6">
          {entry.description}
        </p>
      )}

      {entry?.caveat && (
        <div className="border-l-2 border-amber-400/60 bg-surface rounded-r px-4 py-3 mb-6 max-w-2xl">
          <div className="smallcaps font-mono text-[10px] text-amber-400 mb-1">KNOW BEFORE YOU USE IT</div>
          <div className="text-[12px] text-neutral-400 leading-relaxed">{entry.caveat}</div>
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-px bg-border border border-border rounded overflow-hidden mb-8">
        <SpecCell label="SOURCE">{entry?.source || '…'}</SpecCell>
        <SpecCell label="CADENCE">{entry?.cadence || '…'}</SpecCell>
        <SpecCell label="LICENCE">{entry?.licence || '…'}</SpecCell>
      </div>

      {chart.length > 1 && (
        <section className="mb-8">
          <SectionLabel className="mb-3">PREVIEW · {previewZone} · DAILY · LAST 180D</SectionLabel>
          <div className="bg-surface border border-border rounded p-2">
            <ResponsiveContainer width="100%" height={180}>
              <AreaChart data={chart} margin={{ top: 5, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid {...ct.grid} />
                <XAxis dataKey="x" tick={ct.tick} minTickGap={60} />
                <YAxis tick={ct.tick} width={54} />
                <Tooltip {...CHART_TOOLTIP_PROPS}
                  formatter={(v) => [`${Number(v).toLocaleString()} ${entry?.unit || ''}`, entry?.label]} />
                <Area type="monotone" dataKey="v" stroke={ct.accent} fill={ct.accent}
                  fillOpacity={0.08} strokeWidth={1.4} dot={false} isAnimationActive={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </section>
      )}

      <section className="mb-8">
        <SectionLabel className="mb-3">GET THE DATA</SectionLabel>
        <div className="flex flex-wrap gap-2 mb-4">
          <LinkButton href={`${base}&start=2024-01-01&format=csv`} primary>↓ CSV</LinkButton>
          <LinkButton href={`${base}&start=2024-01-01`}>JSON</LinkButton>
          <LinkButton href={`${base}&start=2024-01-01&format=parquet`}>Parquet</LinkButton>
          {previewZone && (
            <LinkButton href={`/builder?rows=${encodeURIComponent(`${seriesKey}:${previewZone}`)}&range=1y`}>
              Open in chart builder →
            </LinkButton>
          )}
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div>
            <CodeBlock title={copied === 'curl' ? 'CURL — COPIED ✓' : 'CURL'}>{curlSnippet}</CodeBlock>
            <button onClick={() => copy('curl', curlSnippet)}
              className="mt-1 font-mono text-[10px] text-neutral-500 hover:text-cyan-glow">⧉ copy</button>
          </div>
          <div>
            <CodeBlock title={copied === 'py' ? 'PYTHON — COPIED ✓' : 'PYTHON'}>{pySnippet}</CodeBlock>
            <button onClick={() => copy('py', pySnippet)}
              className="mt-1 font-mono text-[10px] text-neutral-500 hover:text-cyan-glow">⧉ copy</button>
          </div>
        </div>
      </section>

      {coverage.length > 0 && (
        <section className="mb-8">
          <SectionLabel className="mb-3">COVERAGE · {coverage.length} ZONES</SectionLabel>
          <div className="bg-surface border border-border rounded overflow-x-auto">
            <table className="w-full font-mono text-[11px]">
              <thead>
                <tr className="text-[9px] text-neutral-600 uppercase tracking-wider border-b border-border/60">
                  <th className="text-left px-3 py-2">Zone</th>
                  <th className="text-left px-3 py-2">From</th>
                  <th className="text-left px-3 py-2">To</th>
                </tr>
              </thead>
              <tbody>
                {coverage.map((r) => (
                  <tr key={r.zone} className="border-b border-border/30">
                    <td className="px-3 py-1.5 text-neutral-300">{r.zone}</td>
                    <td className="px-3 py-1.5 text-neutral-500">{String(r.from).slice(0, 10)}</td>
                    <td className="px-3 py-1.5 text-neutral-500">{String(r.to).slice(0, 10)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-[11px] text-neutral-600">
            Coverage windows refresh hourly; freshness lives on <a href="/status" className="text-cyan-glow hover:underline">/status</a>,
            completeness and the source&apos;s restatements on the desk&apos;s EXPLORE tab.
          </p>
        </section>
      )}
    </DocShell>
  )
}
