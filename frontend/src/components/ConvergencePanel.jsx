import { useState } from 'react'
import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'

const API = '/api'

// ACER MMR band colors: full = coupled (calm green), moderate = amber,
// low = orange. Status palette, matching BordersPanel's convergenceColor.
const BAND_BG = {
  full: 'bg-emerald-500/60',
  moderate: 'bg-amber-500/50',
  low: 'bg-orange-500/60',
}

function ConvergenceLegend() {
  const rows = [
    ['Full', '|spread| ≤ €1/MWh — the two zones cleared as one market that hour.'],
    ['Moderate', '€1 < |spread| ≤ €10/MWh.'],
    ['Low', '|spread| > €10/MWh — the auction split the zones decisively.'],
    ['Ø spread', 'mean absolute day-ahead spread over the whole window (€/MWh).'],
    ['no SDAC', 'border outside EU market coupling (Switzerland): it converges rarely by market design, not congestion. Excluded from the headline.'],
  ]
  return (
    <div className="space-y-2">
      <div className="text-neutral-400 leading-snug">
        Share of hours per ACER's price-convergence bands (Market Monitoring Report
        2024), computed from hourly day-ahead auction prices per border.
      </div>
      <dl className="space-y-1.5">
        {rows.map(([term, def], i) => (
          <div key={i} className="grid grid-cols-[64px_1fr] gap-x-2">
            <dt className="text-cyan-glow/90">{term}</dt>
            <dd className="text-neutral-400 leading-snug">{def}</dd>
          </div>
        ))}
      </dl>
      <div className="pt-1 border-t border-border/40 text-neutral-500">
        ACER's own caveat: reaching full price convergence is not an objective — it
        would require overinvestment in network infrastructure. 100% is not the
        target. Descriptive statistics on published auction prices.
      </div>
    </div>
  )
}

function BandBar({ b }) {
  return (
    <div className="flex h-2 w-full rounded-sm overflow-hidden bg-white/[0.04]"
      title={`full ${b.full_pct}% · moderate ${b.moderate_pct}% · low ${b.low_pct}%`}>
      <div className={BAND_BG.full} style={{ width: `${b.full_pct}%` }} />
      <div className={BAND_BG.moderate} style={{ width: `${b.moderate_pct}%` }} />
      <div className={BAND_BG.low} style={{ width: `${b.low_pct}%` }} />
    </div>
  )
}

/**
 * ACER-band price convergence per border over a long window — the structural
 * companion to BordersPanel's 30-day operational view. Reads the stored daily
 * conv.* series (backend/power/convergence.py), so the panel and the CSV
 * export can never disagree.
 */
export default function ConvergencePanel() {
  const [days, setDays] = useState(365)
  const url = `${API}/power/convergence?days=${days}`
  const { data, loading, error } = useFetchWithError(url, { deps: [days] })

  // Premium preview: 401/403 = not this session's tier. Hidden means hidden —
  // no teaser, no locked-panel chrome, nothing rendered at all.
  if (error && /HTTP (401|403)/.test(error)) return null
  if (error && !data) {
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">CONVERGENCE // FETCH ERROR</div>
      </div>
    )
  }
  if (!loading && !data?.available) {
    return (
      <div className="border border-border bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-neutral-500">
          CONVERGENCE — {data?.reason || 'no data yet.'}
        </div>
      </div>
    )
  }

  const borders = data?.borders ?? []
  const sdac = borders.filter((b) => b.sdac)
  const nonSdac = borders.filter((b) => !b.sdac)
  const overall = data?.overall_sdac

  return (
    <Panel
      source="ENTSO-E 12.1.D day-ahead prices · bands: ACER MMR 2024"
      id="power-convergence"
      title="PRICE CONVERGENCE · ACER BANDS"
      info={<ConvergenceLegend />}
      infoWide
      collapsible
      downloadUrl={url}
      headerRight={
        <span className="flex items-center gap-2 font-mono text-[9px] text-neutral-600">
          {[90, 365].map((d) => (
            <button key={d} onClick={(e) => { e.stopPropagation(); setDays(d) }}
              className={`px-1.5 py-0.5 rounded border ${days === d ? 'text-cyan-glow border-cyan-glow/40 bg-cyan-glow/10' : 'text-neutral-500 border-border hover:text-neutral-300'}`}>
              {d}d
            </button>
          ))}
        </span>
      }
    >
      {loading && !data ? (
        <div className="px-4 py-4 font-mono text-[10px] text-neutral-600 animate-pulse">Loading convergence…</div>
      ) : (
        <>
          {overall && (
            <div className="px-4 pt-3 font-mono text-[11px] text-neutral-300">
              EU (SDAC borders): fully converged in{' '}
              <span className="text-emerald-400">{overall.full_pct}%</span> of border-hours
              <span className="text-neutral-600"> · moderate {overall.moderate_pct}% · low {overall.low_pct}%</span>
            </div>
          )}
          <div className="px-2 py-2 overflow-x-auto">
            <table className="w-full font-mono text-[11px]">
              <thead>
                <tr className="text-[9px] text-neutral-600 uppercase tracking-wider">
                  <th className="text-left px-2 py-1">Border</th>
                  <th className="text-left px-2 py-1 w-[38%]" title="Share of hours per ACER band: full ≤€1 · moderate €1–10 · low >€10">Bands</th>
                  <th className="text-right px-2 py-1" title="Share of hours with |spread| ≤ €1/MWh (ACER full convergence)">Full</th>
                  <th className="text-right px-2 py-1" title="Mean absolute day-ahead spread over the window">Ø spread</th>
                </tr>
              </thead>
              <tbody>
                {sdac.map((b) => (
                  <tr key={`${b.zone_a}|${b.zone_b}`} className="border-t border-border/30">
                    <td className="px-2 py-1.5 text-neutral-300 whitespace-nowrap">{b.label}</td>
                    <td className="px-2 py-1.5"><BandBar b={b} /></td>
                    <td className="px-2 py-1.5 text-right text-neutral-200">{b.full_pct.toFixed(0)}%</td>
                    <td className="px-2 py-1.5 text-right text-neutral-400">€{b.mean_abs_spread.toFixed(1)}</td>
                  </tr>
                ))}
                {nonSdac.map((b) => (
                  <tr key={`${b.zone_a}|${b.zone_b}`} className="border-t border-border/30 opacity-70">
                    <td className="px-2 py-1.5 text-neutral-400 whitespace-nowrap">
                      {b.label}
                      <span className="ml-1.5 text-[8px] px-1 py-px rounded border border-border text-neutral-600"
                        title="Switzerland is outside SDAC market coupling — this border converges rarely by market design, not congestion.">
                        no SDAC
                      </span>
                    </td>
                    <td className="px-2 py-1.5"><BandBar b={b} /></td>
                    <td className="px-2 py-1.5 text-right text-neutral-400">{b.full_pct.toFixed(0)}%</td>
                    <td className="px-2 py-1.5 text-right text-neutral-500">€{b.mean_abs_spread.toFixed(1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="px-4 pb-2 font-mono text-[8px] text-neutral-700">
            Sorted least-converged first · counts aggregated exactly from daily conv.* series ·
            not a target: see ⓘ
          </div>
        </>
      )}
    </Panel>
  )
}
