import { useState, useEffect, lazy, Suspense } from 'react'
import Sidebar from './components/Sidebar'
import CompactView from './components/CompactView'
import PriceChart from './components/PriceChart'
import MacroPanel from './components/MacroPanel'
import SentimentPanel from './components/SentimentPanel'
import CriticalMaterialsView from './components/CriticalMaterialsView'
import AlertsPanel from './components/AlertsPanel'
import SeriesExplorer from './components/SeriesExplorer'
import CoveragePanel from './components/CoveragePanel'
import DurationCurvePanel from './components/DurationCurvePanel'
import MeritOrderScatter from './components/MeritOrderScatter'
import GenMixHistoryPanel from './components/GenMixHistoryPanel'
import TrendsPanel from './components/TrendsPanel'
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
import PowerLoadForecastPanel from './components/PowerLoadForecastPanel'
import SparkSpreadPanel from './components/SparkSpreadPanel'
import GenerationMixPanel from './components/GenerationMixPanel'
import CrossBorderFlowPanel from './components/CrossBorderFlowPanel'
import CopperPanel from './components/CopperPanel'
import RegionPills from './components/RegionPills'
import LiveCharts from './components/LiveCharts'
import InsightsStrip from './components/InsightsStrip'
import NarrativeHero from './components/NarrativeHero'
import RangeSelector from './components/RangeSelector'
import PowerSituationHeader from './components/PowerSituationHeader'
import HydroReservoirPanel from './components/HydroReservoirPanel'
import OutagePanel from './components/OutagePanel'
import RecordChip from './components/RecordChip'
import PowerOverviewMatrix from './components/PowerOverviewMatrix'
import HowToRead from './components/HowToRead'
import Landing from './components/Landing'
import BriefSubscribe from './components/BriefSubscribe'
import CommandPalette from './components/CommandPalette'
import NewsPanel from './components/NewsPanel'
import { useAuth } from './context/AuthContext'
import { ViewStateProvider, useViewState } from './context/ViewStateContext'

// Heavy deck.gl/maplibre maps (~2 MB) render only on the secondary OVERVIEW/ATLAS
// tabs — lazy-load them so the default POWER desk doesn't ship the mapping stack.
const VesselMap = lazy(() => import('./components/VesselMap'))
const AtlasMap = lazy(() => import('./components/AtlasMap'))
const PowerMap = lazy(() => import('./components/PowerMap'))

const MAP_FALLBACK = (
  <div className="border border-border bg-surface rounded px-4 py-8 text-center font-mono text-xs text-neutral-500">
    Loading map…
  </div>
)

const API = '/api'

// Obsyd is the desk for the physical energy system. The front door (`primary`) is
// REFOCUS 2026-07-03: Obsyd is "gridstatus.io for Europe" — the European
// electricity+gas desk. The navigation is only POWER (electrons), GAS (its fuel)
// and ALERTS. Everything non-power (oil/maritime FLOWS, market, signals, critical,
// metals, news, atlas, sentiment) is being split into a sibling project; its tabs
// are removed here (the render blocks below are now unreachable and get physically
// extracted in Phase 2). The default tab is 'energy'.
const TABS = [
  { key: 'europe', label: 'EUROPE', primary: true },
  { key: 'energy', label: 'POWER', primary: true },
  { key: 'analytics', label: 'ANALYTICS', primary: true },
  { key: 'gas', label: 'GAS', primary: true },
  { key: 'explore', label: 'EXPLORE', primary: true },
  { key: 'alerts', label: 'ALERTS', primary: true },
]

// Analyst front door: land on the pan-European overview (all-zones matrix in the
// always-on hero + the choropleth map here), not a single-zone desk.
const DEFAULT_TAB = 'europe'

// Page heading per section (gridstatus-style "Live monitoring" title on the content).
const PAGE_TITLES = { europe: 'Live monitoring', energy: 'Power', analytics: 'Analytics', gas: 'Gas', explore: 'Data explorer', alerts: 'Alerts' }

function Disclaimer() {
  return (
    <footer className="mt-4 mb-4 px-4 text-center font-mono text-[9px] text-neutral-700 leading-relaxed max-w-2xl mx-auto">
      OBSYD is an open-source market observation tool. It does not provide investment advice, trading signals, or recommendations. All data is provided as-is for informational purposes only. AIS data is self-reported and unverified. Correlations shown are statistical observations, not causal predictions. Past correlations do not indicate future results. Not regulated by BaFin or any financial authority.
    </footer>
  )
}

// In-page section header for the grouped POWER tab (PRICES / GRID / FLOWS).
function SectionLabel({ children }) {
  return (
    <div className="font-mono text-[12px] font-semibold text-neutral-400 pt-1">{children}</div>
  )
}

