import { useState, useEffect } from 'react'
import SettingsPanel from './SettingsPanel'

export default function Header({ aisActive, gdeltActive }) {
  const [health, setHealth] = useState(null)
  const [settingsOpen, setSettingsOpen] = useState(false)

  useEffect(() => {
    function poll() {
      fetch('/api/health/collectors')
        .then((r) => (r.ok ? r.json() : null))
        .then(setHealth)
        .catch((e) => console.error('Health poll:', e))
    }
    poll()
    const id = setInterval(poll, 60_000)
    return () => clearInterval(id)
  }, [])

  const eiaOk = health?.eia ?? false
  const fredOk = health?.fred ?? false
  const aisOk = health?.ais ?? aisActive
  const gdeltOk = health?.gdelt ?? gdeltActive

  return (
    <>
      <header className="flex items-center justify-between border-b border-border pb-3">
        <div className="flex items-center gap-3">
          <div className="text-cyan-glow font-mono text-xl font-bold tracking-widest">
            OBSYD
          </div>
          <div className="text-neutral-500 font-mono text-xs hidden sm:block">
            // ENERGY MARKET INTELLIGENCE
          </div>
        </div>
        <div className="flex items-center gap-4">
          <StatusDot label="EIA" ok={eiaOk} />
          <StatusDot label="FRED" ok={fredOk} />
          <StatusDot label="AIS" ok={aisOk} />
          <StatusDot label="GDELT" ok={gdeltOk} />
          <button
            onClick={() => setSettingsOpen(true)}
            className="text-neutral-600 hover:text-neutral-300 transition-colors ml-1"
            title="Settings"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="8" cy="8" r="2.5" />
              <path d="M13.5 8a5.5 5.5 0 00-.3-1.2l1.3-1-.8-1.4-1.5.6a5.5 5.5 0 00-1-1l.6-1.5-1.4-.8-1 1.3A5.5 5.5 0 008 2.5V1H6.5v1.5a5.5 5.5 0 00-1.2.3l-1-1.3-1.4.8.6 1.5a5.5 5.5 0 00-1 1L1 4.2l-.8 1.4 1.3 1A5.5 5.5 0 001.2 8H0v1.5h1.2a5.5 5.5 0 00.3 1.2l-1.3 1 .8 1.4 1.5-.6a5.5 5.5 0 001 1l-.6 1.5 1.4.8 1-1.3a5.5 5.5 0 001.2.3V16H8v-1.5a5.5 5.5 0 001.2-.3l1 1.3 1.4-.8-.6-1.5a5.5 5.5 0 001-1l1.5.6.8-1.4-1.3-1a5.5 5.5 0 00.3-1.2H16V8h-2.5z" />
            </svg>
          </button>
        </div>
      </header>
      <SettingsPanel open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </>
  )
}

function StatusDot({ label, ok }) {
  return (
    <div className="flex items-center gap-1.5 font-mono text-xs">
      <div
        className={`w-1.5 h-1.5 rounded-full ${
          ok ? 'bg-green-glow shadow-[0_0_4px_var(--color-green-glow)]' : 'bg-neutral-600'
        }`}
      />
      <span className={ok ? 'text-neutral-400' : 'text-neutral-600'}>{label}</span>
    </div>
  )
}
