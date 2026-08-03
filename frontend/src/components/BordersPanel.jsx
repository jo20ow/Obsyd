import { useEffect, useRef, useState } from 'react'
import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import { POLL_SLOW_MS } from '../utils/poll'
import {
  ResponsiveContainer, ComposedChart, Line, Bar, XAxis, YAxis, Tooltip, ReferenceLine, CartesianGrid,
} from 'recharts'
import { fmtTs, CHART_TOOLTIP_STYLE } from '../utils/chart'

const API = '/api'

// Convergence reads as "how coupled is this border": all-green = the two zones
// clear as one market; low = they clear apart, and the spread is the story.
function convergenceColor(pct) {
  if (pct == null) return 'text-neutral-700'
  if (pct >= 80) return 'text-emerald-400'
  if (pct >= 40) return 'text-amber-400'
  return 'text-orange-400'
}

// A small chip in the legend, matching the ones in the Util column.
function Chip({ kind }) {
  return (
    <span
      className={`text-[8px] px-1 py-px rounded border ${
        kind === 'ntc' ? 'border-cyan-glow/40 text-cyan-glow/80' : 'border-border text-neutral-600'
      }`}
    >
      {kind === 'ntc' ? 'NTC' : 'P95'}
    </span>
  )
}

// The structured replacement for the raw ~1100-char API note that used to be
// dumped into a 256-px popover. One row per column, HowToRead's <dl> pattern.
// The full prose definitions live in HOW TO READ; the API keeps its note.
function BordersLegend({ eps }) {
  const rows = [
    // Fallback mirrors backend COUPLED_EPS_EUR (0.5) — only shown in the brief
    // window before /power/borders delivers the real value.
    ['Coupled', `share of hours the two zones cleared at the same price (within €${eps ?? 0.5}/MWh).`],
    ['Ø spread', 'mean absolute day-ahead spread over the window (€/MWh).'],
    ['Now', 'the latest hour’s spread; the expensive side is named. “coupled” = below the threshold.'],
    ['At rail', 'share of hours the flow sat at or above this border’s own 95th percentile over the last year.'],
    [<span key="util" className="inline-flex items-center gap-1">Util <Chip kind="ntc" /> <Chip kind="p95" /></span>,
      'latest |flow| ÷ day-ahead NTC in the flow’s direction, where ENTSO-E publishes one (NTC chip) — offered capacity, can exceed 100%. P95 chip = no NTC published (flow-based Core / Nordics, by market design); “At rail” stands in.'],
    ['Counter', 'share of split hours where power ran from the expensive zone to the cheap one.'],
    ['Loop', 'physical flow minus scheduled exchange, where both grains exist — transit and loop together, not a claim about this interconnector.'],
    ['SCHED', 'row read from ENTSO-E’s scheduled exchanges (bidding-zone resolved) instead of the physical country-level flow.'],
  ]
  return (
    <div className="space-y-2">
      <dl className="space-y-1.5">
        {rows.map(([term, def], i) => (
          <div key={i} className="grid grid-cols-[64px_1fr] gap-x-2">
            <dt className="text-cyan-glow/90">{term}</dt>
            <dd className="text-neutral-400 leading-snug">{def}</dd>
          </div>
        ))}
      </dl>
      <div className="pt-1 border-t border-border/40 text-neutral-500">
        Descriptive statistics — a spread is not a claim this border was the binding
        constraint. Full definitions: HOW TO READ, bottom of this tab.
      </div>
    </div>
  )
}

