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
