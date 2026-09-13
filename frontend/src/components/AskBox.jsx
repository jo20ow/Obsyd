import { useEffect, useState } from 'react'
import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid, Legend,
} from 'recharts'
import Panel from './Panel'
import MultiTip from './ChartTip'
import { useChartTheme } from '../utils/chart'

const API = '/api'

// Small fixed categorical set for zone comparison bars (most questions carry
// 1-2 zones; a whole country fans out to at most 7). Fixed assignment by
// position in the question, never re-painted on filtering.
const ZONE_COLORS = ['#1d4ed8', '#f59e0b', '#10b981', '#8b5cf6', '#ef4444', '#0ea5e9', '#a3a3a3']

/**
 * The question box — PREMIUM preview. A deterministic query answerer
 * (backend/power/ask.py): one metric, one or more zones, a time range; the
 * answer card echoes the interpretation, names coverage gaps, and links the
 * raw CSV. Renders NOTHING for non-pro sessions (mount probe → 401/403),
 * hidden means hidden.
 */
export default function AskBox() {
  const [tier, setTier] = useState('unknown') // unknown | pro | hidden
  const [examples, setExamples] = useState([])
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const ct = useChartTheme()

  useEffect(() => {
    let dead = false
    fetch(`${API}/v1/ask`, { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.status))))
      .then((d) => { if (!dead) { setTier('pro'); setExamples(d.examples || []) } })
      .catch(() => { if (!dead) setTier('hidden') })
    return () => { dead = true }
  }, [])

  if (tier !== 'pro') return null

  const submit = (query) => {
    const qq = (query ?? q).trim()
    if (!qq) return
    setBusy(true)
    fetch(`${API}/v1/ask?q=${encodeURIComponent(qq)}`, { credentials: 'include' })
      .then((r) => r.json())
      .then(setResult)
      .catch((e) => setResult({ available: false, message: String(e) }))
      .finally(() => setBusy(false))
  }

  const zones = result?.interpreted?.zones || []
  const labels = result?.zone_labels || {}

  return (
    <Panel
      id="ask"
      source="Deterministic parser + declared per-metric aggregation — no LLM, no guessing"
      title="ASK · ONE QUESTION, ONE ANSWER"
      info="Ask one metric for one or more zones over a time range — e.g. 'compare negative hours in Finland from 2019 to 2025'. Deterministic: the box either understands the question and shows exactly how it read it, or it fails visibly with suggestions. Counts are totalled per period, prices and levels averaged (each answer states its rule); years outside the record are named, never silently trimmed. Descriptive, not a forecast."
      collapsible
    >
      <div className="px-4 pt-3 pb-1">
        <form
          className="flex items-center gap-2"
          onSubmit={(e) => { e.preventDefault(); submit() }}
        >
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="compare negative hours in Finland from 2019 to 2025"
            className="flex-1 bg-surface border border-border rounded px-3 py-1.5 font-mono text-[12px] text-neutral-200 placeholder:text-neutral-600 focus:border-cyan-glow/40 outline-none"
          />
          <button
            type="submit"
            disabled={busy || !q.trim()}
            className="font-mono text-[10px] tracking-wider text-cyan-glow border border-cyan-glow/30 rounded px-3 py-1.5 disabled:opacity-40 hover:bg-cyan-glow/10"
          >
            {busy ? '…' : 'ASK'}
          </button>
        </form>
        {!result && examples.length > 0 && (
          <div className="pt-2 flex flex-wrap gap-1.5">
            {examples.map((ex) => (
              <button key={ex} onClick={() => { setQ(ex); submit(ex) }}
                className="font-mono text-[9px] text-neutral-500 border border-border rounded px-2 py-0.5 hover:text-neutral-300 hover:border-neutral-600">
                {ex}
              </button>
            ))}
          </div>
        )}
      </div>

      {result && !result.available && (
        <div className="px-4 py-3 space-y-2">
          <div className="font-mono text-[11px] text-amber-400">
            {result.message || 'Not understood.'}
          </div>
          {result.known_metrics && (
            <div className="font-mono text-[10px] text-neutral-500">
              Metrics I know: {result.known_metrics.join(' · ')}
            </div>
          )}
          {(result.examples || []).length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {result.examples.map((ex) => (
                <button key={ex} onClick={() => { setQ(ex); submit(ex) }}
                  className="font-mono text-[9px] text-neutral-500 border border-border rounded px-2 py-0.5 hover:text-neutral-300">
                  {ex}
                </button>
              ))}
            </div>
          )}
          {(result.coverage || []).map((c) => (
            <div key={c} className="font-mono text-[10px] text-amber-400/80">{c}</div>
          ))}
        </div>
      )}

      {result?.available && (
        <div className="px-4 py-2 space-y-2">
          {/* How the question was read — always visible, never implicit. */}
          <div className="flex flex-wrap gap-1.5 font-mono text-[9px]">
            {[
              result.interpreted.metric,
              zones.map((z) => labels[z] || z).join(' vs '),
              `${result.interpreted.from ?? 'record start'}–${result.interpreted.to}`,
              `${result.interpreted.grain} · ${result.interpreted.aggregation}`,
            ].map((chip) => (
              <span key={chip} className="px-1.5 py-0.5 rounded border border-cyan-glow/30 text-cyan-glow/90">
                {chip}
              </span>
            ))}
          </div>

          <div className="border-l-2 border-cyan-glow/50 pl-3 font-mono text-[12px] text-neutral-200">
            {result.sentence}
          </div>

          {result.rows?.length > 0 && (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={result.rows} margin={{ top: 5, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid {...ct.grid} />
                <XAxis dataKey="period" tick={ct.tick} />
                <YAxis tick={ct.tick} width={54} />
                <Tooltip content={<MultiTip />} formatter={(v, n) => [
                  `${Number(v).toLocaleString()} ${result.unit}`, labels[n] || n,
                ]} />
                {zones.length > 1 && <Legend wrapperStyle={{ fontSize: 9, fontFamily: 'monospace' }} iconSize={7} />}
                {zones.map((z, i) => (
                  <Bar key={z} dataKey={z} name={labels[z] || z}
                    fill={ZONE_COLORS[i % ZONE_COLORS.length]} fillOpacity={0.75}
                    isAnimationActive={false} />
                ))}
              </BarChart>
            </ResponsiveContainer>
          )}

          {(result.coverage || []).map((c) => (
            <div key={c} className="font-mono text-[10px] text-amber-400/80">{c}</div>
          ))}
          {result.partial_period && (
            <div className="font-mono text-[10px] text-neutral-500">
              {result.partial_period} is still running — a partial period, not a full one.
            </div>
          )}
          <div className="flex items-center gap-3 font-mono text-[9px] text-neutral-600">
            <span>{result.note}</span>
            {result.download_url && (
              <a href={result.download_url} className="text-cyan-glow/80 hover:underline shrink-0">
                ↓ CSV ({zones[0]})
              </a>
            )}
          </div>
        </div>
      )}
    </Panel>
  )
}
