import { useEffect, useState } from 'react'
import { useAuth } from '../context/AuthContext'
import { composeEuropeNarrative } from '../utils/narrative'
import { Wordmark, SectionLabel, CodeBlock, LinkButton } from './doc/DocShell'

// Landing — academic-institutional (2026-08 redesign): paper surface, serif
// headlines, sources and citability up front, a live prose figure instead of a
// marketing stats band. Copy carries over from the previous landing — the
// claims were audited (PR #140/#154); reword, never overclaim.

const PILLARS = [
  {
    title: 'See the whole grid at a glance',
    body:
      'Day-ahead prices, load & residual load and the generation mix for 37 European bidding zones — hourly everywhere, at the market’s real 15-minute resolution where SDAC trades it — plus a live generation-outage board, cross-border flows, Nordic & Alpine reservoir levels and the gas that fuels the marginal price. One desk, not a dozen ENTSO-E queries to reconcile by hand.',
  },
  {
    title: 'Catch grid stress as it happens',
    body:
      'A live radar flags forced power-plant outages, negative prices, Dunkelflaute (wind+solar below 15% of load and unusually dark for that zone) and gas-balance anomalies the moment they deviate from each zone’s own history — with a plain-language "what this means". A deviation vs history, not a forecast.',
  },
  {
    title: 'Honest about its own data',
    body:
      'Every number carries its age — a stalled feed says STALE instead of pretending. Every threshold and anomaly check runs in code you can audit (AGPL-3.0): no black-box ML, no "trust us". Run OBSYD on your own infra, or use obsyd.dev — same code either way.',
  },
]

const SOURCES = [
  {
    source: 'ENTSO-E Transparency Platform',
    what: 'Day-ahead prices, load, generation mix & forecasts, plant outages, hydro reservoirs — 37 bidding zones',
    cadence: 'Hourly · 15-min where SDAC trades it',
    license: 'Free reuse with attribution',
  },
  {
    source: 'Fraunhofer Energy-Charts',
    what: 'Physical cross-border power flows',
    cadence: 'Hourly',
    license: 'CC BY 4.0',
  },
  {
    source: 'GIE (AGSI / ALSI) + ENTSOG',
    what: 'European gas storage, LNG send-out & pipeline flows',
    cadence: 'Daily',
    license: 'Free with attribution',
  },
  {
    source: 'TTF · Henry Hub',
    what: 'The gas price that sets the marginal power price (spark spread)',
    cadence: 'Daily',
    license: 'Indicative quotes',
  },
]

const DOI = '10.5281/zenodo.21699869'
const GITHUB = 'https://github.com/jo20ow/Obsyd'

// Live prose figure — the desk's own "Europe right now" read, composed from
// /api/power/overview via the same template as the desk (utils/narrative.js).
// Fetch-once; on error or empty the section disappears rather than showing a
// broken promise.
function LiveFigure() {
  const [data, setData] = useState(null)
  useEffect(() => {
    let alive = true
    fetch('/api/power/overview')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (alive) setData(d) })
      .catch(() => {})
    return () => { alive = false }
  }, [])

  const parts = data?.available ? composeEuropeNarrative(data?.zones) : null
  if (!parts) return null
  const { lead, moverText, spreadText, negText, dunkelText } = parts

  return (
    <section className="border-y border-border bg-surface">
      <div className="max-w-5xl mx-auto px-5 py-12">
        <SectionLabel className="mb-5">LIVE FROM THE DESK</SectionLabel>
        <figure>
          <p className="font-serif text-xl sm:text-2xl leading-relaxed text-neutral-200 max-w-3xl">
            {lead}{moverText ? ' — ' : '. '}
            {moverText && <>{moverText}. </>}
            {spreadText && <>{spreadText} </>}
            {negText && <>{negText} </>}
            {dunkelText && <>{dunkelText} </>}
          </p>
          <figcaption className="mt-4 text-[11px] text-neutral-500">
            Composed live from ENTSO-E day-ahead, load &amp; generation
            {data?.zones?.length ? ` · ${data.zones.length} bidding zones` : ''} · descriptive, not a forecast ·{' '}
            <a href="/app" className="text-cyan-glow hover:underline">open the desk →</a>
          </figcaption>
        </figure>
      </div>
    </section>
  )
}

