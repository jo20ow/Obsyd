import { useState, useEffect } from 'react'
import { SkeletonCard } from './Skeleton'
import Panel from './Panel'

const API = '/api'

const KEYWORD_SHORT = {
  'oil supply disruption': 'DISRUPTION',
  'OPEC': 'OPEC',
  'Suez Canal': 'SUEZ',
  'Strait of Hormuz': 'HORMUZ',
  'refinery shutdown': 'REFINERY',
  'oil price': 'OIL PRICE',
  'LNG': 'LNG',
}

function ToneBar({ tone }) {
  const clamp = Math.max(-10, Math.min(10, tone))
  const pct = ((clamp + 10) / 20) * 100
  const color = tone >= 0 ? '#00ff9d' : '#ff5050'
  return (
    <div className="w-full h-1 bg-neutral-800 rounded-full mt-0.5">
      <div
        className="h-1 rounded-full transition-all"
        style={{ width: `${pct}%`, backgroundColor: color }}
      />
    </div>
  )
}

function VolumeBar({ value, max }) {
  const pct = max > 0 ? (value / max) * 100 : 0
  return (
    <div className="w-full h-1.5 bg-neutral-800 rounded-full">
      <div
        className="h-1.5 rounded-full bg-cyan-glow/70 transition-all"
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}

export default function SentimentPanel() {
  const [volumeData, setVolumeData] = useState(undefined)
  const [riskData, setRiskData] = useState(undefined)
  const [headlines, setHeadlines] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetch(`${API}/sentiment/volume`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setVolumeData)
      .catch((e) => { console.error('SentimentPanel volume fetch:', e); setError(e.message) })

    fetch(`${API}/sentiment/risk`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setRiskData)
      .catch((e) => { console.error('SentimentPanel risk fetch:', e); setError(e.message) })

    fetch(`${API}/sentiment/headlines`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setHeadlines(d?.articles || []))
      .catch((e) => console.error('Headlines fetch:', e))
  }, [])

  if (error) return (
    <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
      <div className="font-mono text-[10px] text-red-400">SENTIMENT // FETCH ERROR</div>
    </div>
  )

  if (volumeData === undefined || riskData === undefined) return <SkeletonCard lines={5} />

  const hasAI = riskData?.score != null
  const keywords = volumeData?.keywords || {}
  const hasVolume = Object.keys(keywords).length > 0
  const hasHeadlines = headlines && headlines.length > 0

  if (!hasVolume && !hasAI && !hasHeadlines) return null

  // Compute latest volume per keyword
  const kwStats = Object.entries(keywords).map(([kw, points]) => {
    const sorted = [...points].sort((a, b) => (a.timestamp > b.timestamp ? 1 : -1))
    const latest = sorted[sorted.length - 1] || {}
    const avgTone = sorted.reduce((s, p) => s + p.avg_tone, 0) / (sorted.length || 1)
    return { kw, volume: latest.volume || 0, tone: avgTone }
  })

  const maxVol = Math.max(...kwStats.map((k) => k.volume), 0.01)

  return (
    <Panel id="sentiment" title={`${hasAI ? 'AI SENTIMENT' : 'NEWS VOLUME'} // GDELT`} info="News sentiment from GDELT tone analysis. Risk score 1-10 (1=very negative, 10=very positive)." collapsible headerRight={hasAI && <span className="font-mono text-[9px] text-purple-400 border border-purple-400/30 rounded px-1.5 py-0.5">AI</span>}>
      <div className="px-4 py-3">

      {hasAI && riskData.score && (
        <div className="mb-3 pb-3 border-b border-border">
          <div className="flex items-center gap-3">
            <div
              className={`font-mono text-3xl font-bold ${
                riskData.score.risk_score >= 7
                  ? 'text-red-400'
                  : riskData.score.risk_score >= 4
                  ? 'text-yellow-400'
                  : 'text-green-glow'
              }`}
            >
              {riskData.score.risk_score}
            </div>
            <div>
              <div className="font-mono text-[10px] text-neutral-500">RISK SCORE</div>
              <div className="font-mono text-[9px] text-neutral-600">
                {riskData.score.date} via {riskData.score.source}
              </div>
            </div>
          </div>
          {riskData.score.risk_factors?.length > 0 && (
            <div className="mt-2 space-y-0.5">
              {riskData.score.risk_factors.map((f, i) => (
                <div key={i} className="font-mono text-[10px] text-neutral-500">
                  {i + 1}. {f}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {hasVolume && (
        <div className="space-y-1.5">
          {kwStats.map(({ kw, volume, tone }) => (
            <div key={kw}>
              <div className="flex items-center justify-between">
                <span className="font-mono text-[10px] text-neutral-400">
                  {KEYWORD_SHORT[kw] || kw.toUpperCase()}
                </span>
                <span
                  className={`font-mono text-[9px] ${
                    tone >= 0 ? 'text-green-glow' : 'text-red-400'
                  }`}
                >
                  {tone >= 0 ? '+' : ''}{tone.toFixed(1)}
                </span>
              </div>
              <VolumeBar value={volume} max={maxVol} />
              <ToneBar tone={tone} />
            </div>
          ))}
        </div>
      )}

      {/* Headlines */}
      {hasHeadlines && (
        <div className={hasVolume ? 'mt-3 pt-3 border-t border-border' : ''}>
          <div className="font-mono text-[10px] text-neutral-600 tracking-wider mb-2">
            HEADLINES
          </div>
          <div className="space-y-1.5 max-h-48 overflow-y-auto">
            {headlines.slice(0, 10).map((h, i) => (
              <a
                key={i}
                href={h.url}
                target="_blank"
                rel="noopener noreferrer"
                className="block group"
              >
                <div className="font-mono text-[11px] text-neutral-400 group-hover:text-cyan-glow transition-colors leading-tight">
                  {h.title}
                </div>
                <div className="font-mono text-[9px] text-neutral-600 mt-0.5">
                  {h.domain} · {h.date?.slice(0, 10) || ''}
                </div>
              </a>
            ))}
          </div>
        </div>
      )}
      </div>
    </Panel>
  )
}
