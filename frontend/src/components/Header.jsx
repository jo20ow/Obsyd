import { useState, useEffect } from 'react'
import SettingsPanel from './SettingsPanel'
import AuthButton from './AuthButton'
import { useMode } from '../context/ModeContext'

const MODES = [
  { key: 'crude', label: 'CRUDE' },
  { key: 'lng', label: 'LNG' },
  { key: 'all', label: 'ALL' },
]

export default function Header({ aisActive, gdeltActive, compactMode, onToggleCompact }) {
  const { mode, setMode } = useMode()
  const [health, setHealth] = useState(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)

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
          <div className="text-neutral-500 font-mono text-xs hidden md:block">
            // ENERGY MARKET INTELLIGENCE
          </div>
        </div>

        {/* Desktop nav */}
        <div className="hidden md:flex items-center gap-4">
          <div className="flex items-center border border-border rounded overflow-hidden">
            {MODES.map((m) => (
              <button
                key={m.key}
                onClick={() => setMode(m.key)}
                className={`font-mono text-[10px] tracking-wider px-2.5 py-1 transition-colors ${
                  mode === m.key
                    ? 'bg-cyan-glow/15 text-cyan-glow'
                    : 'text-neutral-600 hover:text-neutral-400'
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>
          <StatusDot label="EIA" ok={eiaOk} />
          <StatusDot label="FRED" ok={fredOk} />
          <StatusDot label="AIS" ok={aisOk} />
          <StatusDot label="GDELT" ok={gdeltOk} />
          {!compactMode && onToggleCompact && (
            <button
              onClick={onToggleCompact}
              className="font-mono text-[10px] text-neutral-600 hover:text-cyan-glow tracking-wider transition-colors border border-border hover:border-cyan-glow/30 px-2 py-1"
            >
              COMPACT
            </button>
          )}
          <AuthButton />
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

        {/* Mobile nav: auth + hamburger */}
        <div className="flex md:hidden items-center gap-3">
          <AuthButton />
          <button
            onClick={() => setMenuOpen((v) => !v)}
            className="text-neutral-400 hover:text-cyan-glow transition-colors font-mono text-lg"
            aria-label="Menu"
          >
            {menuOpen ? '✕' : '☰'}
          </button>
        </div>
      </header>

      {/* Mobile dropdown menu */}
      {menuOpen && (
        <div className="md:hidden border-b border-border bg-surface px-3 py-3 space-y-3">
          {/* Mode toggle */}
          <div className="flex items-center gap-2">
            <span className="font-mono text-[10px] text-neutral-600 tracking-wider">MODE</span>
            <div className="flex items-center border border-border rounded overflow-hidden">
              {MODES.map((m) => (
                <button
                  key={m.key}
                  onClick={() => { setMode(m.key); setMenuOpen(false) }}
                  className={`font-mono text-[10px] tracking-wider px-2.5 py-1 transition-colors ${
                    mode === m.key
                      ? 'bg-cyan-glow/15 text-cyan-glow'
                      : 'text-neutral-600 hover:text-neutral-400'
                  }`}
                >
                  {m.label}
                </button>
              ))}
            </div>
          </div>
          {/* Status dots */}
          <div className="flex items-center gap-4">
            <StatusDot label="EIA" ok={eiaOk} />
            <StatusDot label="FRED" ok={fredOk} />
            <StatusDot label="AIS" ok={aisOk} />
            <StatusDot label="GDELT" ok={gdeltOk} />
          </div>
          {/* Compact + Settings */}
          <div className="flex items-center gap-3">
            {!compactMode && onToggleCompact && (
              <button
                onClick={() => { onToggleCompact(); setMenuOpen(false) }}
                className="font-mono text-[10px] text-neutral-600 hover:text-cyan-glow tracking-wider transition-colors border border-border hover:border-cyan-glow/30 px-2 py-1"
              >
                COMPACT
              </button>
            )}
            <button
              onClick={() => { setSettingsOpen(true); setMenuOpen(false) }}
              className="font-mono text-[10px] text-neutral-600 hover:text-cyan-glow tracking-wider transition-colors border border-border hover:border-cyan-glow/30 px-2 py-1"
            >
              SETTINGS
            </button>
          </div>
        </div>
      )}

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