export default function Landing() {
  const { user } = useAuth()

  return (
    <div className="min-h-screen text-neutral-300">
      {/* TOP NAV */}
      <header className="border-b border-border bg-surface">
        <div className="max-w-5xl mx-auto px-5 py-3 flex items-center justify-between">
          <Wordmark />
          <nav className="flex items-center gap-5 text-[12px] text-neutral-500">
            <a href="#how" className="hover:text-neutral-300 hidden sm:inline">
              How it works
            </a>
            <a href="/docs" className="hover:text-neutral-300">
              API
            </a>
            <a
              href={GITHUB}
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-neutral-300 hidden sm:inline"
            >
              GitHub
            </a>
            <a href="/app" className="text-cyan-glow hover:opacity-80">
              {user ? 'Open app →' : 'Live demo →'}
            </a>
          </nav>
        </div>
      </header>

      {/* HERO */}
      <section className="max-w-5xl mx-auto px-5 pt-16 pb-14 sm:pt-24 sm:pb-20">
        <SectionLabel className="mb-6">THE EUROPEAN ELECTRICITY DESK</SectionLabel>
        <h1 className="font-serif text-4xl sm:text-5xl font-semibold tracking-tight text-neutral-100 leading-[1.15] mb-6 max-w-3xl">
          The European power grid — every zone, one desk, free.
        </h1>
        <p className="text-[15px] text-neutral-400 max-w-2xl leading-relaxed mb-8">
          A free European power desk: day-ahead prices, load &amp; residual load, generation mix and
          wind/solar for 37 European bidding zones — plus cross-border flows, tomorrow’s load &amp;
          residual forecast and the gas that fuels the marginal price — from the official record
          (ENTSO-E, GIE) and Fraunhofer Energy-Charts, with a live anomaly radar. Descriptive,
          auditable, open source under AGPL-3.0 — run it yourself, or use the hosted cloud.
        </p>
        <div className="flex flex-col sm:flex-row gap-3 mb-6">
          <LinkButton href="/app" primary>
            Open the live desk →
          </LinkButton>
          <LinkButton href="/docs">Read the API docs</LinkButton>
          <LinkButton href={GITHUB} external>
            Self-host on GitHub
          </LinkButton>
        </div>
        <p className="text-[11px] text-neutral-500 leading-relaxed">
          Built on the official record: ENTSO-E · Fraunhofer Energy-Charts (CC BY 4.0) · GIE —
          free and open source (AGPL-3.0), no paywall, no account needed · citable:{' '}
          <a href="#cite" className="text-cyan-glow hover:underline">DOI {DOI}</a>
        </p>
      </section>

      {/* LIVE FIGURE — replaces the old stats band */}
      <LiveFigure />

      {/* HOW IT WORKS */}
      <section id="how" className="max-w-5xl mx-auto px-5 py-16 sm:py-20">
        <SectionLabel className="mb-4">HOW IT WORKS</SectionLabel>
        <h2 className="font-serif text-2xl sm:text-3xl font-semibold text-neutral-100 mb-10">
          See the situation. Catch the stress. Stay honest.
        </h2>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-8 mb-14">
          {PILLARS.map((p) => (
            <div key={p.title} className="border-t-2 border-border pt-4">
              <div className="font-serif text-[17px] font-semibold text-neutral-100 mb-2 leading-snug">
                {p.title}
              </div>
              <div className="text-[13px] text-neutral-500 leading-relaxed">{p.body}</div>
            </div>
          ))}
        </div>

        {/* The data, laid on the table — sources, cadence, licenses. */}
        <div className="border border-border bg-surface rounded overflow-hidden">
          <div className="px-4 py-2.5 border-b border-border/60 smallcaps text-[11px] text-neutral-500">
            WHAT OBSYD READS
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-[12px]">
              <thead>
                <tr className="text-left text-neutral-500">
                  <th className="px-4 py-2 font-medium smallcaps">SOURCE</th>
                  <th className="px-4 py-2 font-medium smallcaps">WHAT</th>
                  <th className="px-4 py-2 font-medium smallcaps">CADENCE</th>
                  <th className="px-4 py-2 font-medium smallcaps">LICENSE</th>
                </tr>
              </thead>
              <tbody>
                {SOURCES.map((s) => (
                  <tr key={s.source} className="border-t border-border/50 align-top">
                    <td className="px-4 py-2.5 text-neutral-200 whitespace-nowrap">{s.source}</td>
                    <td className="px-4 py-2.5 text-neutral-400">{s.what}</td>
                    <td className="px-4 py-2.5 text-neutral-500 whitespace-nowrap">{s.cadence}</td>
                    <td className="px-4 py-2.5 text-neutral-500 whitespace-nowrap">{s.license}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {/* HONEST SCOPE */}
      <section className="border-y border-border bg-surface">
        <div className="max-w-5xl mx-auto px-5 py-16 sm:py-20 grid grid-cols-1 md:grid-cols-2 gap-10">
          <div>
            <SectionLabel className="mb-4">WHY OBSYD</SectionLabel>
            <h2 className="font-serif text-2xl font-semibold text-neutral-100 mb-5 leading-snug">
              The official power record, turned into a desk.
            </h2>
            <p className="text-[13px] text-neutral-400 leading-relaxed max-w-lg">
              OBSYD doesn&apos;t match Montel, EPEX or a Bloomberg terminal on intraday or
              settlement-grade data — it can&apos;t, and it doesn&apos;t pretend to. What it does is
              turn the free, official European power record — ENTSO-E, Fraunhofer Energy-Charts, GIE —
              into one auditable, legible desk, and watch it for you, so you stop wiring up a dozen
              ENTSO-E queries by hand.
            </p>
          </div>
          <div className="border border-border rounded p-5 text-[12px] text-neutral-500 leading-relaxed self-start">
            <div className="smallcaps text-[11px] text-neutral-500 mb-3">NOT FOR</div>
            <ul className="space-y-2">
              <li>· Intraday or settlement-grade trade execution</li>
              <li>· Desks already paying for Montel / EPEX / Bloomberg</li>
              <li>· Non-power commodities — oil flows, shipping, metals (that&apos;s a separate tool)</li>
            </ul>
            <div className="smallcaps text-[11px] text-neutral-500 mt-5 mb-3">MADE FOR</div>
            <ul className="space-y-2">
              <li>· Power traders &amp; energy-risk analysts without a Montel/Bloomberg seat</li>
              <li>· Utilities &amp; industrials tracking prices, residual load and grid stress</li>
              <li>· Researchers &amp; journalists needing one honest source for the EU power picture</li>
              <li>· Anyone who wants to read the signal code, not trust it blindly</li>
            </ul>
          </div>
        </div>
      </section>

      {/* ENERGY WATCH */}
      <section className="max-w-5xl mx-auto px-5 py-16 sm:py-20">
        <SectionLabel className="mb-4">YOUR ENERGY WATCH</SectionLabel>
        <h2 className="font-serif text-2xl sm:text-3xl font-semibold text-neutral-100 mb-4">
          Don&apos;t watch the desk. Let it watch for you.
        </h2>
        <p className="text-[13px] text-neutral-400 leading-relaxed max-w-2xl mb-10">
          You shouldn&apos;t have to refresh a dozen tabs to know when the energy system moves. OBSYD
          turns the radar into your inbox — set the alerts that matter, and it pings you with the
          evidence the moment something deviates. Free, like the rest of it.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-8 mb-8">
          <div className="border-t-2 border-border pt-4">
            <div className="font-medium text-neutral-100 text-[14px] mb-2">Set your alerts</div>
            <div className="text-[13px] text-neutral-500 leading-relaxed">
              Choose the anomalies that matter to you — negative prices, Dunkelflaute, day-ahead
              spikes, spark-spread and gas-balance breaches — with your own thresholds.
            </div>
          </div>
          <div className="border-t-2 border-border pt-4">
            <div className="font-medium text-neutral-100 text-[14px] mb-2">We watch the radar</div>
            <div className="text-[13px] text-neutral-500 leading-relaxed">
              Every rule is re-checked against its own history around the clock. A cooldown keeps it
              to real moves, not false-alarm spam.
            </div>
          </div>
          <div className="border-t-2 border-border pt-4">
            <div className="font-medium text-neutral-100 text-[14px] mb-2">You see it in the feed</div>
            <div className="text-[13px] text-neutral-500 leading-relaxed">
              The trigger, the evidence, and a link straight to the chart — every firing lands in the
              ALERTS feed on the desk, ranked by severity.
            </div>
          </div>
        </div>
        <div className="flex flex-col sm:flex-row items-start sm:items-center gap-3">
          <LinkButton href="/app" primary>
            Set up your alerts →
          </LinkButton>
          <span className="text-[12px] text-neutral-500">Log in with a magic link — no card, no spam.</span>
        </div>
      </section>

      {/* FOR DEVELOPERS */}
      <section id="developers" className="border-y border-border bg-surface">
        <div className="max-w-5xl mx-auto px-5 py-16 sm:py-20">
          <SectionLabel className="mb-4">FOR DEVELOPERS</SectionLabel>
          <h2 className="font-serif text-2xl sm:text-3xl font-semibold text-neutral-100 mb-3">
            Every number, straight into your notebook.
          </h2>
          <p className="text-[13px] text-neutral-400 max-w-2xl mb-7 leading-relaxed">
            A free, versioned public API over the same sources — day-ahead prices down to the
            market&apos;s real 15-minute resolution, load, generation mix, cross-border flows. No key,
            no account. JSON, streamed CSV or Parquet — and a pandas client.
          </p>
          <CodeBlock title="PYTHON" className="max-w-2xl mb-6">
            <span className="text-neutral-500">$ </span>pip install obsyd
            {'\n\n'}
            <span className="text-cyan-glow">from</span> obsyd <span className="text-cyan-glow">import</span> Obsyd
            {'\n'}
            df = Obsyd().series(<span className="text-amber-300">&quot;price.dayahead&quot;</span>, <span className="text-amber-300">&quot;DE_LU&quot;</span>, start=<span className="text-amber-300">&quot;2024-01-01&quot;</span>)
            {'\n'}
            <span className="text-neutral-500"># → a pandas DataFrame, UTC-indexed. That&apos;s it.</span>
          </CodeBlock>
          <div className="flex flex-col sm:flex-row flex-wrap gap-3">
            <LinkButton href="/docs" primary>
              API docs &amp; quickstart →
            </LinkButton>
            <LinkButton href="/api/docs">Swagger UI</LinkButton>
            <LinkButton href="https://pypi.org/project/obsyd/" external>
              obsyd on PyPI
            </LinkButton>
            <LinkButton href={`${GITHUB}/tree/main/clients/python`} external>
              Client + example notebooks
            </LinkButton>
          </div>
        </div>
      </section>

      {/* PRICING */}
      <section id="pricing" className="max-w-5xl mx-auto px-5 py-16 sm:py-20">
        <SectionLabel className="mb-4">PRICING</SectionLabel>
        <h2 className="font-serif text-2xl sm:text-3xl font-semibold text-neutral-100 mb-8">
          It&apos;s free. All of it.
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 max-w-3xl">
          <div className="border border-border bg-surface rounded p-6">
            <div className="smallcaps text-[11px] text-neutral-500 mb-1">CLOUD</div>
            <div className="font-serif text-3xl text-neutral-100 mb-1">€0</div>
            <div className="text-[11px] text-neutral-500 mb-5">on obsyd.dev · no card, no account needed</div>
            <ul className="text-[12px] text-neutral-400 space-y-1.5">
              <li>· Full power desk + anomaly radar</li>
              <li>· Day-ahead, residual load, generation mix, cross-border flows, forecasts</li>
              <li>· Watchlist, custom alerts</li>
              <li>· Everything unlocked, no limits</li>
            </ul>
          </div>
          <div className="border border-border bg-surface rounded p-6">
            <div className="smallcaps text-[11px] text-neutral-500 mb-1">SELF-HOST</div>
            <div className="font-serif text-3xl text-neutral-100 mb-1">€0</div>
            <div className="text-[11px] text-neutral-500 mb-5">AGPL-3.0 · your infra, your keys</div>
            <ul className="text-[12px] text-neutral-400 space-y-1.5">
              <li>· The exact same code, end to end</li>
              <li>· Bring your own API keys</li>
              <li>· No usage limits</li>
              <li>· You handle updates + ops</li>
            </ul>
          </div>
        </div>
      </section>

      {/* CITE */}
      <section id="cite" className="border-y border-border bg-surface">
        <div className="max-w-5xl mx-auto px-5 py-16 sm:py-20">
          <SectionLabel className="mb-4">CITE OBSYD</SectionLabel>
          <h2 className="font-serif text-2xl font-semibold text-neutral-100 mb-4">
            Citable, like the record it reads.
          </h2>
          <p className="text-[13px] text-neutral-400 max-w-2xl mb-6 leading-relaxed">
            If OBSYD feeds a paper, a thesis or a dataset, cite the archived release — the repository
            ships a <span className="font-code text-[12px]">CITATION.cff</span>, and every release is
            archived on Zenodo.
          </p>
          <CodeBlock title="CITATION" className="max-w-2xl">
            Weisser, J. OBSYD — The European Electricity Desk (open-source software &amp; data API).
            {'\n'}
            DOI: <span className="text-amber-300">{DOI}</span> · https://obsyd.dev
          </CodeBlock>
        </div>
      </section>

      {/* FOOTER */}
      <footer className="border-t border-border">
        <div className="max-w-5xl mx-auto px-5 py-8 flex flex-col sm:flex-row gap-4 justify-between items-start sm:items-center text-[11px] text-neutral-500">
          <div>
            OBSYD is open source under AGPL-3.0. Source on{' '}
            <a
              href={GITHUB}
              target="_blank"
              rel="noopener noreferrer"
              className="text-cyan-glow hover:underline"
            >
              GitHub
            </a>
            {' · '}
            <a href="/api/alerts/rss" className="text-cyan-glow hover:underline">Anomaly radar RSS</a>
            {' · '}
            <a href="/docs" className="text-cyan-glow hover:underline">API</a>
            {' · '}
            <a href="/impressum" className="hover:text-neutral-300">Impressum</a>
            {' · '}
            <a href="/datenschutz" className="hover:text-neutral-300">Datenschutz</a>
          </div>
          <div className="text-neutral-600 max-w-md leading-relaxed">
            Market observation tool — not investment advice, not a trading signal. Data aggregated
            from public sources, provided as-is. Not regulated by BaFin or any financial authority.
          </div>
        </div>
      </footer>
    </div>
  )
}
