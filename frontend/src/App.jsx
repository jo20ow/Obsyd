import { useState, useEffect } from 'react'
import Header from './components/Header'
import PriceChart from './components/PriceChart'
import StatCards from './components/StatCards'
import VesselMap from './components/VesselMap'

const API = '/api'

function App() {
  const [eiaData, setEiaData] = useState([])
  const [liveData, setLiveData] = useState(null)
  const [zones, setZones] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    async function fetchData() {
      try {
        const [eiaRes, zonesRes, liveRes] = await Promise.all([
          fetch(`${API}/prices/eia?limit=500`),
          fetch(`${API}/vessels/zones`),
          fetch(`${API}/prices/live`),
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
      <Header />
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mt-4">
        <div className="lg:col-span-2">
          <PriceChart data={eiaData} />
        </div>
        <div>
          <StatCards data={eiaData} live={liveData} />
        </div>
      </div>
      <div className="mt-4">
        <VesselMap zones={zones} />
      </div>
    </div>
  )
}

export default App
