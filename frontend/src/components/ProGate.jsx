import { useState } from 'react'
import { useAuth } from '../context/AuthContext'

export default function ProGate({ children, feature = 'This feature' }) {
  const { isPro } = useAuth()

  if (isPro) return children

  return (
    <div className="relative">
      <div className="pointer-events-none select-none" style={{ filter: 'blur(6px)', opacity: 0.4 }}>
        {children}
      </div>
      <div className="absolute inset-0 flex items-center justify-center">
        <UpgradePrompt feature={feature} />
      </div>
    </div>
  )
}

function UpgradePrompt({ feature }) {
  const { user } = useAuth()
  const [showLogin, setShowLogin] = useState(false)
  const [email, setEmail] = useState('')
  const [sending, setSending] = useState(false)
  const [sent, setSent] = useState(false)

  const handleLogin = (e) => {
    e.preventDefault()
    if (!email.trim()) return
    setSending(true)
    fetch('/api/auth/magic-link', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email.trim() }),
      credentials: 'include',
    })
      .then((r) => r.json())
      .then((d) => {
        if (d.status === 'ok') setSent(true)
      })
      .finally(() => setSending(false))
  }

  return (
    <div className="border border-cyan-glow/30 bg-[#0a0a12]/95 px-5 py-4 max-w-xs text-center">
      <div className="font-mono text-[10px] text-cyan-glow tracking-wider mb-2">
        OBSYD PRO
      </div>
      <div className="font-mono text-[11px] text-neutral-400 mb-3">
        {feature} requires Pro.
      </div>
      <div className="font-mono text-lg text-neutral-200 font-bold mb-1">
        €9<span className="text-neutral-500 text-xs font-normal">/month</span>
      </div>

      {user?.authenticated ? (
        <a
          href={user.checkout_url || '#'}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-block font-mono text-[10px] tracking-wider text-[#0a0a12] bg-cyan-glow px-4 py-2 mt-2 hover:bg-cyan-glow/80 transition-colors"
        >
          UPGRADE TO PRO
        </a>
      ) : showLogin ? (
        <div className="mt-2">
          {sent ? (
            <div className="font-mono text-[11px] text-emerald-400">
              Check your email for the login link.
            </div>
          ) : (
            <form onSubmit={handleLogin} className="flex gap-1.5">
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="email@example.com"
                className="flex-1 bg-surface border border-border rounded px-2 py-1.5 font-mono text-[10px] text-neutral-200 placeholder:text-neutral-600 focus:border-cyan-glow/40 outline-none min-w-0"
                disabled={sending}
              />
              <button
                type="submit"
                disabled={sending || !email.trim()}
                className="shrink-0 text-[9px] tracking-wider text-cyan-glow border border-cyan-glow/30 px-2 py-1.5 disabled:opacity-40"
              >
                {sending ? '...' : 'SEND'}
              </button>
            </form>
          )}
        </div>
      ) : (
        <button
          onClick={() => setShowLogin(true)}
          className="font-mono text-[10px] tracking-wider text-cyan-glow border border-cyan-glow/30 px-4 py-2 mt-2 hover:border-cyan-glow/60 transition-colors"
        >
          LOG IN / SIGN UP
        </button>
      )}
    </div>
  )
}
