import { useState, useEffect, useCallback, useRef } from 'react'
import { Map } from 'react-map-gl/maplibre'
import DeckGL from '@deck.gl/react'
import { ScatterplotLayer, PolygonLayer, TextLayer } from '@deck.gl/layers'
import 'maplibre-gl/dist/maplibre-gl.css'

const API = '/api'
const POLL_INTERVAL = 30_000

const INITIAL_VIEW = {
  longitude: 45,
  latitude: 20,
  zoom: 2.2,
  pitch: 0,
  bearing: 0,
}

const DARK_MAP_STYLE = {
  version: 8,
  name: 'obsyd-dark',
  sources: {
    'osm-tiles': {
      type: 'raster',
      tiles: ['https://basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}@2x.png'],
      tileSize: 256,
      attribution: '&copy; CARTO &copy; OpenStreetMap',
    },
  },
  layers: [
    {
      id: 'osm-tiles',
      type: 'raster',
      source: 'osm-tiles',
      minzoom: 0,
      maxzoom: 19,
    },
  ],
}

// Ship type classification
function shipTypeLabel(t) {
  if (t >= 80 && t <= 89) return 'Tanker'
  if (t >= 70 && t <= 79) return 'Cargo'
  if (t >= 60 && t <= 69) return 'Passenger'
  if (t >= 40 && t <= 49) return 'High Speed'
  if (t >= 30 && t <= 39) return 'Fishing'
  if (t >= 90 && t <= 99) return 'Other'
  return `Type ${t}`
}

function shipColor(t, sog, isZone) {
  // Tanker
  if (t >= 80 && t <= 89) {
    if (sog < 0.5) return [255, 80, 80, 220]   // anchored tanker = red
    return [0, 229, 255, 220]                     // moving tanker = cyan
  }
  // Cargo
  if (t >= 70 && t <= 79) return [140, 140, 160, 160]
  // Container (often 79 but varies)
  if (t >= 60 && t <= 69) return [100, 140, 200, 160]
  // Default
  return [100, 100, 120, 120]
}

function shipRadius(t, isZone) {
  if (t >= 80 && t <= 89) return isZone ? 5 : 3.5  // tanker = larger
  if (t >= 70 && t <= 79) return isZone ? 3.5 : 2.5
  return isZone ? 3 : 2
}

function timeAgo(ts) {
  if (!ts) return ''
  const diff = Date.now() - new Date(ts).getTime()
  const secs = Math.floor(diff / 1000)
  if (secs < 60) return `${secs}s ago`
  const mins = Math.floor(secs / 60)
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  return `${hrs}h ago`
}

function zoneToPoly(bounds) {
  const [sw, ne] = bounds
  return [
    [sw[1], sw[0]],
    [ne[1], sw[0]],
    [ne[1], ne[0]],
    [sw[1], ne[0]],
    [sw[1], sw[0]],
  ]
}

function zoneCenter(bounds) {
  const [sw, ne] = bounds
  return [(sw[1] + ne[1]) / 2, (sw[0] + ne[0]) / 2]
}

