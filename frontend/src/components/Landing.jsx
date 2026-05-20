import { useState } from 'react'
import { useAuth } from '../context/AuthContext'

const PILLARS = [
  {
    label: '01',
    title: 'Live AIS, six chokepoints',
    body:
      'Real-time tanker positions through Hormuz, Suez, Malacca, Panama, Cape, and Houston — without a Bloomberg seat. Floating storage and ship-to-ship transfers flagged automatically.',
  },
  {
    label: '02',
    title: 'Auditable signal engine',
    body:
      "Every chokepoint anomaly, flow alert, and correlation is computed by code you can read on GitHub. No black-box ML — Pearson with lag optimisation, transparent thresholds, traceable rules.",
  },
  {
    label: '03',
    title: 'Daily briefing in your inbox',
    body:
      'Mon–Fri 07:00 UTC, before European open: the overnight anomalies, the correlations that moved, the rerouting that the market hasn\'t priced in yet.',
  },
]

const STATS = [
  { label: 'tanker positions tracked', value: '3.6M+' },
  { label: 'zone-day events', value: '330+' },
  { label: 'data sources aggregated', value: '13' },
  { label: 'open-source', value: 'MIT' },
]

export default function Landing() {
  const { openPricing, user, isPro } = useAuth()
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
            <a href="/app" className="hover:text-cyan-glow">
              {user ? 'OPEN APP →' : 'LIVE DEMO →'}
            </a>
          </nav>
        </div>
      </header>

      {/* HERO */}
      <section className="px-4 py-12 sm:py-20 max-w-5xl mx-auto">
        <div className="text-[10px] tracking-[4px] text-cyan-glow mb-4">
          ENERGY · MARKET · INTELLIGENCE
        </div>
        <h1 className="text-3xl sm:text-5xl lg:text-6xl text-neutral-100 leading-tight font-mono font-bold mb-6">
          See physical oil flow
          <br />
          <span className="text-cyan-glow">before the price moves.</span>
        </h1>
        <p className="text-sm sm:text-base text-neutral-400 max-w-2xl leading-relaxed mb-8">
          OBSYD aggregates live AIS, IMF chokepoint data, EIA fundamentals, and FRED macro
          into a single dashboard — with a transparent signal engine you can read on GitHub.
          Built for energy analysts who can't justify a Kpler or Bloomberg seat.
        </p>

        <div className="flex flex-col sm:flex-row gap-3">
          <a
            href="/app"
            className="px-6 py-3 text-[11px] tracking-wider bg-cyan-glow text-[#0a0a12] hover:bg-cyan-glow/90 transition-colors font-semibold text-center"
          >
            Open the live dashboard →
          </a>
          {!isPro && (
            <button
              type="button"
              onClick={openPricing}
              className="px-6 py-3 text-[11px] tracking-wider border border-cyan-glow/40 text-cyan-glow hover:bg-cyan-glow/10 transition-colors text-center"
            >
              Start 14-day Pro trial
            </button>
          )}
        </div>

        <p className="mt-6 text-[10px] text-neutral-600">
          No card required for the trial. Cancel any time. EU-VAT-handled via Lemon Squeezy.
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
          One dashboard, three layers,
          <br />
          zero black boxes.
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
              <li>· AISStream WebSocket + AISHub HTTP — live vessel positions</li>
              <li>· IMF PortWatch — chokepoint transits, disruptions</li>
              <li>· EIA — weekly US inventories, refinery utilisation, SPR</li>
              <li>· FRED — daily WTI, Brent, DXY, yields, macro indicators</li>
              <li>· GDELT — energy-keyword news volume + sentiment</li>
              <li>· NOAA — Gulf weather alerts, marine forecasts</li>
              <li>· JODI, NASA FIRMS, Alpha Vantage, Finnhub, Open-Meteo</li>
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
              Kpler is great. It also costs €30k/seat.
            </h2>
            <p className="text-[13px] text-neutral-400 leading-relaxed">
              OBSYD doesn't try to compete with institutional terminals on cargo-flow ML.
              It gives independent analysts, smaller trading desks, and energy journalists
              the 80/20 signal — built on the same public data sources, with the signal
              code in the open so you can audit every alert before you act on it.
            </p>
          </div>
          <div className="border border-border bg-[#06060a] p-5 text-[11px] text-neutral-500 leading-relaxed">
            <div className="text-cyan-glow text-[10px] tracking-wider mb-3">// NOT FOR</div>
            <ul className="space-y-2">
              <li>· Tier-1 integrated oil companies with in-house AIS</li>
              <li>· Day traders who need sub-second tick data</li>
              <li>· Anyone needing audited regulatory-grade pricing</li>
            </ul>
            <div className="text-cyan-glow text-[10px] tracking-wider mt-5 mb-3">// MADE FOR</div>
            <ul className="space-y-2">
              <li>· Energy analysts at mid-size trading shops</li>
              <li>· Commodity hedge-fund researchers</li>
              <li>· Energy journalists and policy analysts</li>
              <li>· Academic researchers in commodity markets</li>
            </ul>
          </div>
        </div>
      </section>

      {/* PRICING SNIPPET */}
      <section id="pricing" className="px-4 py-14 sm:py-20 max-w-5xl mx-auto">
        <div className="text-[10px] tracking-[3px] text-neutral-500 mb-3">// PRICING</div>
        <h2 className="text-2xl sm:text-3xl text-neutral-100 mb-8 font-bold">
          Free dashboard.{' '}
          <span className="text-cyan-glow">€19,90/month</span> for the briefing & alerts.
        </h2>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-px bg-border max-w-3xl">
          <div className="bg-[#0a0a12] p-6">
            <div className="text-[10px] tracking-widest text-neutral-500 mb-2">FREE</div>
            <div className="text-3xl text-neutral-200 mb-1">€0</div>
            <div className="text-[10px] text-neutral-600 mb-5">forever — no card</div>
            <ul className="text-[11px] text-neutral-400 space-y-1.5">
              <li>· Full live dashboard</li>
              <li>· Chokepoint transit data</li>
              <li>· Weekly market briefing</li>
            </ul>
          </div>
          <div className="bg-[#0a0a12] p-6">
            <div className="text-[10px] tracking-widest text-cyan-glow mb-2">PRO</div>
            <div className="text-3xl text-neutral-100 mb-1">
              €19,90<span className="text-sm text-neutral-500">/Monat</span>
            </div>
            <div className="text-[10px] text-neutral-600 mb-5">14-day trial, no card</div>
            <ul className="text-[11px] text-neutral-300 space-y-1.5">
              <li>+ Daily briefing email Mon–Fri</li>
              <li>+ Floating storage & STS alerts</li>
              <li>+ Crack spreads & equity overlay</li>
              <li>+ Custom flow-anomaly alerts</li>
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
            OBSYD is open source under MIT. Source on{' '}
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
            Market observation tool — not investment advice. Data aggregated from public
            sources, provided as-is. Not regulated by BaFin or any financial authority.
          </div>
        </div>
      </footer>
    </div>
  )
}
