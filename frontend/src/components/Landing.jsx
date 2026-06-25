import { useState } from 'react'
import { useAuth } from '../context/AuthContext'

const PILLARS = [
  {
    label: '01',
    title: 'See who controls supply',
    body:
      'Supply concentration (HHI) for seven strategic materials — rare earths, cobalt, lithium, nickel, copper, oil, gas. Top-producer share, top-3 breakdown, and a fragility tier from EXTREME to DIVERSIFIED. Rare earths and cobalt are dominated by a single country; the map shows exactly how much.',
  },
  {
    label: '02',
    title: 'Catch disruptions as they happen',
    body:
      'A live radar of ten descriptive detectors flags physical supply anomalies — chokepoint transit drops, Suez→Cape rerouting, floating-storage build-ups, gas/power imbalance — the moment they deviate from their own history. A deviation vs history, not a forecast.',
  },
  {
    label: '03',
    title: 'Read every rule on GitHub',
    body:
      'Every threshold, every anomaly check runs in code you can audit. No black-box ML, no proprietary scoring, no "trust us". Run OBSYD on your own infra (AGPL-3.0), or skip the ops and use obsyd.dev — same code either way.',
  },
]

const STATS = [
  { label: 'strategic materials tracked', value: '7' },
  { label: 'live anomaly detectors', value: '10' },
  { label: 'official public-domain data', value: '100%' },
  { label: 'license', value: 'AGPL-3.0' },
]

