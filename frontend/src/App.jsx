import { useState, useEffect } from 'react'
import Header from './components/Header'
import CompactView from './components/CompactView'
import PriceChart from './components/PriceChart'
import MacroPanel from './components/MacroPanel'
import SentimentPanel from './components/SentimentPanel'
import VesselMap from './components/VesselMap'
import AtlasMap from './components/AtlasMap'
import CriticalMaterialsView from './components/CriticalMaterialsView'
import AlertsPanel from './components/AlertsPanel'
import FundamentalsPanel from './components/FundamentalsPanel'
import JODIPanel from './components/JODIPanel'
import ChokePointMonitor, { DisruptionBanner } from './components/ChokePointMonitor'
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
import PricingModal from './components/PricingModal'
import ErrorBoundary from './components/ErrorBoundary'
import PriceTicker from './components/PriceTicker'
import TransitChart from './components/TransitChart'
import TonneMilesPanel from './components/TonneMilesPanel'
import DisruptionScorePanel from './components/DisruptionScorePanel'
import MarketReportPanel from './components/MarketReportPanel'
import EIAPredictionPanel, { EIAPredictionMini } from './components/EIAPredictionPanel'
import FreightProxyPanel from './components/FreightProxyPanel'
import AlertRulesPanel from './components/AlertRulesPanel'
import GasBalancePanel from './components/GasBalancePanel'
import GasStoragePanel from './components/GasStoragePanel'
import GasSupplyPanel from './components/GasSupplyPanel'
import GasDemandPanel from './components/GasDemandPanel'
import PowerDayAheadPanel from './components/PowerDayAheadPanel'
import PowerGridPanel from './components/PowerGridPanel'
import SparkSpreadPanel from './components/SparkSpreadPanel'
import GenerationMixPanel from './components/GenerationMixPanel'
import CrossBorderFlowPanel from './components/CrossBorderFlowPanel'
import CopperPanel from './components/CopperPanel'
import ZoneSelector from './components/ZoneSelector'
import Landing from './components/Landing'
import { useAuth } from './context/AuthContext'

const API = '/api'

const TABS = [
  { key: 'critical', label: 'CRITICAL' },
  { key: 'overview', label: 'OVERVIEW' },
  { key: 'market', label: 'MARKET' },
  { key: 'signals', label: 'SIGNALS' },
  { key: 'gas', label: 'GAS' },
  { key: 'energy', label: 'ENERGY' },
  { key: 'metals', label: 'METALS' },
  { key: 'atlas', label: 'ATLAS' },
  { key: 'sentiment', label: 'SENTIMENT' },
  { key: 'alerts', label: 'ALERTS' },
]

function Disclaimer() {
  return (
    <footer className="mt-4 mb-4 px-4 text-center font-mono text-[9px] text-neutral-700 leading-relaxed max-w-2xl mx-auto">
      OBSYD is an open-source market observation tool. It does not provide investment advice, trading signals, or recommendations. All data is provided as-is for informational purposes only. AIS data is self-reported and unverified. Correlations shown are statistical observations, not causal predictions. Past correlations do not indicate future results. Not regulated by BaFin or any financial authority.
    </footer>
  )
}

function ProBanner() {
  const { isPro, openPricing } = useAuth()
  if (isPro) return null

  return (
    <div className="border border-cyan-glow/10 bg-cyan-glow/[0.02] rounded px-4 py-2 flex items-center justify-between flex-wrap gap-2">
      <span className="font-mono text-[10px] text-neutral-500">
        OBSYD is open source and free to use. Pro adds daily briefings, custom alerts, and deep-dive panels.
      </span>
      <button
        type="button"
        onClick={openPricing}
        className="font-mono text-[10px] tracking-wider text-cyan-glow hover:text-cyan-glow/80 underline-offset-2 hover:underline"
      >
        See Pro →
      </button>
    </div>
  )
}