// Smooth-scroll to an in-page section anchor (the POWER sub-nav).
function scrollToSection(id) {
  const el = document.getElementById(id)
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
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

  // Anon visitor on the root path -> Landing; everyone else -> Dashboard.
  if (!wantsApp && !user && !authLoading) {
    return <Landing />
  }

  // ViewStateProvider (zone+range spine + URL sync) wraps only the Dashboard, so the
  // anonymous Landing route is never rewritten with ?zone=&range=.
  return (
    <ViewStateProvider>
      <Dashboard />
    </ViewStateProvider>
  )
}

function Dashboard() {
  const [compactMode, setCompactMode] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [eiaData, setEiaData] = useState([])
  const [liveData, setLiveData] = useState(null)
  const [, setLiveSource] = useState(null)
  const [zones, setZones] = useState([])
  const [aisActive, setAisActive] = useState(false)
  const [gdeltActive, setGdeltActive] = useState(false)
  const [weatherAlerts, setWeatherAlerts] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const [activeTab, setActiveTab] = useState(() => {
    const hash = window.location.hash.replace('#', '')
    return TABS.find((t) => t.key === hash) ? hash : DEFAULT_TAB
  })

  // Navigate to a tab AND bring the desk nav (zone/range + tabs) to the viewport
  // top, so the tab's content is actually in view. Without this a jump (e.g. ⌘K
  // "Power zone: AT" or an overview-row click) only switches the tab and leaves you
  // scrolled above the always-on chrome, having to scroll down to reach the section.
  const goToTab = (key) => {
    setActiveTab(key)
    requestAnimationFrame(() => scrollToSection('desk-nav'))
  }

  // Selected bidding zone — now the global navigation spine (ViewStateContext):
  // one zone drives the hero, POWER, ANALYTICS and the explorer, is mirrored into
  // the URL (?zone=) and persists. Aliased to the old local names so the ~14
  // downstream consumers stay untouched. SparkSpreadHistory is DE-LU-only in-panel.
  const { zone: energyZone, setZone: setEnergyZone } = useViewState()

  // URL hash sync — keep the default (POWER) tab off the URL so the bare
  // homepage stays clean (`/`); only non-default tabs get a shareable hash.
  useEffect(() => {
    if (activeTab === DEFAULT_TAB) {
      history.replaceState(null, '', window.location.pathname + window.location.search)
    } else {
      window.location.hash = activeTab
    }
  }, [activeTab])

  useEffect(() => {
    const handler = () => {
      const hash = window.location.hash.replace('#', '')
      if (TABS.find((t) => t.key === hash)) setActiveTab(hash)
    }
    window.addEventListener('hashchange', handler)
    return () => window.removeEventListener('hashchange', handler)
  }, [])

  // Terminal command palette (⌘K / Ctrl-K toggles it). First global key handler.
  const { user } = useAuth()
  const [paletteOpen, setPaletteOpen] = useState(false)
  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault()
        setPaletteOpen((o) => !o)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
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
    <div className="min-h-screen lg:flex">
      {/* ===== LEFT SIDEBAR (nav + utilities) ===== */}
      <Sidebar
        tabs={TABS}
        activeTab={activeTab}
        onNavigate={(k) => { goToTab(k); setSidebarOpen(false) }}
        onOpenPalette={() => setPaletteOpen(true)}
        aisActive={aisActive}
        gdeltActive={gdeltActive}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />

      {/* ===== MAIN CONTENT ===== */}
      <main className="flex-1 min-w-0 p-3 lg:p-4">
        {/* Mobile top bar: hamburger + brand (sidebar is a drawer below lg) */}
        <div className="lg:hidden flex items-center justify-between mb-3 pb-2 border-b border-border">
          <button onClick={() => setSidebarOpen(true)} aria-label="Open menu"
            className="font-mono text-lg text-neutral-400 hover:text-cyan-glow">☰</button>
          <span className="font-mono font-bold tracking-widest text-cyan-glow">OBSYD</span>
          <span className="w-5" />
        </div>

        {/* Slim price ticker */}
        <div className="mb-3">
          <PriceTicker />
        </div>

        {/* Region pills (Everywhere + zones) + global range */}
        <div id="desk-nav" className="scroll-mt-2 flex flex-wrap items-center gap-x-3 gap-y-2 pb-3 mb-1 border-b border-border">
          <h1 className="font-mono text-[15px] font-semibold text-neutral-200 shrink-0 mr-1">{PAGE_TITLES[activeTab] || 'Obsyd'}</h1>
          <RegionPills
            activeTab={activeTab}
            onEverywhere={() => goToTab('europe')}
            onPickZone={(z) => { setEnergyZone(z); if (activeTab === 'europe') goToTab('energy') }}
          />
          <div className="flex items-center gap-2 ml-auto">
            <span className="font-mono text-[10px] text-neutral-600 tracking-wider">RANGE</span>
            <RangeSelector />
          </div>
        </div>

        {/* ===== TAB CONTENT ===== */}
        <div className="mt-3">

        {/* CRITICAL MATERIALS TAB — supply concentration for critical minerals */}
        {activeTab === 'critical' && (
          <ErrorBoundary name="critical-materials">
            <CriticalMaterialsView />
          </ErrorBoundary>
        )}

        {/* OVERVIEW TAB — oil/maritime situational picture */}
        {activeTab === 'overview' && (
          <>
            {/* Row 0: Live tanker map + daily briefing (maritime context) */}
            <ErrorBoundary name="vessel-map">
              <Suspense fallback={MAP_FALLBACK}>
                <VesselMap zones={zones} weatherAlerts={weatherAlerts} />
              </Suspense>
            </ErrorBoundary>
            <ErrorBoundary name="briefing">
              <div className="mt-3">
                <BriefingPanel />
              </div>
            </ErrorBoundary>

            {/* Row 1: Market Intelligence Report */}
            <div className="mt-3">
              <ErrorBoundary name="market-report">
                <MarketReportPanel />
              </ErrorBoundary>
            </div>

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
                <CrackSpreadPanel />
              </ErrorBoundary>
              <ErrorBoundary name="equities-market">
                <RelatedEquitiesPanel />
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
                <STSPanel />
              </ErrorBoundary>
              <ErrorBoundary name="rerouting-signals">
                <ReroutingIndex />
              </ErrorBoundary>
              <ErrorBoundary name="crack-spread">
                <CrackSpreadPanel />
              </ErrorBoundary>
            </div>
          </>
        )}

        {/* GAS TAB */}
        {activeTab === 'gas' && (
          <>
            {/* Row 1: Residual balance hero */}
            <ErrorBoundary name="gas-balance">
              <GasBalancePanel />
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

        {/* POWER TAB — evidence/drill-down behind the always-on situation header.
            Zone is controlled from the desk header above (energyZone). The
            zone-aware, complete free panels (price, residual) lead; the DE-LU-only
            spark spread follows. */}
        {activeTab === 'energy' && (
          <div className="space-y-3">
            {/* Sticky sub-nav — jump between the grouped sections instead of one long
                scroll (gridstatus "Grid Conditions / Trends & Profile" analog). */}
            <div className="sticky top-0 z-20 flex flex-wrap items-center gap-2 py-1.5 bg-surface/95 backdrop-blur border-b border-border/60">
              <span className="font-mono text-[12px] font-semibold text-neutral-300">Power · {energyZone}</span>
              <div className="flex items-center gap-1">
                {[['section-power-prices', 'PRICES'], ['section-power-grid', 'GRID'], ['section-power-flows', 'FLOWS']].map(([id, label]) => (
                  <button key={id} onClick={() => scrollToSection(id)}
                    className="font-mono text-[9px] px-2 py-0.5 rounded border text-neutral-500 border-border hover:text-cyan-glow hover:border-cyan-glow/40">
                    {label}
                  </button>
                ))}
              </div>
            </div>

            <ErrorBoundary name="power-situation">
              <PowerSituationHeader zone={energyZone} />
            </ErrorBoundary>
            <ErrorBoundary name="record-chip">
              <RecordChip zone={energyZone} />
            </ErrorBoundary>

            {/* PRICES — day-ahead + spark spread */}
            <div id="section-power-prices" className="scroll-mt-16 space-y-3">
              <SectionLabel>PRICES</SectionLabel>
              <ErrorBoundary name="power-dayahead">
                <PowerDayAheadPanel zone={energyZone} />
              </ErrorBoundary>
              <ErrorBoundary name="power-spark">
                <SparkSpreadPanel zone={energyZone} />
              </ErrorBoundary>
            </div>

            {/* GRID & GENERATION — outages, residual/Dunkelflaute, load forecast, generation mix */}
            <div id="section-power-grid" className="scroll-mt-16 space-y-3">
              <SectionLabel>GRID &amp; GENERATION</SectionLabel>
              <ErrorBoundary name="power-outages">
                <OutagePanel zone={energyZone} />
              </ErrorBoundary>
              <ErrorBoundary name="power-grid">
                <PowerGridPanel zone={energyZone} />
              </ErrorBoundary>
              <ErrorBoundary name="power-load-forecast">
                <PowerLoadForecastPanel zone={energyZone} />
              </ErrorBoundary>
              <ErrorBoundary name="generation-mix">
                <GenerationMixPanel zone={energyZone} />
              </ErrorBoundary>
            </div>

            {/* FLOWS — cross-border physical flows */}
            <div id="section-power-flows" className="scroll-mt-16 space-y-3">
              <SectionLabel>CROSS-BORDER FLOWS</SectionLabel>
              <ErrorBoundary name="cross-border-flows">
                <CrossBorderFlowPanel zone={energyZone} />
              </ErrorBoundary>
            </div>
          </div>
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

        {/* NEWS TAB — cross-asset headlines (GDELT, free) */}
        {activeTab === 'news' && (
          <ErrorBoundary name="news">
            <NewsPanel />
          </ErrorBoundary>
        )}

        {/* ATLAS TAB — per-country world map (energy / macro / climate / resources) */}
        {activeTab === 'atlas' && (
          <ErrorBoundary name="atlas">
            <Suspense fallback={MAP_FALLBACK}>
              <AtlasMap />
            </Suspense>
          </ErrorBoundary>
        )}

        {/* ANALYTICS TAB — exploit the 5y hourly history for the analyst audience */}
        {activeTab === 'analytics' && (
          <>
            <div className="mb-2">
              <span className="font-mono text-[10px] text-neutral-600 tracking-wider">// ANALYTICS · deep history · {energyZone}</span>
            </div>
            <ErrorBoundary name="duration-curve">
              <DurationCurvePanel zone={energyZone} />
            </ErrorBoundary>
            <div className="mt-3">
              <ErrorBoundary name="genmix-history">
                <GenMixHistoryPanel zone={energyZone} />
              </ErrorBoundary>
            </div>
            <div className="mt-3">
              <ErrorBoundary name="trends">
                <TrendsPanel zone={energyZone} />
              </ErrorBoundary>
            </div>
            <div className="mt-3">
              <ErrorBoundary name="merit-order">
                <MeritOrderScatter zone={energyZone} />
              </ErrorBoundary>
            </div>
          </>
        )}

        {/* LIVE (default front door) — the all-zones overview: sortable table BESIDE
            the choropleth map, then the anomaly radar + orientation. */}
        {activeTab === 'europe' && (
          <div className="space-y-3">
            <ErrorBoundary name="narrative">
              <NarrativeHero />
            </ErrorBoundary>
            <h2 className="font-mono text-[15px] font-semibold text-neutral-200">European power desk · all zones</h2>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 items-start">
              <ErrorBoundary name="power-overview">
                <PowerOverviewMatrix
                  selectedZone={energyZone}
                  onSelect={(z) => { setEnergyZone(z); goToTab('energy') }}
                />
              </ErrorBoundary>
              <ErrorBoundary name="power-map">
                <Suspense fallback={MAP_FALLBACK}>
                  <PowerMap />
                </Suspense>
              </ErrorBoundary>
            </div>
            <ErrorBoundary name="insights">
              <InsightsStrip onMore={() => goToTab('alerts')} />
            </ErrorBoundary>
            <ErrorBoundary name="hydro">
              <HydroReservoirPanel />
            </ErrorBoundary>
            <ErrorBoundary name="live-charts">
              <LiveCharts />
            </ErrorBoundary>
            <ErrorBoundary name="how-to-read">
              <HowToRead />
            </ErrorBoundary>
          </div>
        )}

        {/* EXPLORE TAB — interactive query over the public data API (/api/v1/series) */}
        {activeTab === 'explore' && (
          <div className="space-y-3">
            <ErrorBoundary name="series-explorer">
              <SeriesExplorer />
            </ErrorBoundary>
            <ErrorBoundary name="coverage">
              <CoveragePanel />
            </ErrorBoundary>
          </div>
        )}

        {/* ALERTS TAB (Pro feature; panel itself handles the gate) */}
        {activeTab === 'alerts' && (
          <div className="space-y-3">
            <ErrorBoundary name="radar">
              <AlertsPanel weatherAlerts={weatherAlerts} />
            </ErrorBoundary>
            <ErrorBoundary name="brief-subscribe">
              <BriefSubscribe />
            </ErrorBoundary>
            <ErrorBoundary name="alert-rules">
              <AlertRulesPanel />
            </ErrorBoundary>
          </div>
        )}

        {/* SENTIMENT TAB */}
        {activeTab === 'sentiment' && (
          <>
            {/* Row 1: GDELT Sentiment (full width) */}
            <ErrorBoundary name="sentiment">
              <SentimentPanel />
            </ErrorBoundary>

            {/* Row 2: Related Equities */}
            <ErrorBoundary name="related-equities">
              <div className="mt-3">
                <RelatedEquitiesPanel />
              </div>
            </ErrorBoundary>
          </>
        )}
      </div>

        {/* ===== FOOTER ===== */}
        <Disclaimer />
      </main>

      {paletteOpen && (
        <CommandPalette
          onClose={() => setPaletteOpen(false)}
          tabs={TABS}
          setActiveTab={goToTab}
          setEnergyZone={setEnergyZone}
          authed={!!user?.authenticated}
        />
      )}
    </div>
  )
}

export default App
