import { useState } from 'react'
import { useAuth } from '../context/AuthContext'

const PILLARS = [
  {
    label: '01',
    title: 'Six chokepoints on one map',
    body:
      'Live AIS tanker positions through Hormuz, Suez, Malacca, Panama, Cape, and Houston — aggregated from public feeds you would otherwise have to wire up yourself.',
  },
  {
    label: '02',
    title: 'Read every rule on GitHub',
    body:
      'Every threshold, every anomaly check, every correlation runs in code you can audit. No black-box ML, no proprietary scoring, no "trust us" — just transparent rules over public data.',
  },
  {
    label: '03',
    title: 'Self-host or use the cloud',
    body:
      'Run OBSYD on your own infra with your own API keys (AGPL-3.0), or skip setup and use obsyd.dev. Both paths give the same code; the cloud tier just saves you the ops work.',
  },
]

const STATS = [
  { label: 'tanker positions tracked', value: '3.6M+' },
  { label: 'zone-day events', value: '330+' },
  { label: 'data sources aggregated', value: '13' },
  { label: 'license', value: 'AGPL-3.0' },
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
          OPEN · ENERGY · DATA
        </div>
        <h1 className="text-3xl sm:text-5xl lg:text-6xl text-neutral-100 leading-tight font-mono font-bold mb-6">
          One dashboard for public
          <br />
          <span className="text-cyan-glow">energy market data.</span>
        </h1>
        <p className="text-sm sm:text-base text-neutral-400 max-w-2xl leading-relaxed mb-8">
          OBSYD aggregates live AIS, IMF chokepoint transits, EIA fundamentals, FRED macro, and
          11 other public feeds into one auditable dashboard. Open source under AGPL-3.0 — run it
          yourself, or use the hosted cloud version.
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
          Cloud Free is forever-free; Cloud Pro is €15/month and pays for hosting + alerts. AGPL-3.0 source.
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
          Aggregate. Display. Stay honest.
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
              <li>· IMF PortWatch — chokepoint transits (3–5 day publication lag)</li>
              <li>· EIA — weekly US inventories, refinery utilisation, SPR</li>
              <li>· FRED — daily WTI, Brent, DXY, yields, macro indicators</li>
              <li>· GDELT — energy-keyword news volume + tone</li>
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
              Public data, in one place,
              <br />
              without the terminal price tag.
            </h2>
            <p className="text-[13px] text-neutral-400 leading-relaxed">
              OBSYD doesn't try to match Kpler or Vortexa on proprietary cargo-flow data — it
              can't, and it doesn't pretend to. What it does is aggregate the public feeds that
              are already there (AIS, PortWatch, EIA, FRED, GDELT) into one auditable dashboard,
              so you stop wiring up 13 APIs by hand.
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
              <li>· Developers building energy-data tools (self-host the engine)</li>
              <li>· Researchers and journalists needing one source for context</li>
              <li>· Independent analysts and small funds without Bloomberg seats</li>
              <li>· Anyone who wants to read the signal code, not trust it blindly</li>
            </ul>
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