function ProFooter() {
  const { isPro, openPricing } = useAuth()
  if (isPro) return null

  return (
    <div className="mt-6 border-t border-border pt-4 pb-2">
      <div className="text-center font-mono text-[10px] text-neutral-600">
        OBSYD is open source and free to use.{' '}
        <button
          type="button"
          onClick={openPricing}
          className="text-cyan-glow/80 hover:text-cyan-glow underline-offset-2 hover:underline"
        >
          Upgrade to Pro →
        </button>
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

/**
 * Top-level router. Anonymous visitors hitting `/` see the marketing
 * Landing; signed-in users, the dashboard. Any `/app` path always
 * forces the dashboard (for bookmarks, social-media links, direct demo).
 * We read pathname once at mount — sufficient because the SPA never
 * navigates between landing↔dashboard internally; each is its own route.
 */
function App() {
  const { user, loading: authLoading } = useAuth()
  // Read once at module init — no need to react to client-side navigation
  // since neither route mutates the URL after mount.
  const pathname = typeof window !== 'undefined' ? window.location.pathname : '/'
  const wantsApp = pathname.startsWith('/app') || pathname.startsWith('/dashboard')

  // Anon visitor on the root path -> Landing. The PricingModal is also
  // mounted here so the Landing's "Start trial" button can open it.
  if (!wantsApp && !user && !authLoading) {
    return (
      <>
        <Landing />
        <PricingModal />
      </>
    )
  }

  return <Dashboard />
}

function Dashboard() {
  const [compactMode, setCompactMode] = useState(false)
  const [eiaData, setEiaData] = useState([])
  const [liveData, setLiveData] = useState(null)
  const [liveSource, setLiveSource] = useState(null)
  const [zones, setZones] = useState([])
  const [aisActive, setAisActive] = useState(false)
  const [gdeltActive, setGdeltActive] = useState(false)
  const [weatherAlerts, setWeatherAlerts] = useState([])
  const [disruptions, setDisruptions] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const [activeTab, setActiveTab] = useState(() => {
    const hash = window.location.hash.replace('#', '')
    return TABS.find((t) => t.key === hash) ? hash : 'critical'
  })

  // Selected bidding zone for the ENERGY tab (DE_LU / FR / NL).
  // SparkSpreadHistory has no zone column and stays DE-LU only (intentional).
  const [energyZone, setEnergyZone] = useState('DE_LU')

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
    const controller = new AbortController()
    const { signal } = controller
    async function fetchData() {
      try {
        const [eiaRes, zonesRes, liveRes, aisRes, gdeltRes] = await Promise.all([
          fetch(`${API}/prices/eia?limit=500`, { signal }),
          fetch(`${API}/vessels/zones`, { signal }),
          fetch(`${API}/prices/live`, { signal }),
          fetch(`${API}/vessels/positions?limit=1`, { signal }),
          fetch(`${API}/sentiment/status`, { signal }),
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

        fetch(`${API}/weather/alerts`, { signal })
          .then((r) => (r.ok ? r.json() : []))
          .then(setWeatherAlerts)
          .catch((e) => {
            if (e.name !== 'AbortError') console.error('Weather alerts fetch:', e)
          })

        fetch(`${API}/portwatch/summary`, { signal })
          .then((r) => (r.ok ? r.json() : null))
          .then((d) => { if (d?.disruptions) setDisruptions(d.disruptions) })
          .catch((e) => {
            if (e.name !== 'AbortError') console.error('PortWatch summary fetch:', e)
          })
      } catch (e) {
        if (e.name === 'AbortError') return
        setError(e.message)
      } finally {
        if (!signal.aborted) setLoading(false)
      }
    }
    fetchData()

    const interval = setInterval(() => {
      fetch(`${API}/prices/live`, { signal })
        .then((r) => (r.ok ? r.json() : null))
        .then((live) => {
          if (live?.available) {
            setLiveData(live.prices)
            setLiveSource(live.source || null)
          }
        })
        .catch((e) => {
          if (e.name !== 'AbortError') console.error('Live prices poll:', e)
        })
    }, 15 * 60 * 1000)
    return () => {
      clearInterval(interval)
      controller.abort()
    }
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

      {/* ===== DISRUPTIONS + TAB NAVIGATION ===== */}
      <div className="mt-4">
        <DisruptionBanner disruptions={disruptions} />
        <TabBar active={activeTab} onChange={setActiveTab} />
      </div>

      {/* ===== TAB CONTENT ===== */}
      <div className="mt-3">

        {/* CRITICAL MATERIALS TAB — the product hero */}
        {activeTab === 'critical' && (
          <ErrorBoundary name="critical-materials">
            <CriticalMaterialsView />
          </ErrorBoundary>
        )}

        {/* OVERVIEW TAB */}
        {activeTab === 'overview' && (
          <>
            {/* Row 0: Market Intelligence Report */}
            <ErrorBoundary name="market-report">
              <MarketReportPanel />
            </ErrorBoundary>

            {/* Row 1: Supply Disruption Index */}
            <div className="grid grid-cols-1 lg:grid-cols-[1fr_2fr] gap-3 mt-3">
              <ErrorBoundary name="disruption-score">
                <DisruptionScorePanel />
              </ErrorBoundary>
              <ErrorBoundary name="chokepoint-monitor">
                <ChokePointMonitor />
              </ErrorBoundary>
            </div>

            {/* Row 2: Rerouting + Historical Anomalies */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mt-3">
              <ErrorBoundary name="rerouting">
                <ReroutingIndex />
              </ErrorBoundary>
              <ErrorBoundary name="event-timeline">
                <EventTimeline />
              </ErrorBoundary>
            </div>

            {/* Row 3: EIA Fundamentals + EIA Prediction */}
            <ErrorBoundary name="fundamentals">
              <div className="mt-3">
                <FundamentalsPanel />
                <ErrorBoundary name="eia-prediction-mini">
                  <EIAPredictionMini />
                </ErrorBoundary>
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

            {/* Row 3: Crack Spread + Related Equities [PRO] */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mt-3">
              <ErrorBoundary name="crack-spread-market">
                <ProGate feature="Crack Spreads">
                  <CrackSpreadPanel />
                </ProGate>
              </ErrorBoundary>
              <ErrorBoundary name="equities-market">
                <ProGate feature="Related Equities">
                  <RelatedEquitiesPanel />
                </ProGate>
              </ErrorBoundary>
            </div>

            {/* Row 4: Correlation Heatmap */}
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

            {/* Row 3: Tonne-Miles + EIA Prediction */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mt-3">
              <ErrorBoundary name="tonne-miles">
                <TonneMilesPanel />
              </ErrorBoundary>
              <ErrorBoundary name="eia-prediction">
                <EIAPredictionPanel />
              </ErrorBoundary>
            </div>

            {/* Row 4: Freight Proxy */}
            <ErrorBoundary name="freight-proxy">
              <div className="mt-3">
                <FreightProxyPanel />
              </div>
            </ErrorBoundary>

            {/* Row 5: STS + Rerouting + Crack Spread */}
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

        {/* GAS TAB */}
        {activeTab === 'gas' && (
          <>
            {/* Row 1: Residual balance hero (Pro) */}
            <ErrorBoundary name="gas-balance">
              <ProGate feature="EU Gas Balance">
                <GasBalancePanel />
              </ProGate>
            </ErrorBoundary>

            {/* Row 2: Free driver panels */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-3 mt-3">
              <ErrorBoundary name="gas-storage">
                <GasStoragePanel />
              </ErrorBoundary>
              <ErrorBoundary name="gas-supply">
                <GasSupplyPanel />
              </ErrorBoundary>
              <ErrorBoundary name="gas-demand">
                <GasDemandPanel />
              </ErrorBoundary>
            </div>
          </>
        )}

        {/* ENERGY TAB */}
        {activeTab === 'energy' && (
          <>
            {/* Zone selector — applies to DayAhead, Grid, GenerationMix panels.
                SparkSpreadHistory has no zone column → stays DE-LU only. */}
            <div className="flex items-center justify-end mb-2">
              <ZoneSelector zone={energyZone} onChange={setEnergyZone} />
            </div>

            {/* Row 1: Spark Spread hero (Pro) — DE-LU only, no zone param */}
            <ErrorBoundary name="power-spark">
              <ProGate feature="Spark Spread">
                <SparkSpreadPanel />
              </ProGate>
            </ErrorBoundary>

            {/* Row 2: Day-Ahead price (free) */}
            <div className="mt-3">
              <ErrorBoundary name="power-dayahead">
                <PowerDayAheadPanel zone={energyZone} />
              </ErrorBoundary>
            </div>

            {/* Row 3: Residual Load + Dunkelflaute (free) */}
            <div className="mt-3">
              <ErrorBoundary name="power-grid">
                <PowerGridPanel zone={energyZone} />
              </ErrorBoundary>
            </div>

            {/* Row 4: Generation Mix (free) */}
            <div className="mt-3">
              <ErrorBoundary name="generation-mix">
                <GenerationMixPanel zone={energyZone} />
              </ErrorBoundary>
            </div>

            {/* Row 5: Cross-Border Physical Flows (free) */}
            <div className="mt-3">
              <ErrorBoundary name="cross-border-flows">
                <CrossBorderFlowPanel />
              </ErrorBoundary>
            </div>
          </>
        )}

        {/* METALS TAB */}
        {activeTab === 'metals' && (
          <>
            {/* U.S. Copper Supply — USGS MIS (public domain, free) */}
            <ErrorBoundary name="copper">
              <CopperPanel />
            </ErrorBoundary>
          </>
        )}

        {/* ATLAS TAB — per-country world map (energy / macro / climate / resources) */}
        {activeTab === 'atlas' && (
          <ErrorBoundary name="atlas">
            <AtlasMap />
          </ErrorBoundary>
        )}

        {/* ALERTS TAB (Pro feature; panel itself handles the gate) */}
        {activeTab === 'alerts' && (
          <ErrorBoundary name="alert-rules">
            <AlertRulesPanel />
          </ErrorBoundary>
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

      {/* ===== PRICING MODAL (rendered conditionally by AuthContext state) ===== */}
      <PricingModal />
    </div>
  )
}

export default App
