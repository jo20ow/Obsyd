import { useState } from 'react'
import { useAuth } from '../context/AuthContext'

const PILLARS = [
  {
    label: '01',
    title: 'See the power situation at a glance',
    body:
      'Day-ahead price, residual load, spark spread and grid stress for DE-LU, FR and NL — joined into one descriptive top-line that reads whether the market is calm, elevated or stressed. The charts below are the evidence, not six tabs to reconcile by hand.',
  },
  {
    label: '02',
    title: 'Catch grid stress as it happens',
    body:
      'A live radar flags negative prices, Dunkelflaute (wind+solar below 15% of load) and day-ahead anomalies the moment they deviate from each zone’s own history. A deviation vs history, not a forecast.',
  },
  {
    label: '03',
    title: 'Read every rule on GitHub',
    body:
      'Every threshold, every anomaly check runs in code you can audit. No black-box ML, no proprietary scoring, no "trust us". Run OBSYD on your own infra (AGPL-3.0), or skip the ops and use obsyd.dev — same code either way.',
  },
]

const STATS = [
  { label: 'bidding zones (DE-LU · FR · NL)', value: '3' },
  { label: 'cross-border flows tracked', value: '20' },
  { label: 'official public-domain data', value: '100%' },
  { label: 'license', value: 'AGPL-3.0' },
]

