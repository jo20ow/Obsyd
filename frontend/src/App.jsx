import { useState, useEffect } from 'react'
import Header from './components/Header'
import CompactView from './components/CompactView'
import PriceChart from './components/PriceChart'
import MacroPanel from './components/MacroPanel'
import SentimentPanel from './components/SentimentPanel'
import VesselMap from './components/VesselMap'
import AlertsPanel from './components/AlertsPanel'
import FundamentalsPanel from './components/FundamentalsPanel'
import JODIPanel from './components/JODIPanel'
import ChokePointMonitor from './components/ChokePointMonitor'
import CorrelationPanel from './components/CorrelationPanel'
import BriefingPanel from './components/BriefingPanel'
import MarketStructure from './components/MarketStructure'
import ReroutingIndex from './components/ReroutingIndex'
import EventTimeline from './components/EventTimeline'
import ZoneActivityChart from './components/ZoneActivityChart'
import VoyagesPanel from './components/VoyagesPanel'
import FlowMatrixPanel from './components/FlowMatrixPanel'
import STSPanel from './components/STSPanel'
import CrackSpreadPanel from './components/CrackSpreadPanel'
import RelatedEquitiesPanel from './components/RelatedEquitiesPanel'
import ProGate from './components/ProGate'
import ErrorBoundary from './components/ErrorBoundary'
import PriceTicker from './components/PriceTicker'
import TransitChart from './components/TransitChart'
import { useAuth } from './context/AuthContext'

const API = '/api'

const TABS = [
  { key: 'overview', label: 'OVERVIEW' },
  { key: 'market', label: 'MARKET' },
  { key: 'signals', label: 'SIGNALS' },
  { key: 'sentiment', label: 'SENTIMENT' },
]

function Disclaimer() {
  return (
    <footer className="mt-4 mb-4 px-4 text-center font-mono text-[9px] text-neutral-700 leading-relaxed max-w-2xl mx-auto">
      OBSYD is an open-source market observation tool. It does not provide investment advice, trading signals, or recommendations. All data is provided as-is for informational purposes only. AIS data is self-reported and unverified. Correlations shown are statistical observations, not causal predictions. Past correlations do not indicate future results. Not regulated by BaFin or any financial authority.
    </footer>
  )
}

function ProBanner() {
  const { isPro } = useAuth()
  if (isPro) return null

  return (
    <div className="border border-cyan-glow/10 bg-cyan-glow/[0.02] rounded px-4 py-2 flex items-center justify-between flex-wrap gap-2">
      <span className="font-mono text-[10px] text-neutral-500">
        <span className="text-cyan-glow/80 font-bold">OBSYD PRO</span>
        <span className="mx-2 text-neutral-700">—</span>
        STS Detection · Crack Spreads · Related Equities · Daily Briefing
        <span className="mx-2 text-neutral-700">—</span>
        <span className="text-neutral-400">€9/mo</span>
      </span>
      <span className="font-mono text-[10px] text-cyan-glow/50 cursor-pointer hover:text-cyan-glow transition-colors">
        LOG IN / SIGN UP
      </span>
    </div>
  )
}

function ProFooter() {
  const { isPro } = useAuth()
  if (isPro) return null

  return (
    <div className="mt-6 border-t border-border pt-4 pb-2">
      <div className="text-center font-mono text-[10px] text-neutral-600">
        <span className="text-cyan-glow/60">OBSYD Pro</span>: STS Detection, Crack Spreads, Related Equities, Daily Briefing Email.{' '}
        <span className="text-neutral-400">€9/month.</span>{' '}
        <span className="text-cyan-glow/40 cursor-pointer hover:text-cyan-glow transition-colors underline underline-offset-2">
          Log in / Sign up
        </span>
      </div>
    </div>
  )
}

function TabBar({ active, onChange }) {
  return (
    <div className="flex items-center gap-0.5 overflow-x-auto scrollbar-hidden border-b border-border">
      {TABS.map((tab) => (
        <button
          key={tab.key}
          onClick={() => onChange(tab.key)}
          className={`font-mono text-[11px] tracking-wider px-4 py-2.5 transition-colors shrink-0 border-b-2 -mb-px ${
            active === tab.key
              ? 'text-cyan-glow border-cyan-glow bg-cyan-glow/5'
              : 'text-neutral-600 hover:text-neutral-400 border-transparent'
          }`}
        >
          {tab.label}
        </button>
      ))}
    </div>
  )
}

