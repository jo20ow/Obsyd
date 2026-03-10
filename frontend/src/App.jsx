import { useState, useEffect } from 'react'
import Header from './components/Header'
import CompactView from './components/CompactView'
import PriceChart from './components/PriceChart'
import StatCards from './components/StatCards'
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

const API = '/api'

function Disclaimer() {
  return (
    <footer className="mt-8 mb-4 px-4 text-center font-mono text-[9px] text-neutral-700 leading-relaxed max-w-2xl mx-auto">
      OBSYD is an open-source market observation tool. It does not provide investment advice, trading signals, or recommendations. All data is provided as-is for informational purposes only. AIS data is self-reported and unverified. Correlations shown are statistical observations, not causal predictions. Past correlations do not indicate future results. Not regulated by BaFin or any financial authority.
    </footer>
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

        // Fetch weather alerts once (shared by VesselMap + AlertsPanel)
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

    // Re-poll live prices every 15 minutes
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
    <div className="min-h-screen p-4 lg:p-6">
      <Header aisActive={aisActive} gdeltActive={gdeltActive} compactMode={compactMode} onToggleCompact={() => setCompactMode(true)} />
      <ErrorBoundary name="briefing">
        <div className="mt-4">
          <BriefingPanel />
        </div>
      </ErrorBoundary>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mt-4">
        <div className="lg:col-span-2">
          <ErrorBoundary name="vessel-map">
            <VesselMap zones={zones} weatherAlerts={weatherAlerts} />
          </ErrorBoundary>
        </div>
        <div className="space-y-4">
          <ErrorBoundary name="stat-cards">
            <StatCards data={eiaData} live={liveData} liveSource={liveSource} />
          </ErrorBoundary>
          <ErrorBoundary name="alerts">
            <AlertsPanel weatherAlerts={weatherAlerts} />
          </ErrorBoundary>
        </div>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mt-4">
        <div className="lg:col-span-2 space-y-4">
          <ErrorBoundary name="price-chart">
            <PriceChart data={eiaData} live={liveData} />
          </ErrorBoundary>
          <ErrorBoundary name="fundamentals">
            <FundamentalsPanel />
          </ErrorBoundary>
          <ErrorBoundary name="jodi">
            <JODIPanel />
          </ErrorBoundary>
        </div>
        <div className="space-y-4">
          <ErrorBoundary name="market-structure">
            <MarketStructure />
          </ErrorBoundary>
          <ErrorBoundary name="crack-spread">
            <ProGate feature="Crack Spread">
              <CrackSpreadPanel />
            </ProGate>
          </ErrorBoundary>
          <ErrorBoundary name="related-equities">
            <ProGate feature="Related Equities">
              <RelatedEquitiesPanel />
            </ProGate>
          </ErrorBoundary>
          <ErrorBoundary name="macro">
            <MacroPanel />
          </ErrorBoundary>
          <ErrorBoundary name="sentiment">
            <SentimentPanel />
          </ErrorBoundary>
        </div>
      </div>
      <ErrorBoundary name="chokepoint-monitor">
        <div className="mt-4">
          <ChokePointMonitor />
        </div>
      </ErrorBoundary>
      <ErrorBoundary name="zone-activity">
        <div className="mt-4">
          <ZoneActivityChart />
        </div>
      </ErrorBoundary>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
        <ErrorBoundary name="voyages">
          <VoyagesPanel />
        </ErrorBoundary>
        <ErrorBoundary name="flow-matrix">
          <FlowMatrixPanel />
        </ErrorBoundary>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
        <ErrorBoundary name="sts-detection">
          <ProGate feature="STS Detection">
            <STSPanel />
          </ProGate>
        </ErrorBoundary>
        <ErrorBoundary name="rerouting">
          <ReroutingIndex />
        </ErrorBoundary>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
        <ErrorBoundary name="correlation">
          <CorrelationPanel />
        </ErrorBoundary>
        <ErrorBoundary name="event-timeline-col">
          <EventTimeline />
        </ErrorBoundary>
      </div>
      <Disclaimer />
    </div>
  )
}

export default App