export default function Landing() {
  const { openPricing, user } = useAuth()
  const [glanceOpen, setGlanceOpen] = useState(false)

  return (
    <div className="min-h-screen bg-[#06060a] text-neutral-300 font-mono">
      {/* TOP NAV */}
      <header className="border-b border-border">
        <div className="max-w-5xl mx-auto px-4 py-3 flex items-center justify-between">
          <a href="/" className="text-cyan-glow text-[13px] tracking-[4px] font-bold">
            OBSYD
          </a>
          <nav className="flex items-center gap-4 text-[10px] tracking-wider text-neutral-500">
            <a href="#how" className="hover:text-neutral-200 hidden sm:inline">
              HOW IT WORKS
            </a>
            <a href="#pricing" className="hover:text-neutral-200">
              PRICING
            </a>
            <a
              href="https://github.com/jo20ow/Obsyd"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-neutral-200 hidden sm:inline"
            >
              GITHUB
            </a>
            <a href="/app" className="hover:text-cyan-glow">
              {user ? 'OPEN APP →' : 'LIVE DEMO →'}
            </a>
          </nav>
        </div>
      </header>

      {/* HERO */}
      <section className="px-4 py-12 sm:py-20 max-w-5xl mx-auto">
        <div className="text-[10px] tracking-[4px] text-cyan-glow mb-4">
          CRITICAL MATERIALS · ENERGY SECURITY
        </div>
        <h1 className="text-3xl sm:text-5xl lg:text-6xl text-neutral-100 leading-tight font-mono font-bold mb-6">
          Who controls critical supply —
          <br />
          <span className="text-cyan-glow">and when it breaks.</span>
        </h1>
        <p className="text-sm sm:text-base text-neutral-400 max-w-2xl leading-relaxed mb-8">
          OBSYD tracks supply concentration for seven strategic materials from official
          public-domain data (USGS · EIA · ENTSO-E), and flags physical supply disruptions the
          moment they deviate from history. Descriptive, auditable, open source under AGPL-3.0 —
          run it yourself, or use the hosted cloud.
        </p>

        <div className="flex flex-col sm:flex-row gap-3">
          <a
            href="/app"
            className="px-6 py-3 text-[11px] tracking-wider bg-cyan-glow text-[#0a0a12] hover:bg-cyan-glow/90 transition-colors font-semibold text-center"
          >
            Open the live dashboard →
          </a>
          <a
            href="https://github.com/jo20ow/Obsyd"
            target="_blank"
            rel="noopener noreferrer"
            className="px-6 py-3 text-[11px] tracking-wider border border-cyan-glow/40 text-cyan-glow hover:bg-cyan-glow/10 transition-colors text-center"
          >
            Self-host on GitHub
          </a>
        </div>

        <p className="mt-6 text-[10px] text-neutral-600">
          Cloud Free is the full live map, forever-free. Cloud Pro adds disruption alerts + a daily brief — €15/month. AGPL-3.0 source.
        </p>
      </section>

      {/* STATS STRIP */}
      <section className="border-y border-border bg-[#0a0a12]">
        <div className="max-w-5xl mx-auto px-4 py-6 grid grid-cols-2 sm:grid-cols-4 gap-6">
          {STATS.map((s) => (
            <div key={s.label}>
              <div className="text-2xl sm:text-3xl text-cyan-glow mb-1">{s.value}</div>
              <div className="text-[10px] tracking-wider text-neutral-600 uppercase">
                {s.label}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* HOW IT WORKS */}
      <section id="how" className="px-4 py-14 sm:py-20 max-w-5xl mx-auto">
        <div className="text-[10px] tracking-[3px] text-neutral-500 mb-3">// HOW IT WORKS</div>
        <h2 className="text-2xl sm:text-3xl text-neutral-100 mb-10 font-bold">
          Track concentration. Catch disruption. Stay honest.
        </h2>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-px bg-border">
          {PILLARS.map((p) => (
            <div key={p.label} className="bg-[#06060a] p-6">
              <div className="text-cyan-glow text-[11px] tracking-widest mb-3">{p.label}</div>
              <div className="text-neutral-100 text-base mb-3 leading-snug">{p.title}</div>
              <div className="text-[12px] text-neutral-500 leading-relaxed">{p.body}</div>
            </div>
          ))}
        </div>

        <div className="mt-10 border border-border bg-[#0a0a12] p-5 text-[11px] text-neutral-500 leading-relaxed">
          <button
            type="button"
            onClick={() => setGlanceOpen((v) => !v)}
            className="text-cyan-glow text-[10px] tracking-wider hover:underline mb-3"
          >
            {glanceOpen ? '− HIDE' : '+ WHAT DATA EXACTLY?'}
          </button>
          {glanceOpen && (
            <ul className="space-y-1.5 mt-2">
              <li>· USGS Mineral Commodity Summaries — mine production &amp; supply concentration (public domain)</li>
              <li>· EIA International — per-country oil &amp; gas production (public domain)</li>
              <li>· ENTSO-E / SMARD — European power: load, generation mix, day-ahead prices</li>
              <li>· World Bank — macro context for 200+ countries (CC BY 4.0)</li>
              <li>· IMF PortWatch — chokepoint transits (3–5 day publication lag)</li>
              <li>· AISStream + AISHub — live vessel positions through the six chokepoints</li>
              <li>· GDELT, NOAA, JODI, NASA FIRMS, Open-Meteo — news tone, weather, balances</li>
            </ul>
          )}
        </div>
      </section>

      {/* DIFFERENTIATION */}
      <section className="border-y border-border bg-[#0a0a12]">
        <div className="max-w-5xl mx-auto px-4 py-14 sm:py-20 grid grid-cols-1 md:grid-cols-2 gap-10">
          <div>
            <div className="text-[10px] tracking-[3px] text-neutral-500 mb-3">// WHY OBSYD</div>
            <h2 className="text-2xl text-neutral-100 mb-5 font-bold leading-snug">
              The official public record,
              <br />
              turned into a supply-risk desk.
            </h2>
            <p className="text-[13px] text-neutral-400 leading-relaxed">
              OBSYD doesn&apos;t match Kpler or a Bloomberg terminal on proprietary cargo data — it
              can&apos;t, and it doesn&apos;t pretend to. What it does is turn the official public
              record (USGS, EIA, ENTSO-E, World Bank) into a critical-materials &amp;
              energy-security dashboard, and watch it for you — so you stop wiring up a dozen APIs
              by hand.
            </p>
          </div>
          <div className="border border-border bg-[#06060a] p-5 text-[11px] text-neutral-500 leading-relaxed">
            <div className="text-cyan-glow text-[10px] tracking-wider mb-3">// NOT FOR</div>
            <ul className="space-y-2">
              <li>· Anyone needing real-time intraday trading signals</li>
              <li>· Tier-1 trading desks already paying for Kpler / Vortexa</li>
              <li>· Anyone needing audited regulatory-grade pricing</li>
            </ul>
            <div className="text-cyan-glow text-[10px] tracking-wider mt-5 mb-3">// MADE FOR</div>
            <ul className="space-y-2">
              <li>· Procurement &amp; supply-chain teams tracking critical-materials exposure</li>
              <li>· Commodity- and energy-risk analysts without a Bloomberg seat</li>
              <li>· Researchers and journalists needing one honest source for context</li>
              <li>· Anyone who wants to read the signal code, not trust it blindly</li>
            </ul>
          </div>
        </div>
      </section>

      {/* YOUR SUPPLY-WATCH (the payable product, honest to what ships today) */}
      <section className="px-4 py-14 sm:py-20 max-w-5xl mx-auto">
        <div className="text-[10px] tracking-[3px] text-neutral-500 mb-3">// YOUR SUPPLY-WATCH</div>
        <h2 className="text-2xl sm:text-3xl text-neutral-100 mb-5 font-bold">
          The map is free. <span className="text-cyan-glow">Pro watches it for you.</span>
        </h2>
        <p className="text-[13px] text-neutral-400 leading-relaxed max-w-2xl mb-10">
          You shouldn&apos;t have to refresh six tabs to know when supply breaks. Pro turns the
          radar into your inbox — set the alerts that matter, and OBSYD pings you with the evidence
          the moment something deviates.
        </p>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-px bg-border">
          <div className="bg-[#0a0a12] p-6">
            <div className="text-cyan-glow text-[11px] tracking-widest mb-3">01</div>
            <div className="text-neutral-100 text-base mb-3 leading-snug">Set your alerts</div>
            <div className="text-[12px] text-neutral-500 leading-relaxed">
              Choose the supply disruptions that matter to you — chokepoint transit drops,
              floating-storage build-ups, spread breaches — with your own thresholds.
            </div>
          </div>
          <div className="bg-[#0a0a12] p-6">
            <div className="text-cyan-glow text-[11px] tracking-widest mb-3">02</div>
            <div className="text-neutral-100 text-base mb-3 leading-snug">We watch the radar</div>
            <div className="text-[12px] text-neutral-500 leading-relaxed">
              Every rule is re-checked against its own history around the clock. A cooldown keeps
              it to real moves, not false-alarm spam.
            </div>
          </div>
          <div className="bg-[#0a0a12] p-6">
            <div className="text-cyan-glow text-[11px] tracking-widest mb-3">03</div>
            <div className="text-neutral-100 text-base mb-3 leading-snug">You get the email</div>
            <div className="text-[12px] text-neutral-500 leading-relaxed">
              The trigger, the evidence, and a link straight to the chart — plus a Mon–Fri daily
              brief so your morning starts with the lay of the land.
            </div>
          </div>
        </div>
      </section>

      {/* PRICING SNIPPET */}
      <section id="pricing" className="px-4 py-14 sm:py-20 max-w-5xl mx-auto">
        <div className="text-[10px] tracking-[3px] text-neutral-500 mb-3">// PRICING</div>
        <h2 className="text-2xl sm:text-3xl text-neutral-100 mb-8 font-bold">
          Self-host free.{' '}
          <span className="text-cyan-glow">Cloud €15/month</span> if you skip the setup.
        </h2>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-px bg-border max-w-4xl">
          <div className="bg-[#0a0a12] p-6">
            <div className="text-[10px] tracking-widest text-neutral-500 mb-2">SELF-HOST</div>
            <div className="text-3xl text-neutral-200 mb-1">€0</div>
            <div className="text-[10px] text-neutral-600 mb-5">AGPL-3.0 · your infra</div>
            <ul className="text-[11px] text-neutral-400 space-y-1.5">
              <li>· Full feature set</li>
              <li>· Bring your own API keys</li>
              <li>· No usage limits</li>
              <li>· You handle updates + ops</li>
            </ul>
          </div>
          <div className="bg-[#0a0a12] p-6">
            <div className="text-[10px] tracking-widest text-neutral-500 mb-2">CLOUD FREE</div>
            <div className="text-3xl text-neutral-200 mb-1">€0</div>
            <div className="text-[10px] text-neutral-600 mb-5">on obsyd.dev · no card</div>
            <ul className="text-[11px] text-neutral-400 space-y-1.5">
              <li>· Full live dashboard</li>
              <li>· 30-day history window</li>
              <li>· Up to 3 saved alerts</li>
              <li>· No API access, no exports</li>
            </ul>
          </div>
          <div className="bg-[#0a0a12] p-6 relative">
            <div className="absolute top-3 right-3 text-[9px] tracking-[2px] text-cyan-glow bg-cyan-glow/10 px-2 py-0.5 border border-cyan-glow/30 rounded-sm">
              RECOMMENDED
            </div>
            <div className="text-[10px] tracking-widest text-cyan-glow mb-2">CLOUD PRO</div>
            <div className="text-3xl text-neutral-100 mb-1">
              €15<span className="text-sm text-neutral-500">/month</span>
            </div>
            <div className="text-[10px] text-neutral-600 mb-5">or €149/year (−17%)</div>
            <ul className="text-[11px] text-neutral-300 space-y-1.5">
              <li>+ Full history (back to 2019)</li>
              <li>+ Unlimited saved alerts</li>
              <li>+ API access (rate-limited)</li>
              <li>+ CSV / JSON data export</li>
              <li>+ Daily email brief (Mon–Fri)</li>
            </ul>
          </div>
        </div>

        <div className="mt-8 flex flex-col sm:flex-row gap-3">
          <button
            type="button"
            onClick={openPricing}
            className="px-6 py-3 text-[11px] tracking-wider bg-cyan-glow text-[#0a0a12] hover:bg-cyan-glow/90 transition-colors font-semibold"
          >
            See full pricing →
          </button>
          <a
            href="/app"
            className="px-6 py-3 text-[11px] tracking-wider border border-border text-neutral-400 hover:text-cyan-glow hover:border-cyan-glow/40 transition-colors text-center"
          >
            Try the dashboard first
          </a>
        </div>
      </section>

      {/* FOOTER */}
      <footer className="border-t border-border bg-[#0a0a12]">
        <div className="max-w-5xl mx-auto px-4 py-8 flex flex-col sm:flex-row gap-4 justify-between items-start sm:items-center text-[10px] text-neutral-600">
          <div>
            OBSYD is open source under AGPL-3.0. Source on{' '}
            <a
              href="https://github.com/jo20ow/Obsyd"
              target="_blank"
              rel="noopener noreferrer"
              className="text-cyan-glow hover:underline"
            >
              GitHub
            </a>
            .
          </div>
          <div className="text-neutral-700 max-w-md leading-relaxed">
            Market observation tool — not investment advice, not a trading signal. Data aggregated
            from public sources, provided as-is. Not regulated by BaFin or any financial authority.
          </div>
        </div>
      </footer>
    </div>
  )
}
