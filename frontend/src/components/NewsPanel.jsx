import { useState } from 'react'
import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'

const API = '/api'

// Absolute timestamp (pure: reading the current clock in render is impure/flagged).
function fmtWhen(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d)) return ''
  return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export default function NewsPanel() {
  const topicsRes = useFetchWithError(`${API}/news/topics`)
  const [topic, setTopic] = useState('markets')
  const feed = useFetchWithError(`${API}/news/feed?topic=${topic}`, { deps: [topic] })

  const topics = topicsRes.data?.topics ?? []
  const articles = feed.data?.available ? (feed.data.data ?? []) : []

  return (
    <Panel
      id="news"
      title="NEWS · CROSS-ASSET"
      info="Recent English-language headlines per topic, aggregated from the free GDELT news index. Descriptive aggregation of public reporting — not our own reporting, and not investment advice."
      collapsible
      headerRight={feed.data?.available && <span className="font-mono text-[9px] text-neutral-600">GDELT</span>}
    >
      {/* Topic chips */}
      <div className="px-4 pt-3 flex flex-wrap gap-1.5">
        {topics.map((t) => (
          <button
            key={t.key}
            onClick={() => setTopic(t.key)}
            className={`font-mono text-[10px] px-2 py-0.5 rounded border ${
              t.key === topic
                ? 'text-cyan-glow border-cyan-glow/40 bg-cyan-glow/10'
                : 'text-neutral-500 border-border hover:text-neutral-300'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {feed.loading && !feed.data && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">Loading headlines…</div>
      )}

      {!feed.loading && !feed.data?.available && (
        <div className="px-4 py-4 font-mono text-[11px] text-neutral-500">No headlines right now — check back shortly.</div>
      )}

      {articles.length > 0 && (
        <div className="divide-y divide-border/40 mt-2">
          {articles.map((a) => (
            <a
              key={a.url}
              href={a.url}
              target="_blank"
              rel="noopener noreferrer"
              className="block px-4 py-2 hover:bg-white/[0.02]"
            >
              <div className="font-mono text-[12px] text-neutral-200 leading-snug">{a.title}</div>
              <div className="font-mono text-[9px] text-neutral-600 mt-0.5">
                {a.source}{a.published ? ` · ${fmtWhen(a.published)}` : ''}
              </div>
            </a>
          ))}
        </div>
      )}
      <div className="px-4 py-2 font-mono text-[9px] text-neutral-700">Source: GDELT · public news aggregation · not investment advice</div>
    </Panel>
  )
}