export default function Landing() {
  const { user } = useAuth()
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
          EUROPEAN POWER DESK
        </div>
        <h1 className="text-3xl sm:text-5xl lg:text-6xl text-neutral-100 leading-tight font-mono font-bold mb-6">
          The European power market —
          <br />
          <span className="text-cyan-glow">from the official record.</span>
        </h1>
        <p className="text-sm sm:text-base text-neutral-400 max-w-2xl leading-relaxed mb-8">
          OBSYD turns the official European power record (ENTSO-E · Fraunhofer Energy-Charts) into one
          desk: day-ahead prices with negative-price flags, residual load &amp; Dunkelflaute, spark
          spreads and cross-border flows for DE-LU, FR and NL — joined into a single read of whether
          the market is calm, elevated or stressed. Descriptive, auditable, open source under
          AGPL-3.0 — run it yourself, or use the hosted cloud.
        </p>

        <div className="flex flex-col sm:flex-row gap-3">
          <a
            href="/app"
            className="px-6 py-3 text-[11px] tracking-wider bg-cyan-glow text-[#0a0a12] hover:bg-cyan-glow/90 transition-colors font-semibold text-center"
          >
            Open the live desk →
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
          Free and open source (AGPL-3.0). Everything unlocked — no paywall, no account needed to explore.
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
          See the situation. Catch the stress. Stay honest.
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
              <li>· ENTSO-E — day-ahead prices, load &amp; generation mix for DE-LU, FR, NL</li>
              <li>· Fraunhofer Energy-Charts — cross-border physical flows (CC BY 4.0)</li>
              <li>· TTF / NG / Brent — the fuel prices that set the marginal power price</li>
              <li>· USGS / EIA International / World Bank — supply concentration &amp; macro context</li>
              <li>· IMF PortWatch, AISStream, GDELT, NOAA, NASA FIRMS — the wider engine behind the desk</li>
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
              The official power record,
              <br />
              turned into a desk.
            </h2>
            <p className="text-[13px] text-neutral-400 leading-relaxed">
              OBSYD doesn&apos;t match Montel, EEX or a Bloomberg terminal on proprietary or intraday
              data — it can&apos;t, and it doesn&apos;t pretend to. What it does is turn the official
              European power record (ENTSO-E, Fraunhofer Energy-Charts) into one auditable desk —
              day-ahead, residual load, spark spread, cross-border flows — and watch it for you, so
              you stop wiring up a dozen APIs by hand.
            </p>
          </div>
          <div className="border border-border bg-[#06060a] p-5 text-[11px] text-neutral-500 leading-relaxed">
            <div className="text-cyan-glow text-[10px] tracking-wider mb-3">// NOT FOR</div>
            <ul className="space-y-2">
              <li>· Intraday or real-time trade execution</li>
              <li>· Desks already paying for Montel / EEX / Bloomberg</li>
              <li>· Anyone needing settlement-grade or pan-EU-complete pricing</li>
            </ul>
            <div className="text-cyan-glow text-[10px] tracking-wider mt-5 mb-3">// MADE FOR</div>
            <ul className="space-y-2">
              <li>· Power traders &amp; energy-risk analysts without a Montel/Bloomberg seat</li>
              <li>· Energy desks at utilities &amp; industrials tracking spreads and grid stress</li>
              <li>· Researchers and journalists needing one honest source for the power picture</li>
              <li>· Anyone who wants to read the signal code, not trust it blindly</li>
            </ul>
          </div>
        </div>
      </section>

      {/* YOUR POWER WATCH (the recurring deliverable, honest to what ships today) */}
      <section className="px-4 py-14 sm:py-20 max-w-5xl mx-auto">
        <div className="text-[10px] tracking-[3px] text-neutral-500 mb-3">// YOUR POWER WATCH</div>
        <h2 className="text-2xl sm:text-3xl text-neutral-100 mb-5 font-bold">
          Don&apos;t watch the desk. <span className="text-cyan-glow">Let it watch for you.</span>
        </h2>
        <p className="text-[13px] text-neutral-400 leading-relaxed max-w-2xl mb-10">
          You shouldn&apos;t have to refresh six tabs to know when the power market moves. OBSYD turns
          the radar into your inbox — set the alerts that matter, and it pings you with the evidence
          the moment a zone deviates. Free, like the rest of it.
        </p>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-px bg-border">
          <div className="bg-[#0a0a12] p-6">
            <div className="text-cyan-glow text-[11px] tracking-widest mb-3">01</div>
            <div className="text-neutral-100 text-base mb-3 leading-snug">Set your alerts</div>
            <div className="text-[12px] text-neutral-500 leading-relaxed">
              Choose the power anomalies that matter to you — negative prices, Dunkelflaute,
              day-ahead spikes, spark-spread breaches — with your own thresholds.
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
              brief so your morning starts with the power situation.
            </div>
          </div>
        </div>
      </section>

      {/* PRICING → it's all free */}
      <section id="pricing" className="px-4 py-14 sm:py-20 max-w-5xl mx-auto">
        <div className="text-[10px] tracking-[3px] text-neutral-500 mb-3">// PRICING</div>
        <h2 className="text-2xl sm:text-3xl text-neutral-100 mb-8 font-bold">
          It&apos;s <span className="text-cyan-glow">free</span>. All of it.
        </h2>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-px bg-border max-w-3xl">
          <div className="bg-[#0a0a12] p-6">
            <div className="text-[10px] tracking-widest text-neutral-500 mb-2">CLOUD</div>
            <div className="text-3xl text-neutral-200 mb-1">€0</div>
            <div className="text-[10px] text-neutral-600 mb-5">on obsyd.dev · no card, no account needed</div>
            <ul className="text-[11px] text-neutral-400 space-y-1.5">
              <li>· Full power desk + anomaly radar</li>
              <li>· Day-ahead, residual load, spark spread, cross-border flows</li>
              <li>· Watchlist, custom alerts, daily brief</li>
              <li>· Everything unlocked, no limits</li>
            </ul>
          </div>
          <div className="bg-[#0a0a12] p-6">
            <div className="text-[10px] tracking-widest text-neutral-500 mb-2">SELF-HOST</div>
            <div className="text-3xl text-neutral-200 mb-1">€0</div>
            <div className="text-[10px] text-neutral-600 mb-5">AGPL-3.0 · your infra, your keys</div>
            <ul className="text-[11px] text-neutral-400 space-y-1.5">
              <li>· The exact same code, end to end</li>
              <li>· Bring your own API keys</li>
              <li>· No usage limits</li>
              <li>· You handle updates + ops</li>
            </ul>
          </div>
        </div>

        <div className="mt-8 flex flex-col sm:flex-row gap-3">
          <a
            href="/app"
            className="px-6 py-3 text-[11px] tracking-wider bg-cyan-glow text-[#0a0a12] hover:bg-cyan-glow/90 transition-colors font-semibold text-center"
          >
            Open the desk →
          </a>
          <a
            href="https://github.com/jo20ow/Obsyd"
            target="_blank"
            rel="noopener noreferrer"
            className="px-6 py-3 text-[11px] tracking-wider border border-border text-neutral-400 hover:text-cyan-glow hover:border-cyan-glow/40 transition-colors text-center"
          >
            Self-host on GitHub
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
