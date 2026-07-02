import { useState } from 'react'

// One-time, dismissible orientation for first-time visitors who land straight on
// the dashboard (bypassing the Landing page) and have no idea what the desk shows.
// Persists a "seen" flag in localStorage — same idiom as Panel.jsx collapse state.
const KEY = 'obsyd-power-intro-seen'

export default function PowerIntro() {
  const [seen, setSeen] = useState(() => {
    try {
      return localStorage.getItem(KEY) === '1'
    } catch {
      return false
    }
  })

  if (seen) return null

  const dismiss = () => {
    setSeen(true)
    try {
      localStorage.setItem(KEY, '1')
    } catch {
      /* localStorage unavailable — banner just won't persist dismissal */
    }
  }

  return (
    <div className="border border-cyan-glow/25 bg-cyan-glow/[0.04] rounded px-4 py-3 mb-3 flex items-start justify-between gap-3">
      <div className="min-w-0">
        <div className="font-mono text-[10px] text-cyan-glow tracking-wider mb-1">// NEW HERE?</div>
        <div className="font-mono text-[11px] text-neutral-400 leading-relaxed">
          This is the European power desk. The top-line reads whether a zone (DE-LU / FR / NL) is{' '}
          <span className="text-green-glow">CALM</span>,{' '}
          <span className="text-yellow-400">ELEVATED</span> or{' '}
          <span className="text-red-400">STRESSED</span> — a deviation vs its own recent history, not
          a forecast. Tap the info dot next to the state to see how it is derived; the panels below
          are the evidence.
        </div>
      </div>
      <button
        onClick={dismiss}
        aria-label="Dismiss intro"
        className="font-mono text-[12px] text-neutral-500 hover:text-neutral-300 shrink-0 leading-none px-1"
      >
        ✕
      </button>
    </div>
  )
}
