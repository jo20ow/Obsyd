import { useState, useEffect } from 'react'
import Header from './components/Header'
import PriceChart from './components/PriceChart'
import StatCards from './components/StatCards'
import MacroPanel from './components/MacroPanel'
import SentimentPanel from './components/SentimentPanel'
import VesselMap from './components/VesselMap'
import AlertsPanel from './components/AlertsPanel'
import FundamentalsPanel from './components/FundamentalsPanel'
import JODIPanel from './components/JODIPanel'

const API = '/api'

function App() {
  const [eiaData, setEiaData] = useState([])
  const [liveData, setLiveData] = useState(null)
  const [zones, setZones] = useState([])
  const [aisActive, setAisActive] = useState(false)
  const [gdeltActive, setGdeltActive] = useState(false)
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
          if (live.available) setLiveData(live.prices)
        }

        if (aisRes.ok) {
          const aisData = await aisRes.json()
          setAisActive(aisData.length > 0)
        }

        if (gdeltRes.ok) {
          const gdelt = await gdeltRes.json()
          setGdeltActive(gdelt.active)
        }
      } catch (e) {
        setError(e.message)
      } finally {
        setLoading(false)
      }
    }
    fetchData()
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

  return (
    <div className="min-h-screen p-4 lg:p-6">
      <Header aisActive={aisActive} gdeltActive={gdeltActive} />
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mt-4">
        <div className="lg:col-span-2 space-y-4">
          <PriceChart data={eiaData} />
          <FundamentalsPanel />
          <JODIPanel />
        </div>
        <div className="space-y-4">
          <StatCards data={eiaData} live={liveData} />
          <MacroPanel />
          <SentimentPanel />
        </div>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mt-4">
        <div className="lg:col-span-2">
          <VesselMap zones={zones} />
        </div>
        <div>
          <AlertsPanel />
        </div>
      </div>
    </div>
  )
}

export default App
