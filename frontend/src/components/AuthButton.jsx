import { useState } from 'react'
import { useAuth } from '../context/AuthContext'

export default function AuthButton() {
  const { user, isPro, logout } = useAuth()
  const [showLogin, setShowLogin] = useState(false)
  const [email, setEmail] = useState('')
  const [sending, setSending] = useState(false)
  const [sent, setSent] = useState(false)

  if (user?.authenticated) {
    return (
      <div className="flex items-center gap-2 font-mono text-[10px]">
        {isPro && (
          <span className="text-cyan-glow border border-cyan-glow/30 px-1.5 py-0.5 tracking-wider">
            PRO
          </span>
        )}
        <span className="text-neutral-500 hidden sm:inline">{user.email}</span>
        <button
          onClick={logout}
          className="text-neutral-600 hover:text-neutral-400 transition-colors"
        >
          LOGOUT
        </button>
      </div>
    )
  }

  if (showLogin) {
    if (sent) {
      return (
        <span className="font-mono text-[10px] text-emerald-400">
          Check email for login link
        </span>
      )
    }
    return (
      <form
        onSubmit={(e) => {
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
            .then((d) => { if (d.status === 'ok') setSent(true) })
            .finally(() => setSending(false))
        }}
        className="flex items-center gap-1.5"
      >
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="email"
          className="bg-surface border border-border rounded px-2 py-1 font-mono text-[10px] text-neutral-200 placeholder:text-neutral-600 focus:border-cyan-glow/40 outline-none w-36"
          disabled={sending}
          autoFocus
        />
        <button
          type="submit"
          disabled={sending || !email.trim()}
          className="text-[9px] font-mono tracking-wider text-cyan-glow border border-cyan-glow/30 px-2 py-1 disabled:opacity-40"
        >
          {sending ? '...' : 'GO'}
        </button>
        <button
          type="button"
          onClick={() => setShowLogin(false)}
          className="text-neutral-600 hover:text-neutral-400 text-[10px] font-mono"
        >
          ✕
        </button>
      </form>
    )
  }

  return (
    <button
      onClick={() => setShowLogin(true)}
      className="font-mono text-[10px] text-neutral-600 hover:text-cyan-glow tracking-wider transition-colors border border-border hover:border-cyan-glow/30 px-2 py-1"
    >
      LOG IN
    </button>
  )
}
