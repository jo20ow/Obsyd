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
  ['Price-setting tech', 'which technology is estimated to have set a zone’s price in its latest hour: the most expensive band that meaningfully dispatches in a FIXED merit order (must-run renewables → nuclear → lignite → hard coal → gas → oil), with flexible hydro allowed to claim it, since reservoirs and pumped storage bid opportunity cost at any level. An estimate, not a cost model — no fuel or CO₂ prices enter it, so it cannot see coal↔gas switching, and an import can set the price with no domestic technology marginal at all. “Tension” = the price sits outside the band that technology implies: reported, never reclassified.'],
  ['Coupled border', 'two neighbouring zones cleared at (nearly) the same day-ahead price — the interconnector had room, so they traded as one market. A low coupled share means they clear apart, and the spread is the story.'],
  ['At the rail', 'the physical flow sat at or above this border’s own 95th percentile of the last year — near the top of its observed range. A statement about its own history, not about a physical limit.'],
  ['NTC utilization', 'latest |flow| ÷ the day-ahead NTC in the flow’s direction, where ENTSO-E publishes one. NTC is the capacity OFFERED to the auction, not a physical limit — utilization can exceed 100% after intraday trading and countertrading. The flow-based Core region and the Nordics publish none by market design → those borders show a P95 chip instead (their own 95th percentile stands in).'],
  ['Counter-price flow', 'power ran from the expensive zone to the cheap one during hours the prices were split. Common under flow-based market coupling, where a border can be loaded to relieve a constraint elsewhere in the grid — noteworthy, not automatically wrong.'],
  ['Loop flow', 'physical flow minus scheduled exchange, where both records exist — transit and loop flow together, not a claim about any single interconnector.'],
  ['Line outage', 'ENTSO-E’s A78 record: a transmission asset (AC line, DC link, transformer, substation) that its TSO reports as unavailable or de-rated for a window. “Forced” = unplanned, the asset tripped or failed; “planned” = scheduled maintenance, which is the overwhelming majority. The MW figure is “available” — what the asset can STILL carry (0 = fully out). ENTSO-E publishes no capacity baseline for these assets, so the amount LOST is not derivable and Obsyd never invents it. TSOs file these at their own pace and revise them (a withdrawn revision removes the event), and the map shows what is out right now plus what starts inside the next 30 days — it is a filing record, not a live grid telemetry feed. On the map each affected border draws one dashed chord, and the dash carries the timing: a TIGHT dash means something on that border is out right now, a SPARSE one that it only starts later inside the 30-day horizon (three weeks out must not read as “this line is gone”).'],
  ['SCHED vs physical', 'which record a border is read from. SCHED = ENTSO-E’s scheduled commercial exchanges, resolved per bidding zone (the only grain that can see DK1 vs DK2); physical = the metered country-level flow.'],
]

// The Honest-Record vocabulary (EXPLORE tab's DATA QUALITY + REVISIONS LEDGER
// panels). Every term describes what the SOURCE published or restated — none
// of it judges the data or the market.
const QUALITY_TERMS = [
  ['Completeness', 'the share of a UTC day’s expected intervals the source actually published (24 for hourly series, 96 for 15-min series), averaged over a 30/90-day window. A low day is hours the source has not (yet) published — a statement about the record, not the market.'],
  ['Revision / restatement', 'the source re-published a DIFFERENT value for an hour it had already published, beyond float noise. “Mature” = observed more than 48 h after the hour it restates — settled data changed, not the routine provisional fill-in sources run for a day or two.'],
  ['Arrival lag', 'the wall-clock gap between when a fetch arrived and the newest hour it delivered. Negative (“ahead”) for day-ahead series — the auction publishes tomorrow’s hours, so the data runs ahead of the clock.'],
  ['Quality flag', 'a rule-based description of odd published data: solar reported in the dead of night, load flatlining at exact zero, an hourly step 8× larger than the series’ own trailing month. Each flag describes the feed, never the market.'],
  ['Zone-level checks', 'quality checks that need several series at once — e.g. total generation below load while the zone exports. They appear as their own “zone-level checks” row, only on flagged days, with no completeness or lag of their own.'],
]

// The forecast-scoreboard vocabulary (ANALYTICS tab). OBSYD grades ENTSO-E's
// own published D-1 forecasts against its published actuals — it makes no
// forecasts of its own, and every score states its sample.
const SCOREBOARD_TERMS = [
  ['What is graded', 'ENTSO-E’s own published day-ahead forecasts for load, residual load, wind and solar, compared against the actuals the same source later published. OBSYD makes no forecasts — it reports how the official ones fared.'],
  ['MAE / RMSE', 'mean absolute error: the typical hourly miss in MW, direction ignored. RMSE punishes large misses harder — RMSE well above MAE means the errors are spiky, not steady.'],
  ['Bias', 'mean(forecast − actual) in MW. Positive = the published forecast leaned high on average, negative = it leaned low. (The older forecast-error strip on the POWER tab states the same number with the opposite sign convention.)'],
  ['MAPE / nMAE', 'the miss as a percentage. MAPE (% of the actual) works for load only — wind and solar hit honest zeros overnight, residual load crosses zero. Wind/solar rank by nMAE instead: MAE as % of the zone’s installed capacity (ENTSO-E A68), so a 500 MW miss in a 60 GW fleet doesn’t read like one in a 5 GW fleet.'],
  ['Skill vs naive', '1 − MAE/MAE_naive against two no-model yardsticks built from published actuals alone: persistence (the actual at the same hour yesterday) and seasonal (same hour last week). Positive = the published forecast beat the yardstick; a forecast that trails persistence added no information that day.'],
  ['n=', 'every score names the days (and hours) it is computed over — a 30-day skill over 6 days of data says so.'],
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
        <div className="pt-2 border-t border-border/40 font-mono text-[10px] text-neutral-500 tracking-wider">DATA QUALITY</div>
        <dl className="space-y-2">
          {QUALITY_TERMS.map(([term, def]) => (
            <div key={term} className="grid grid-cols-1 sm:grid-cols-[160px_1fr] gap-x-3 gap-y-0.5">
              <dt className="font-mono text-[11px] text-cyan-glow/90">{term}</dt>
              <dd className="font-mono text-[11px] text-neutral-400 leading-snug">{def}</dd>
            </div>
          ))}
        </dl>
        <div className="pt-2 border-t border-border/40 font-mono text-[10px] text-neutral-500 tracking-wider">FORECAST SCOREBOARD</div>
        <dl className="space-y-2">
          {SCOREBOARD_TERMS.map(([term, def]) => (
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