export default function VesselMap({ zones, weatherAlerts = [] }) {
  const [vessels, setVessels] = useState([])
  const [globalVessels, setGlobalVessels] = useState([])
  const [mode, setMode] = useState('geofence')
  const [showThermal, setShowThermal] = useState(false)
  const [thermalData, setThermalData] = useState([])
  const [portwatch, setPortwatch] = useState(null)
  const [marine, setMarine] = useState({})
  const [viewState, setViewState] = useState(INITIAL_VIEW)

  const hurricanes = weatherAlerts.filter((a) => a.latitude && a.longitude)

  const fetchVessels = useCallback(async () => {
    try {
      if (mode === 'global') {
        const res = await fetch(`${API}/vessels/global?limit=5000`)
        if (res.ok) {
          const data = await res.json()
          setGlobalVessels(data)
        }
      }
      const res = await fetch(`${API}/vessels/positions?limit=2000`)
      if (res.ok) {
        const data = await res.json()
        setVessels(data)
      }
    } catch {
      // silent fail, will retry
    }
  }, [mode])

  useEffect(() => {
    fetch(`${API}/ports/summary`)
      .then((r) => r.ok ? r.json() : null)
      .then((d) => { if (d) setPortwatch(d) })
      .catch((e) => console.error('VesselMap ports/summary:', e))

    fetch(`${API}/weather/marine`)
      .then((r) => r.ok ? r.json() : null)
      .then((d) => { if (d?.zones) setMarine(d.zones) })
      .catch((e) => console.error('VesselMap weather/marine:', e))

    fetch(`${API}/thermal/hotspots`)
      .then((r) => r.ok ? r.json() : [])
      .then(setThermalData)
      .catch((e) => console.error('VesselMap thermal:', e))
  }, [])

  useEffect(() => {
    fetchVessels()
    const id = setInterval(fetchVessels, POLL_INTERVAL)
    return () => clearInterval(id)
  }, [fetchVessels])

  const flyToZone = (z) => {
    const [lon, lat] = zoneCenter(z.bounds)
    setViewState({
      longitude: lon,
      latitude: lat,
      zoom: 7,
      pitch: 0,
      bearing: 0,
      transitionDuration: 800,
    })
  }

  const isGlobal = mode === 'global'
  const displayVessels = isGlobal ? globalVessels : vessels

  // Counts for legend
  const tankerCount = vessels.filter((v) => v.ship_type >= 80 && v.ship_type <= 89).length
  const anchoredCount = vessels.filter((v) => v.sog < 0.5).length

  // Build layers
  const layers = [
    // Geofence zone polygons
    new PolygonLayer({
      id: 'geofences',
      data: zones,
      getPolygon: (z) => zoneToPoly(z.bounds),
      getFillColor: (z) => z.no_ais_coverage ? [255, 160, 0, 10] : [0, 229, 255, 18],
      getLineColor: (z) => z.no_ais_coverage ? [255, 160, 0, 50] : [0, 229, 255, 80],
      getLineWidth: 1,
      lineWidthUnits: 'pixels',
      filled: true,
      stroked: true,
      pickable: true,
    }),
    // Zone labels
    new TextLayer({
      id: 'zone-labels',
      data: zones,
      getPosition: (z) => zoneCenter(z.bounds),
      getText: (z) => z.name.toUpperCase(),
      getColor: (z) => z.no_ais_coverage ? [255, 160, 0, 100] : [0, 229, 255, 120],
      getSize: 11,
      fontFamily: 'JetBrains Mono, monospace',
      fontWeight: 700,
      getTextAnchor: 'middle',
      getAlignmentBaseline: 'center',
      billboard: false,
    }),
    // Anchored tanker rings (SOG < 0.5 in zone vessels)
    new ScatterplotLayer({
      id: 'anchor-rings',
      data: (isGlobal ? globalVessels.filter((d) => d.is_tanker && d.zone) : vessels).filter((d) => d.sog < 0.5),
      getPosition: (d) => [d.lon, d.lat],
      getRadius: 7,
      radiusUnits: 'pixels',
      radiusMinPixels: 5,
      radiusMaxPixels: 12,
      getFillColor: [0, 0, 0, 0],
      getLineColor: [255, 80, 80, 140],
      getLineWidth: 1,
      lineWidthUnits: 'pixels',
      stroked: true,
      filled: false,
      updateTriggers: { getPosition: [vessels.length, globalVessels.length] },
    }),
    // Global non-tanker vessels (grey)
    ...(isGlobal
      ? [
          new ScatterplotLayer({
            id: 'global-vessels',
            data: globalVessels.filter((d) => !d.is_tanker),
            getPosition: (d) => [d.lon, d.lat],
            getRadius: (d) => shipRadius(d.ship_type, false),
            radiusUnits: 'pixels',
            radiusMinPixels: 1,
            radiusMaxPixels: 4,
            getFillColor: (d) => shipColor(d.ship_type, d.sog, false),
            pickable: true,
            updateTriggers: { getPosition: [globalVessels.length] },
          }),
        ]
      : []),
    // Main vessel layer (zone tankers or global tankers)
    new ScatterplotLayer({
      id: 'vessels',
      data: isGlobal
        ? globalVessels.filter((d) => d.is_tanker)
        : vessels,
      getPosition: (d) => [d.lon, d.lat],
      getRadius: (d) => shipRadius(d.ship_type, true),
      radiusUnits: 'pixels',
      radiusMinPixels: 3,
      radiusMaxPixels: 8,
      getFillColor: (d) => shipColor(d.ship_type, d.sog, true),
      pickable: true,
      updateTriggers: {
        getFillColor: [vessels.length, globalVessels.length],
        getPosition: [vessels.length, globalVessels.length],
      },
    }),
    // Thermal hotspots
    ...(showThermal && thermalData.length > 0
      ? [
          new ScatterplotLayer({
            id: 'thermal',
            data: thermalData,
            getPosition: (d) => [d.lon, d.lat],
            getRadius: (d) => Math.max(3, Math.min(10, (d.brightness - 300) / 20)),
            radiusUnits: 'pixels',
            radiusMinPixels: 3,
            radiusMaxPixels: 12,
            getFillColor: (d) => {
              const t = Math.min(1, Math.max(0, (d.brightness - 300) / 100))
              return [255, Math.floor(200 - t * 120), Math.floor(50 - t * 50), 180]
            },
            pickable: true,
            updateTriggers: {
              getRadius: [thermalData.length],
              getFillColor: [thermalData.length],
            },
          }),
        ]
      : []),
    // Hurricane alerts
    new ScatterplotLayer({
      id: 'hurricanes',
      data: hurricanes,
      getPosition: (d) => [d.longitude, d.latitude],
      getRadius: 18,
      radiusUnits: 'pixels',
      radiusMinPixels: 12,
      radiusMaxPixels: 30,
      getFillColor: [255, 160, 0, 60],
      getLineColor: [255, 160, 0, 200],
      lineWidthUnits: 'pixels',
      getLineWidth: 2,
      stroked: true,
      filled: true,
      pickable: true,
    }),
  ]

  const getTooltip = ({ object, layer }) => {
    if (!object) return null
    if (layer.id === 'vessels' || layer.id === 'global-tankers' || layer.id === 'global-vessels' || layer.id === 'anchor-rings') {
      const hdg = object.heading != null && object.heading !== 511
        ? `${object.heading.toFixed(0)}°` : '—'
      return {
        html: `<div style="font-family:'JetBrains Mono',monospace;font-size:11px;color:#c8c8d0;line-height:1.5">
          <div style="color:#00e5ff;font-weight:bold;margin-bottom:2px">${object.ship_name || 'UNKNOWN'}</div>
          <div><span style="color:#666">MMSI</span> ${object.mmsi}</div>
          <div><span style="color:#666">TYPE</span> ${shipTypeLabel(object.ship_type)} <span style="color:#555">(${object.ship_type})</span></div>
          <div><span style="color:#666">SOG</span> ${object.sog.toFixed(1)} kn &nbsp;<span style="color:#666">HDG</span> ${hdg}</div>
          ${object.zone ? `<div><span style="color:#666">ZONE</span> <span style="color:#00e5ff">${object.zone.toUpperCase()}</span></div>` : ''}
          ${object.timestamp ? `<div style="color:#555;font-size:10px;margin-top:2px">${timeAgo(object.timestamp)}</div>` : ''}
        </div>`,
        style: { background: '#0a0a0f', border: '1px solid #1e1e2e', borderRadius: '4px', padding: '8px' },
      }
    }
    if (layer.id === 'thermal') {
      return {
        html: `<div style="font-family:monospace;font-size:11px;color:#ffa000">
          <div style="font-weight:bold">THERMAL HOTSPOT</div>
          <div style="color:#c8c8d0">Brightness: ${object.brightness.toFixed(1)} K</div>
          <div style="color:#c8c8d0">Confidence: ${object.confidence}</div>
          <div style="color:#c8c8d0">Area: ${object.area_name}</div>
          <div style="color:#c8c8d0">${object.acq_date} ${object.acq_time}</div>
        </div>`,
        style: { background: '#0a0a0f', border: '1px solid #ffa000', borderRadius: '4px', padding: '8px' },
      }
    }
    if (layer.id === 'geofences') {
      const count = vessels.filter((v) => v.zone === object.name).length
      return {
        html: `<div style="font-family:monospace;font-size:11px;color:#00e5ff">
          <div style="font-weight:bold">${object.display_name}</div>
          ${count ? `<div style="color:#c8c8d0">${count} tankers tracked</div>` : ''}
          ${object.no_ais_coverage ? '<div style="color:#ffa000;font-size:10px">No AIS coverage</div>' : ''}
        </div>`,
        style: { background: '#0a0a0f', border: '1px solid #1e1e2e', borderRadius: '4px', padding: '8px' },
      }
    }
    if (layer.id === 'hurricanes') {
      return {
        html: `<div style="font-family:monospace;font-size:11px;color:#ffa000;font-weight:bold">${object.event}<br/><span style="color:#c8c8d0;font-weight:normal">${object.area?.substring(0, 80) || ''}</span></div>`,
        style: { background: '#0a0a0f', border: '1px solid #ffa000', borderRadius: '4px', padding: '8px' },
      }
    }
    return null
  }

  return (
    <div className="border border-border bg-surface rounded">
      {/* Header bar */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border flex-wrap gap-2">
        <span className="font-mono text-xs text-neutral-500">
          AIS VESSEL MAP // {isGlobal ? 'GLOBAL VIEW' : 'GEOFENCE MONITORING'}
        </span>
        <div className="flex items-center gap-3 font-mono text-[10px]">
          <div className="flex items-center border border-border rounded overflow-hidden">
            <button
              onClick={() => setMode('geofence')}
              className={`px-2 py-0.5 transition-colors ${
                !isGlobal
                  ? 'bg-cyan-glow/15 text-cyan-glow'
                  : 'text-neutral-600 hover:text-neutral-400'
              }`}
            >
              GEOFENCE
            </button>
            <button
              onClick={() => setMode('global')}
              className={`px-2 py-0.5 transition-colors ${
                isGlobal
                  ? 'bg-cyan-glow/15 text-cyan-glow'
                  : 'text-neutral-600 hover:text-neutral-400'
              }`}
            >
              GLOBAL
            </button>
          </div>
          <button
            onClick={() => setShowThermal((v) => !v)}
            className={`px-2 py-0.5 border rounded transition-colors ${
              showThermal
                ? 'bg-orange-400/15 text-orange-400 border-orange-500/30'
                : 'text-neutral-600 border-border hover:text-neutral-400'
            }`}
          >
            THERMAL
          </button>
          <span className="text-neutral-600">30s poll</span>
        </div>
      </div>

      {/* Map */}
      <div className="relative h-[500px] w-full">
        <DeckGL
          viewState={viewState}
          onViewStateChange={({ viewState: vs }) => setViewState(vs)}
          controller={true}
          layers={layers}
          getTooltip={getTooltip}
        >
          <Map mapStyle={DARK_MAP_STYLE} />
        </DeckGL>

        {/* Legend overlay */}
        <div className="absolute bottom-3 left-3 bg-[#0a0a0f]/90 border border-border rounded px-3 py-2.5 font-mono text-[10px] space-y-1.5 pointer-events-auto">
          <div className="text-neutral-500 tracking-wider mb-1">LEGEND</div>
          <div className="flex items-center gap-2">
            <span className="w-2.5 h-2.5 rounded-full bg-cyan-glow shrink-0" />
            <span className="text-neutral-300">Tanker (moving)</span>
            <span className="text-cyan-glow ml-auto pl-3">{isGlobal ? globalVessels.filter(v => v.is_tanker).length : tankerCount}</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="w-2.5 h-2.5 rounded-full bg-red-400 shrink-0" />
            <span className="text-neutral-300">Anchored (SOG&lt;0.5)</span>
            <span className="text-red-400 ml-auto pl-3">{anchoredCount}</span>
          </div>
          {isGlobal && (
            <div className="flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-neutral-500 shrink-0" />
              <span className="text-neutral-400">Cargo / Other</span>
              <span className="text-neutral-500 ml-auto pl-3">{globalVessels.filter(v => !v.is_tanker).length}</span>
            </div>
          )}
          <div className="border-t border-border pt-1.5 mt-1.5 space-y-1">
            <div className="text-neutral-500 tracking-wider">ZONES</div>
            {zones.map((z) => {
              const count = vessels.filter((v) => v.zone === z.name).length
              return (
                <button
                  key={z.name}
                  onClick={() => flyToZone(z)}
                  className="flex items-center gap-2 w-full text-left hover:text-cyan-glow transition-colors group"
                >
                  <span className={`w-2 h-2 rounded-sm shrink-0 ${z.no_ais_coverage ? 'bg-orange-400/40' : 'bg-cyan-glow/40'}`} />
                  <span className="text-neutral-400 group-hover:text-cyan-glow">{z.name.toUpperCase()}</span>
                  {z.no_ais_coverage ? (
                    <span className="text-orange-400/60 ml-auto text-[9px]">NO AIS</span>
                  ) : (
                    <span className="text-neutral-600 ml-auto">{count || '—'}</span>
                  )}
                </button>
              )
            })}
            <button
              onClick={() => setViewState({ ...INITIAL_VIEW, transitionDuration: 800 })}
              className="text-neutral-600 hover:text-neutral-400 transition-colors mt-0.5"
            >
              RESET VIEW
            </button>
          </div>
        </div>
      </div>

      {/* Zone cards footer */}
      <div className="px-4 py-3 border-t border-border">
        <div className="font-mono text-[10px] text-neutral-600 mb-2 tracking-wider">
          ACTIVE GEOFENCE ZONES
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
          {zones.map((z) => {
            const count = vessels.filter((v) => v.zone === z.name).length
            const cp = portwatch?.chokepoints?.find((c) => c.zone === z.name)
            return (
              <button
                key={z.name}
                onClick={() => flyToZone(z)}
                className="text-left border border-border bg-surface-light rounded px-3 py-2 hover:border-cyan-glow/30 transition-colors"
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-xs text-cyan-glow">
                    {z.name.toUpperCase()}
                  </span>
                  {z.no_ais_coverage ? (
                    <span className="font-mono text-[9px] text-orange-400/60">NO AIS</span>
                  ) : count > 0 ? (
                    <span className="font-mono text-[10px] text-green-glow">{count}</span>
                  ) : null}
                </div>
                <div className="font-mono text-[10px] text-neutral-600 mt-0.5 leading-tight">
                  {z.display_name}
                </div>
                {(cp || marine[z.name]) && (
                  <div className="mt-1.5 pt-1.5 border-t border-border">
                    {cp && (
                      <div className="font-mono text-[10px] text-neutral-500">
                        {cp.vessel_count} transits
                        <span className="text-neutral-600"> / </span>
                        {cp.vessel_count_tanker} tanker
                      </div>
                    )}
                    {marine[z.name] && (
                      <div className="font-mono text-[10px] text-neutral-600 mt-0.5">
                        {marine[z.name].wind_speed != null && (
                          <span>{marine[z.name].wind_speed.toFixed(0)} kn </span>
                        )}
                        {marine[z.name].wave_height != null && (
                          <span>{marine[z.name].wave_height.toFixed(1)}m</span>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </button>
            )
          })}
        </div>
        {portwatch && (
          <div className="font-mono text-[9px] text-neutral-700 mt-2">
            Source: IMF PortWatch{portwatch.date ? ` (${portwatch.date})` : ''} // AIS: AISHub + AISStream
          </div>
        )}
      </div>
    </div>
  )
}
