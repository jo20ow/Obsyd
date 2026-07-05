import useZones from '../hooks/useZones'
import ZoneSelector from './ZoneSelector'
import { useViewState } from '../context/ViewStateContext'

// gridstatus-style region strip: "Everywhere" (all-zones overview) + a few core
// zones as pills + the full ZoneSelector as a "more" dropdown, so it stays compact
// while scaling to all 37 zones. Drives the global ViewState zone.
const CORE = ['DE_LU', 'FR', 'NL', 'BE', 'ES', 'AT', 'PL']

function Pill({ active, onClick, children }) {
  return (
    <button
      onClick={onClick}
      className={`font-mono text-[11px] px-2.5 py-1 rounded-full border transition-colors ${
        active ? 'bg-cyan-glow/10 text-cyan-glow border-cyan-glow/40' : 'text-neutral-500 border-border hover:text-neutral-300'
      }`}
    >
      {children}
    </button>
  )
}

export default function RegionPills({ activeTab, onEverywhere, onPickZone }) {
  const { zone } = useViewState()
  const { zones } = useZones()
  const keys = new Set(zones.map((z) => z.key))
  const core = CORE.filter((k) => keys.has(k))
  const labelFor = (k) => zones.find((z) => z.key === k)?.label || k
  const everywhere = activeTab === 'europe'
  return (
    <div className="flex items-center gap-1.5 flex-wrap min-w-0">
      <Pill active={everywhere} onClick={onEverywhere}>Everywhere</Pill>
      {core.map((k) => (
        <Pill key={k} active={!everywhere && zone === k} onClick={() => onPickZone(k)}>{labelFor(k)}</Pill>
      ))}
      <ZoneSelector zone={zone} onChange={onPickZone} />
    </div>
  )
}
