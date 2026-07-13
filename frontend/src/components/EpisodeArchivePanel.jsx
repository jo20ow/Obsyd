import { useState } from 'react'
import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import { POLL_SLOW_MS } from '../utils/poll'

const API = '/api'

const KINDS = [
  { key: 'dunkelflaute', label: 'DUNKELFLAUTE', unit: 'share' },
  { key: 'negative_prices', label: 'NEGATIVE PRICES', unit: 'hours' },
  { key: 'price_spike', label: 'PRICE SPIKES', unit: 'eur' },
]

function fmtDepth(kind, value) {
  if (value == null) return '—'
  if (kind === 'dunkelflaute') return `${(value * 100).toFixed(1)}%`
  if (kind === 'negative_prices') return `${value.toFixed(0)}h`
  return `€${value.toFixed(0)}`
}

/**
 * Grid stress as episodes — runs of days, ranked against the zone's own record.
 *
 * The radar only ever saw today: "DE-LU is in a Dunkelflaute". It could never say "and this is
 * the second-longest in five years", which is the sentence that decides whether to care.
 */
export default function EpisodeArchivePanel({ zone = 'DE_LU' }) {
  const [kind, setKind] = useState('dunkelflaute')
  const { data, loading, error } = useFetchWithError(
    `${API}/power/episodes?zone=${zone}&kind=${kind}`,
    { deps: [zone, kind], pollMs: POLL_SLOW_MS },
  )

  if (error) {
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">EPISODES // FETCH ERROR</div>
      </div>
    )
  }

  const active = data?.active
  const rank = data?.rank
  const episodes = data?.episodes ?? []
  const current = KINDS.find((k) => k.key === kind)

  return (
    <Panel
      id="power-episodes"
      title={`EPISODE ARCHIVE · ${zone}`}
      info={data?.note || 'Runs of consecutive qualifying days, re-derived nightly from the published record.'}
      collapsible
      headerRight={
        <div className="flex items-center gap-1">
          {KINDS.map((k) => (
            <button
              key={k.key}
              onClick={() => setKind(k.key)}
              className={`font-mono text-[9px] px-1.5 py-0.5 rounded border ${
                kind === k.key
                  ? 'border-cyan-glow/40 text-cyan-glow'
                  : 'border-border text-neutral-600 hover:text-neutral-400'
              }`}
            >
              {k.label}
            </button>
          ))}
        </div>
      }
    >
      {loading && !data ? (
        <div className="px-4 py-4 font-mono text-[10px] text-neutral-600 animate-pulse">Loading episodes…</div>
      ) : !data?.available ? (
        <div className="px-4 py-3 font-mono text-[10px] text-neutral-500">
          {data?.reason || 'No episodes on record.'}
        </div>
      ) : (
        <>
          {/* The running episode, with the only thing that makes it worth reading: its rank. */}
          {active && (
            <div className="px-4 py-3 border-b border-border/40">
              <div className="font-mono text-[11px] text-neutral-200">
                Running {active.duration_days} days
                <span className="text-neutral-600"> · {active.start_date} → {active.end_date}</span>
              </div>
              <div className="font-mono text-[10px] mt-1">
                {rank?.position != null ? (
                  <span className={rank.position === 1 ? 'text-amber-400' : 'text-cyan-glow'}>
                    {rank.position === 1 ? 'Longest' : `${rank.position}${['th', 'st', 'nd', 'rd'][rank.position % 10] || 'th'}-longest`}
                    {' '}of {rank.of} on record
                    <span className="text-neutral-600">
                      {' '}(longest: {rank.longest_days} days, from {rank.longest_start})
                    </span>
                  </span>
                ) : (
                  <span className="text-neutral-600">{rank?.reason}</span>
                )}
              </div>
            </div>
          )}

          <div className="px-2 py-2 overflow-x-auto">
            <table className="w-full font-mono text-[11px]">
              <thead>
                <tr className="text-[9px] text-neutral-600 uppercase tracking-wider">
                  <th className="text-left px-2 py-1">Episode</th>
                  <th className="text-right px-2 py-1">Days</th>
                  <th className="text-right px-2 py-1" title="The worst value reached, and the day it was reached">Deepest</th>
                  <th className="text-left px-2 py-1">on</th>
                </tr>
              </thead>
              <tbody>
                {episodes.map((e) => (
                  <tr key={e.start_date} className="border-t border-border/30">
                    <td className="px-2 py-1.5 text-neutral-300">
                      {e.start_date} → {e.end_date}
                      {e.status === 'active' && (
                        <span className="ml-1.5 text-[9px] text-cyan-glow">RUNNING</span>
                      )}
                    </td>
                    <td className="px-2 py-1.5 text-right text-neutral-200">{e.duration_days}</td>
                    <td className="px-2 py-1.5 text-right text-amber-400">
                      {fmtDepth(kind, e.depth)}
                    </td>
                    {/* The evidence pointer — records.py's discipline: an extreme without a date
                        to go and look at is an assertion. */}
                    <td className="px-2 py-1.5 text-neutral-600">{e.depth_date}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="px-4 pb-2 font-mono text-[9px] text-neutral-700 leading-relaxed">
            {data.count} {current?.label.toLowerCase()} episode{data.count === 1 ? '' : 's'} in{' '}
            {Math.floor((data.history_days || 0) / 365)} years of {zone} data. An episode is a run
            of consecutive qualifying days, re-derived nightly from the published record — not an
            alert log. &ldquo;Running&rdquo; means it reaches the newest day we hold, not that it
            continues.
          </div>
        </>
      )}
    </Panel>
  )
}
