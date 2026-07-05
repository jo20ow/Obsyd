import { useState } from 'react'
import useZones from '../hooks/useZones'
import MiniSeriesCard from './MiniSeriesCard'
import MiniMixCard from './MiniMixCard'

// gridstatus "Live monitoring" grid: section tabs (which metric) + a responsive
// multi-zone card grid (the metric per core zone), all driven by the global range.
const CORE = ['DE_LU', 'FR', 'NL', 'BE', 'ES', 'AT']

const SECTIONS = [
  { key: 'prices', label: 'Prices', kind: 'series', series: 'price.dayahead', unit: '€/MWh', scale: 1, color: '#22d3ee' },
  { key: 'mix', label: 'Fuel Mix', kind: 'mix' },
  { key: 'load', label: 'Load', kind: 'series', series: 'load.actual', unit: 'GW', scale: 1 / 1000, color: '#a78bfa' },
  { key: 'residual', label: 'Residual', kind: 'series', series: 'residual.actual', unit: 'GW', scale: 1 / 1000, color: '#f59e0b' },
]

export default function LiveCharts() {
  const [section, setSection] = useState('prices')
  const { zones } = useZones()
  const keys = new Set(zones.map((z) => z.key))
  const gridZones = CORE.filter((k) => keys.has(k))
  const labelFor = (k) => zones.find((z) => z.key === k)?.label || k
  const s = SECTIONS.find((x) => x.key === section) || SECTIONS[0]

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-1 border-b border-border overflow-x-auto scrollbar-hidden">
        {SECTIONS.map((x) => (
          <button
            key={x.key}
            onClick={() => setSection(x.key)}
            className={`font-mono text-[11px] px-3 py-2 -mb-px border-b-2 shrink-0 transition-colors ${
              section === x.key ? 'text-cyan-glow border-cyan-glow' : 'text-neutral-500 border-transparent hover:text-neutral-300'
            }`}
          >
            {x.label}
          </button>
        ))}
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3 items-start">
        {gridZones.map((z) => (
          s.kind === 'mix' ? (
            <MiniMixCard key={z} title={`Fuel Mix · ${labelFor(z)}`} zone={z} />
          ) : (
            <MiniSeriesCard
              key={z}
              title={`${s.label} · ${labelFor(z)}`}
              series={s.series}
              zone={z}
              unit={s.unit}
              scale={s.scale}
              color={s.color}
            />
          )
        ))}
      </div>
    </div>
  )
}