function SpreadChart({ a, b }) {
  const { data, loading } = useFetchWithError(
    `${API}/power/spread?a=${a}&b=${b}&days=14`, { deps: [a, b] },
  )
  if (loading && !data) {
    return <div className="px-4 py-6 font-mono text-[10px] text-neutral-600 animate-pulse">Loading border…</div>
  }
  if (!data?.available) {
    return (
      <div className="px-4 py-3 font-mono text-[10px] text-neutral-500">
        {data?.reason || 'No data for this border.'}
      </div>
    )
  }
  const rows = (data.data ?? []).map((p) => ({
    x: p.ts_utc, spread: p.spread, flow: p.flow_mw != null ? p.flow_mw / 1000 : null,
  }))
  // Compact caption; the full API note (utilization semantics etc.) rides on hover.
  const [labelA, labelB] = (data.label ?? '').split('↔')
  return (
    <div className="px-2 pt-3 pb-1 border-t border-border/40">
      <div className="px-2 pb-1 font-mono text-[9px] text-neutral-600" title={data.note}>
        {data.label} · spread = {labelA} − {labelB} (€/MWh, line) · flow &gt; 0 = {labelA} exports (GW, bars)
      </div>
      <ResponsiveContainer width="100%" height={180}>
        <ComposedChart data={rows} margin={{ top: 4, right: 8, bottom: 2, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" />
          <XAxis dataKey="x" tickFormatter={fmtTs} tick={{ fontSize: 8, fill: '#737373' }} minTickGap={70} />
          <YAxis yAxisId="s" tick={{ fontSize: 8, fill: '#737373' }} width={40} />
          <YAxis yAxisId="f" orientation="right" tick={{ fontSize: 8, fill: '#525252' }} width={34} />
          <ReferenceLine yAxisId="s" y={0} stroke="#444" />
          <Tooltip
            contentStyle={CHART_TOOLTIP_STYLE}
            labelFormatter={fmtTs}
            formatter={(v, n) => [
              n === 'spread' ? `${Number(v).toFixed(1)} €/MWh` : `${Number(v).toFixed(2)} GW`,
              n === 'spread' ? 'spread (A−B)' : 'flow (A→B)',
            ]}
          />
          <Bar yAxisId="f" dataKey="flow" fill="#334155" isAnimationActive={false} />
          <Line yAxisId="s" type="monotone" dataKey="spread" stroke="#fbbf24" strokeWidth={1.4}
            dot={false} connectNulls isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}

/**
 * The border layer: day-ahead prices joined to physical flows, per interconnector.
 *
 * Descriptive statistics only. A spread is NOT a claim that this interconnector
 * was the binding constraint — the Core region clears flow-based, where the
 * constraint is a network element inside the grid, not the border itself. The
 * backend caption says so and travels with the data.
 */
export default function BordersPanel({ focus }) {
  const [open, setOpen] = useState(null)  // "A|B" of the expanded border
  const { data, loading, error } = useFetchWithError(`${API}/power/borders?days=30`, {
    pollMs: POLL_SLOW_MS,
  })

  // Map arc click → this panel: open the border's row. Keys match by
  // construction — both sides use the canonically sorted zone pair. State is
  // adjusted during render (React's previous-renders pattern); only the DOM
  // scroll stays in an effect. Both trackers initialize to the INCOMING focus,
  // so a remount with an old focus (tab switch and back) neither re-opens the
  // row nor scrolls — only a focus that changes after mount does.
  const [seenFocus, setSeenFocus] = useState(focus)
  if (focus && focus !== seenFocus) {
    setSeenFocus(focus)
    if (focus.a && focus.b) setOpen(`${focus.a}|${focus.b}`)
  }
  const mountFocus = useRef(focus)
  useEffect(() => {
    // `scroll: false` = prepare the row but STAY PUT. The EUROPE tab sends it
    // for map clicks: the panel is two screens below the map, so scrolling here
    // dragged the page off the thing the user had just clicked. Opening +
    // expanding is unchanged, so the row is ready whenever they come down —
    // by their own scroll, or via the chip the map offers. Any caller that
    // omits the field keeps the original scroll-into-view behaviour.
    if (!focus?.a || !focus?.b || focus === mountFocus.current || focus.scroll === false) return
    document.getElementById('panel-power-borders')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [focus])

  // The degraded states keep the panel's ANCHOR id: something on the map always
  // offers to scroll here (the EUROPE tab's border chip), and a link that
  // silently goes nowhere is worse than one that lands on "fetch error".
  if (error) {
    return (
      <div id="panel-power-borders" className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">BORDERS // FETCH ERROR</div>
      </div>
    )
  }
  if (!data?.available && !loading) {
    return (
      <div id="panel-power-borders" className="border border-border bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-neutral-500">
          BORDERS — {data?.reason || 'no border data yet.'}
        </div>
      </div>
    )
  }

  const borders = data?.borders ?? []
  const uncoverable = data?.uncoverable_borders ?? []
  const superseded = data?.superseded_aggregate_flows ?? []

  return (
    <Panel
      id="power-borders"
      title="BORDERS · PRICE CONVERGENCE & CONGESTION"
      info={<BordersLegend eps={data?.coupled_eps_eur} />}
      infoWide
      expandSignal={focus?.ts}
      collapsible
      headerRight={
        borders.length > 0 && (
          <span className="font-mono text-[9px] text-neutral-600">
            {borders.length} borders · last {data?.days}d
          </span>
        )
      }
    >
      {loading && !data ? (
        <div className="px-4 py-4 font-mono text-[10px] text-neutral-600 animate-pulse">Loading borders…</div>
      ) : (
        <>
          <div className="px-2 py-2 overflow-x-auto">
            <table className="w-full font-mono text-[11px]">
              <thead>
                <tr className="text-[9px] text-neutral-600 uppercase tracking-wider">
                  <th className="text-left px-2 py-1">Border</th>
                  <th className="text-right px-2 py-1" title="Share of hours the two zones cleared at the same price">Coupled</th>
                  <th className="text-right px-2 py-1" title="Mean absolute day-ahead spread over the window">Ø spread</th>
                  <th className="text-right px-2 py-1" title="Latest spread; the expensive side is named">Now</th>
                  <th className="text-right px-2 py-1" title="Share of hours the flow reached this border's own 95th percentile — the honest proxy where no NTC is published (P95 chip)">At rail</th>
                  <th className="text-right px-2 py-1" title="Latest |flow| ÷ day-ahead NTC where one is published — offered capacity, can exceed 100% (chips: NTC = real denominator, P95 = own history stands in).">Util</th>
                  <th className="text-right px-2 py-1" title="Share of split hours where power ran from the expensive zone to the cheap one">Counter</th>
                  <th className="text-right px-2 py-1" title="Physical flow minus scheduled exchange, where both grains exist. Transit and loop flow together — not a claim about this interconnector.">Loop</th>
                </tr>
              </thead>
              <tbody>
                {borders.map((b) => {
                  const key = `${b.zone_a}|${b.zone_b}`
                  const isOpen = open === key
                  return (
                    <tr
                      key={key}
                      onClick={() => setOpen(isOpen ? null : key)}
                      className={`border-t border-border/30 cursor-pointer hover:bg-white/[0.02] ${isOpen ? 'bg-white/[0.03]' : ''}`}
                    >
                      <td className="px-2 py-1.5 text-neutral-300">
                        {isOpen ? '▾ ' : '▸ '}{b.label}
                        {/* Which grain the border was read from. `scheduled` is ENTSO-E's
                            bidding-zone schedule — the only one that can see DK1 from DK2. */}
                        {b.flow_source === 'scheduled' && (
                          <span className="ml-1.5 text-[9px] text-cyan-glow/60" title="Read from ENTSO-E scheduled exchanges (bidding-zone resolved)">SCHED</span>
                        )}
                      </td>
                      <td className={`px-2 py-1.5 text-right ${convergenceColor(b.convergence_pct)}`}>
                        {b.convergence_pct != null ? `${b.convergence_pct.toFixed(0)}%` : '—'}
                      </td>
                      <td className="px-2 py-1.5 text-right text-neutral-200">
                        {b.mean_abs_spread != null ? `€${b.mean_abs_spread.toFixed(0)}` : '—'}
                      </td>
                      <td className="px-2 py-1.5 text-right text-neutral-400">
                        {b.latest_spread == null ? '—'
                          : b.expensive_side == null ? 'coupled'
                            : `€${Math.abs(b.latest_spread).toFixed(0)} · ${b.expensive_side === b.zone_a ? b.label.split('↔')[0] : b.label.split('↔')[1]} dearer`}
                      </td>
                      <td className="px-2 py-1.5 text-right text-neutral-400">
                        {b.at_rail_pct != null ? `${b.at_rail_pct.toFixed(0)}%` : '—'}
                      </td>
                      {/* The chip is ALWAYS rendered — the NTC/P95 distinction is the honesty:
                          a real published denominator vs the border's own history standing in. */}
                      <td className="px-2 py-1.5 text-right text-neutral-400 whitespace-nowrap">
                        {b.util_latest_pct != null ? `${b.util_latest_pct.toFixed(0)}%` : '—'}
                        <span
                          className={`ml-1.5 text-[8px] px-1 py-px rounded border ${
                            b.capacity_source === 'ntc'
                              ? 'border-cyan-glow/40 text-cyan-glow/80'
                              : 'border-border text-neutral-600'
                          }`}
                          title={b.capacity_source === 'ntc'
                            ? 'Denominator: day-ahead NTC (ENTSO-E A61) — the capacity offered to the auction, not a physical limit. Utilization can exceed 100% after intraday/countertrading.'
                            : "No day-ahead NTC is published for this border (flow-based Core region / Nordics — a market-design fact, not missing data). 'At rail' uses the border's own 95th percentile instead."}
                        >
                          {b.capacity_source === 'ntc' ? 'NTC' : 'P95'}
                        </span>
                      </td>
                      <td className={`px-2 py-1.5 text-right ${b.counter_price_pct > 10 ? 'text-orange-400' : 'text-neutral-500'}`}>
                        {b.counter_price_pct != null ? `${b.counter_price_pct.toFixed(0)}%` : '—'}
                      </td>
                      {/* Absent with a reason, never silently blank: most sub-zone borders have
                          no physical grain at all, and that is coverage, not a bug. */}
                      <td className="px-2 py-1.5 text-right text-neutral-500" title={b.loop_reason || undefined}>
                        {b.loop_mean_mw != null
                          ? `${b.loop_mean_mw > 0 ? '+' : ''}${(b.loop_mean_mw / 1000).toFixed(1)} GW`
                          : <span className="text-neutral-700">n/a</span>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {open && <SpreadChart a={open.split('|')[0]} b={open.split('|')[1]} />}

          {(uncoverable.length > 0 || superseded.length > 0) && (
            <div className="px-4 py-2 border-t border-border/40 font-mono text-[9px] text-neutral-700 leading-relaxed space-y-1">
              {uncoverable.length > 0 && (
                <div>
                  <span className="text-neutral-600">Not coverable ({uncoverable.length}):</span>{' '}
                  {uncoverable.join(', ')} — we carry no bidding zone for these neighbours (GB left
                  ENTSO-E&apos;s day-ahead publication after Brexit), so no grain will resolve them.
                </div>
              )}
              {superseded.length > 0 && (
                /* Not a gap. These are Energy-Charts' COUNTRY aggregates — Denmark, not DK1 and
                   DK2 — and the real bidding-zone borders behind them are all covered above, by
                   the scheduled grain. Saying "uncoverable" here would claim a blindness the
                   desk no longer has. */
                <div>
                  <span className="text-neutral-600">Superseded ({superseded.length}):</span>{' '}
                  {superseded.join(', ')} — country-level aggregates from Energy-Charts. The
                  bidding-zone borders behind them are covered above (scheduled exchanges), which
                  is why they are listed rather than shown.
                </div>
              )}
            </div>
          )}
        </>
      )}
    </Panel>
  )
}
