import Panel from './Panel'

// Persistent, always-reachable orientation — replaces the old one-time, permanently
// dismissible intro. Open by default for newcomers; collapsible (state persists) so
// repeat users can tuck it away, but it can always be re-opened. Teaches both what
// Obsyd shows AND the market terms, in plain language.
const TERMS = [
  ['State (CALM / ELEVATED / STRESSED)', 'how far something sits from its OWN recent history — a deviation, not a forecast. STRESSED ≈ ≥3σ from its trailing norm; ELEVATED ≈ ≥2σ or a flag. Each panel states the exact window it measured against.'],
  ['Residual load', 'electricity demand minus wind & solar — the demand that gas / coal / nuclear must cover. It is the biggest driver of the power price.'],
  ['Spark spread', 'the profit margin of a gas-fired power plant: power price − gas cost. Positive = worth running; negative = uneconomic.'],
  ['Dunkelflaute', 'a “dark lull”: wind + solar cover under 15% of demand AND that is unusually dark for this zone in this month (bottom 2% of its own record) — thermal plants carry the grid, prices tend to firm. A zone with no wind/solar fleet cannot have one; its 0% is its normal, not an event.'],
  ['Day-ahead price', 'tomorrow’s hourly electricity price, set at today’s auction (€/MWh).'],
  ['Coupled border', 'two neighbouring zones cleared at (nearly) the same day-ahead price — the interconnector had room, so they traded as one market. A low coupled share means they clear apart, and the spread is the story.'],
  ['At the rail', 'the physical flow sat at or above this border’s own 95th percentile of the last year — near the top of its observed range. A statement about its own history, not about a physical limit.'],
  ['NTC utilization', 'latest |flow| ÷ the day-ahead NTC in the flow’s direction, where ENTSO-E publishes one. NTC is the capacity OFFERED to the auction, not a physical limit — utilization can exceed 100% after intraday trading and countertrading. The flow-based Core region and the Nordics publish none by market design → those borders show a P95 chip instead (their own 95th percentile stands in).'],
  ['Counter-price flow', 'power ran from the expensive zone to the cheap one during hours the prices were split. Common under flow-based market coupling, where a border can be loaded to relieve a constraint elsewhere in the grid — noteworthy, not automatically wrong.'],
  ['Loop flow', 'physical flow minus scheduled exchange, where both records exist — transit and loop flow together, not a claim about any single interconnector.'],
  ['SCHED vs physical', 'which record a border is read from. SCHED = ENTSO-E’s scheduled commercial exchanges, resolved per bidding zone (the only grain that can see DK1 vs DK2); physical = the metered country-level flow.'],
]

export default function HowToRead() {
  return (
    <Panel id="how-to-read" title="NEW HERE? HOW TO READ THIS" collapsible defaultCollapsed={true}>
      <div className="px-4 py-3 space-y-3">
        <p className="font-mono text-[12px] text-neutral-300 leading-relaxed">
          Obsyd is the <span className="text-cyan-glow">European electricity desk</span> — the power grid (prices, load, generation, flows) and the gas that fuels it.
          Every number tells you <span className="text-neutral-200">how far it is from normal</span> and what
          that means — descriptive, never a price forecast.
        </p>
        <dl className="space-y-2">
          {TERMS.map(([term, def]) => (
            <div key={term} className="grid grid-cols-1 sm:grid-cols-[160px_1fr] gap-x-3 gap-y-0.5">
              <dt className="font-mono text-[11px] text-cyan-glow/90">{term}</dt>
              <dd className="font-mono text-[11px] text-neutral-400 leading-snug">{def}</dd>
            </div>
          ))}
        </dl>
      </div>
    </Panel>
  )
}