function App() {
  const [compactMode, setCompactMode] = useState(false)
  const [eiaData, setEiaData] = useState([])
  const [liveData, setLiveData] = useState(null)
  const [liveSource, setLiveSource] = useState(null)
  const [zones, setZones] = useState([])
  const [aisActive, setAisActive] = useState(false)
  const [gdeltActive, setGdeltActive] = useState(false)
  const [weatherAlerts, setWeatherAlerts] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const [activeTab, setActiveTab] = useState(() => {
    const hash = window.location.hash.replace('#', '')
    return TABS.find((t) => t.key === hash) ? hash : 'overview'
  })

  // URL hash sync
  useEffect(() => {
    window.location.hash = activeTab
  }, [activeTab])

  useEffect(() => {
    const handler = () => {
      const hash = window.location.hash.replace('#', '')
      if (TABS.find((t) => t.key === hash)) setActiveTab(hash)
    }
    window.addEventListener('hashchange', handler)
    return () => window.removeEventListener('hashchange', handler)
  }, [])

  useEffect(() => {
    async function fetchData() {
      try {
        const [eiaRes, zonesRes, liveRes, aisRes, gdeltRes] = await Promise.all([
          fetch(`${API}/prices/eia?limit=500`),
          fetch(`${API}/vessels/zones`),
          fetch(`${API}/prices/live`),
          fetch(`${API}/vessels/positions?limit=1`),
          fetch(`${API}/sentiment/status`),
        ])
        if (!eiaRes.ok) throw new Error(`EIA API: ${eiaRes.status}`)
        if (!zonesRes.ok) throw new Error(`Zones API: ${zonesRes.status}`)

        const [eia, z] = await Promise.all([eiaRes.json(), zonesRes.json()])
        setEiaData(eia)
        setZones(z)

        if (liveRes.ok) {
          const live = await liveRes.json()
          if (live.available) {
            setLiveData(live.prices)
            setLiveSource(live.source || null)
          }
        }

        if (aisRes.ok) {
          const aisData = await aisRes.json()
          setAisActive(aisData.length > 0)
        }

        if (gdeltRes.ok) {
          const gdelt = await gdeltRes.json()
          setGdeltActive(gdelt.active)
        }

        fetch(`${API}/weather/alerts`)
          .then((r) => (r.ok ? r.json() : []))
          .then(setWeatherAlerts)
          .catch((e) => console.error('Weather alerts fetch:', e))
      } catch (e) {
        setError(e.message)
      } finally {
        setLoading(false)
      }
    }
    fetchData()

    const interval = setInterval(() => {
      fetch(`${API}/prices/live`)
        .then((r) => (r.ok ? r.json() : null))
        .then((live) => {
          if (live?.available) {
            setLiveData(live.prices)
            setLiveSource(live.source || null)
          }
        })
        .catch(() => {})
    }, 15 * 60 * 1000)
    return () => clearInterval(interval)
  }, [])

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-cyan-glow font-mono text-sm animate-pulse">
          OBSYD // INITIALIZING ...
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="border border-red-500/30 bg-red-500/5 p-6 max-w-md">
          <div className="text-red-400 font-mono text-xs mb-2">// CONNECTION ERROR</div>
          <div className="text-red-300 font-mono text-sm">{error}</div>
          <div className="text-neutral-500 font-mono text-xs mt-3">
            Ensure backend is running at localhost:8000
          </div>
        </div>
      </div>
    )
  }

  if (compactMode) {
    return <CompactView onSwitchToFull={() => setCompactMode(false)} />
  }

  return (
    <div className="min-h-screen p-3 lg:p-4">
      {/* ===== ALWAYS VISIBLE ===== */}

      {/* HEADER */}
      <Header aisActive={aisActive} gdeltActive={gdeltActive} compactMode={compactMode} onToggleCompact={() => setCompactMode(true)} />

      {/* PRICE TICKER */}
      <div className="mt-3">
        <PriceTicker />
      </div>

      {/* BRIEFING */}
      <ErrorBoundary name="briefing">
        <div className="mt-3">
          <BriefingPanel />
        </div>
      </ErrorBoundary>

      {/* PRO BANNER (compact, once) */}
      <div className="mt-3">
        <ProBanner />
      </div>

      {/* MAP + ALERTS */}
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_340px] gap-3 mt-3">
        <ErrorBoundary name="vessel-map">
          <VesselMap zones={zones} weatherAlerts={weatherAlerts} />
        </ErrorBoundary>
        <div className="lg:max-h-[600px] lg:overflow-y-auto scrollbar-hidden">
          <ErrorBoundary name="alerts">
            <AlertsPanel weatherAlerts={weatherAlerts} />
          </ErrorBoundary>
        </div>
      </div>

      {/* ===== TAB NAVIGATION ===== */}
      <div className="mt-4">
        <TabBar active={activeTab} onChange={setActiveTab} />
      </div>

      {/* ===== TAB CONTENT ===== */}
      <div className="mt-3">

        {/* OVERVIEW TAB */}
        {activeTab === 'overview' && (
          <>
            {/* Row 1: Chokepoint Monitor */}
            <ErrorBoundary name="chokepoint-monitor">
              <ChokePointMonitor />
            </ErrorBoundary>

            {/* Row 2: Rerouting + Historical Anomalies */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mt-3">
              <ErrorBoundary name="rerouting">
                <ReroutingIndex />
              </ErrorBoundary>
              <ErrorBoundary name="event-timeline">
                <EventTimeline />
              </ErrorBoundary>
            </div>

            {/* Row 3: EIA Fundamentals */}
            <ErrorBoundary name="fundamentals">
              <div className="mt-3">
                <FundamentalsPanel />
              </div>
            </ErrorBoundary>
          </>
        )}

        {/* MARKET TAB */}
        {activeTab === 'market' && (
          <>
            {/* Row 1: Price Chart + Futures/Macro */}
            <div className="grid grid-cols-1 lg:grid-cols-[3fr_2fr] gap-3">
              <ErrorBoundary name="price-chart">
                <PriceChart data={eiaData} live={liveData} />
              </ErrorBoundary>
              <div className="space-y-3">
                <ErrorBoundary name="market-structure">
                  <MarketStructure />
                </ErrorBoundary>
                <ErrorBoundary name="macro">
                  <MacroPanel />
                </ErrorBoundary>
              </div>
            </div>

            {/* Row 2: Fundamentals + JODI */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mt-3">
              <ErrorBoundary name="fundamentals-market">
                <FundamentalsPanel />
              </ErrorBoundary>
              <ErrorBoundary name="jodi">
                <JODIPanel />
              </ErrorBoundary>
            </div>

            {/* Row 3: Correlation Heatmap */}
            <ErrorBoundary name="correlation">
              <div className="mt-3">
                <CorrelationPanel />
              </div>
            </ErrorBoundary>
          </>
        )}

        {/* SIGNALS TAB */}
        {activeTab === 'signals' && (
          <>
            {/* Row 1: Zone Activity Chart */}
            <ErrorBoundary name="zone-activity">
              <ZoneActivityChart />
            </ErrorBoundary>

            {/* Row 2: Transit History + Voyages/Flow */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mt-3">
              <ErrorBoundary name="transit-chart">
                <TransitChart />
              </ErrorBoundary>
              <div className="space-y-3">
                <ErrorBoundary name="voyages">
                  <VoyagesPanel />
                </ErrorBoundary>
                <ErrorBoundary name="flow-matrix">
                  <FlowMatrixPanel />
                </ErrorBoundary>
              </div>
            </div>

            {/* Row 3: STS + Rerouting + Crack Spread */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-3 mt-3">
              <ErrorBoundary name="sts-detection">
                <ProGate feature="STS Detection">
                  <STSPanel />
                </ProGate>
              </ErrorBoundary>
              <ErrorBoundary name="rerouting-signals">
                <ReroutingIndex />
              </ErrorBoundary>
              <ErrorBoundary name="crack-spread">
                <ProGate feature="Crack Spread">
                  <CrackSpreadPanel />
                </ProGate>
              </ErrorBoundary>
            </div>
          </>
        )}

        {/* SENTIMENT TAB */}
        {activeTab === 'sentiment' && (
          <>
            {/* Row 1: GDELT Sentiment (full width) */}
            <ErrorBoundary name="sentiment">
              <SentimentPanel />
            </ErrorBoundary>

            {/* Row 2: Related Equities [PRO] */}
            <ErrorBoundary name="related-equities">
              <div className="mt-3">
                <ProGate feature="Related Equities">
                  <RelatedEquitiesPanel />
                </ProGate>
              </div>
            </ErrorBoundary>
          </>
        )}
      </div>

      {/* ===== FOOTER ===== */}
      <ProFooter />
      <Disclaimer />
    </div>
  )
}

export default App
