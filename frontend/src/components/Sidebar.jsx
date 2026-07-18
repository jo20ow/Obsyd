import { useEffect, useState } from 'react'
import AuthButton from './AuthButton'
import SettingsPanel from './SettingsPanel'
import { useTheme } from '../context/ThemeContext'

// gridstatus-style left rail: brand, section nav, then utilities (⌘K, theme,
// settings, auth, collector health). Section keys mirror the TABS array; labels are
// friendlier here (europe → "Live"). Reuses AuthButton + SettingsPanel + useTheme.

const LABELS = { europe: 'Live', energy: 'Power', analytics: 'Analytics', gas: 'Gas', explore: 'Explore', alerts: 'Alerts' }

function Icon({ name }) {
  const p = { width: 15, height: 15, viewBox: '0 0 16 16', fill: 'none', stroke: 'currentColor', strokeWidth: 1.4, strokeLinecap: 'round', strokeLinejoin: 'round' }
  switch (name) {
    case 'europe': return (<svg {...p}><line x1="2.5" y1="4" x2="13.5" y2="4" /><line x1="2.5" y1="8" x2="13.5" y2="8" /><line x1="2.5" y1="12" x2="13.5" y2="12" /></svg>)
    case 'energy': return (<svg {...p}><polygon points="9 2 4 9 7.5 9 7 14 12 6.5 8.5 6.5 9 2" fill="currentColor" stroke="none" /></svg>)
    case 'analytics': return (<svg {...p}><polyline points="2 13 6 8 9 10.5 14 4" /><line x1="2.2" y1="2" x2="2.2" y2="14" /><line x1="2.2" y1="14" x2="14" y2="14" /></svg>)
    case 'gas': return (<svg {...p}><path d="M8 2c2.2 3 3.2 4.4 3.2 7a3.2 3.2 0 11-6.4 0c0-1.2.6-2.2 1.3-3 .2 1 .8 1.6 1.5 1.8C6.8 6 8 4.4 8 2z" /></svg>)
    case 'explore': return (<svg {...p}><circle cx="7" cy="7" r="4" /><line x1="10" y1="10" x2="14" y2="14" /></svg>)
    case 'alerts': return (<svg {...p}><path d="M4.5 7a3.5 3.5 0 017 0c0 3 1 4 1 4H3.5s1-1 1-4z" /><path d="M6.5 13.5a1.5 1.5 0 003 0" /></svg>)
    default: return <span className="inline-block w-[15px]" />
  }
}

function StatusDot({ label, ok }) {
  return (
    <div className="flex items-center gap-1.5 font-mono text-[10px]">
      <div className={`w-1.5 h-1.5 rounded-full ${ok ? 'bg-green-glow shadow-[0_0_4px_var(--color-green-glow)]' : 'bg-neutral-600'}`} />
      <span className={ok ? 'text-neutral-400' : 'text-neutral-600'}>{label}</span>
    </div>
  )
}

function SidebarContent({ tabs, activeTab, onNavigate, onOpenPalette, onOpenSettings }) {
  const { theme, toggle } = useTheme()
  const [health, setHealth] = useState(null)

  useEffect(() => {
    const c = new AbortController()
    const poll = () => fetch('/api/health/collectors', { signal: c.signal })
      .then((r) => (r.ok ? r.json() : null)).then(setHealth).catch(() => {})
    poll()
    const id = setInterval(poll, 60_000)
    return () => { clearInterval(id); c.abort() }
  }, [])

  const isMac = typeof navigator !== 'undefined' && /Mac/i.test(navigator.platform || '')

  return (
    <div className="flex flex-col h-full">
      <div className="px-4 py-4 border-b border-border">
        <div className="font-mono text-lg font-bold tracking-widest text-cyan-glow">OBSYD</div>
        <div className="font-mono text-[10px] text-neutral-600 tracking-wider">European Electricity Desk</div>
      </div>

      <nav className="flex-1 overflow-y-auto scrollbar-hidden px-2 py-3 space-y-0.5">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => onNavigate(t.key)}
            className={`w-full flex items-center gap-2.5 px-3 py-2 rounded font-mono text-[12px] tracking-wide transition-colors ${
              activeTab === t.key ? 'bg-cyan-glow/10 text-cyan-glow' : 'text-neutral-400 hover:text-neutral-200 hover:bg-white/[0.04]'
            }`}
          >
            <Icon name={t.key} />
            <span>{LABELS[t.key] || t.label}</span>
          </button>
        ))}
      </nav>

      <div className="border-t border-border px-2 py-3 space-y-2">
        <button
          onClick={onOpenPalette}
          className="w-full flex items-center justify-between px-3 py-1.5 rounded border border-border text-neutral-400 hover:text-cyan-glow hover:border-cyan-glow/40 font-mono text-[11px] transition-colors"
        >
          <span>Jump to…</span>
          <span className="text-neutral-600">{isMac ? '⌘' : 'Ctrl'} K</span>
        </button>
        <div className="flex items-center gap-2 px-1">
          <button
            onClick={toggle}
            title="Toggle light / dark theme"
            className="font-mono text-[10px] px-2 py-1 rounded border border-border text-neutral-500 hover:text-cyan-glow hover:border-cyan-glow/40 transition-colors"
          >
            {theme === 'light' ? '☾ Dark' : '☀ Light'}
          </button>
          <button
            onClick={onOpenSettings}
            title="Settings"
            className="font-mono text-[10px] px-2 py-1 rounded border border-border text-neutral-500 hover:text-cyan-glow hover:border-cyan-glow/40 transition-colors"
          >
            ⚙ Settings
          </button>
        </div>
        <div className="px-1"><AuthButton /></div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-1 pt-1">
          <StatusDot label="PRICES" ok={health?.price_qh ?? false} />
          <StatusDot label="FLOWS" ok={health?.power_flows ?? false} />
          <StatusDot label="OUTAGES" ok={health?.power_outages ?? false} />
          <StatusDot label="GAS" ok={health?.gas_balance ?? false} />
        </div>
        <div className="px-1 pt-2 font-mono text-[9px] text-neutral-700">
          <a href="/impressum" className="hover:text-neutral-500">Impressum</a>
          {' · '}
          <a href="/datenschutz" className="hover:text-neutral-500">Datenschutz</a>
        </div>
      </div>
    </div>
  )
}

export default function Sidebar({ tabs, activeTab, onNavigate, onOpenPalette, open, onClose }) {
  const [settingsOpen, setSettingsOpen] = useState(false)
  const content = (
    <SidebarContent
      tabs={tabs}
      activeTab={activeTab}
      onNavigate={onNavigate}
      onOpenPalette={onOpenPalette}
      onOpenSettings={() => setSettingsOpen(true)}
    />
  )
  return (
    <>
      {/* Desktop rail */}
      <aside className="hidden lg:block w-56 shrink-0 border-r border-border h-screen sticky top-0 bg-surface">
        {content}
      </aside>
      {/* Mobile drawer */}
      {open && (
        <div className="lg:hidden fixed inset-0 z-50">
          <div className="absolute inset-0 bg-black/60" onClick={onClose} />
          <div className="relative w-64 max-w-[80vw] h-full border-r border-border bg-surface">
            {content}
          </div>
        </div>
      )}
      <SettingsPanel open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </>
  )
}
