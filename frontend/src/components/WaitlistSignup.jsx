import { useState } from 'react'

const API = '/api'

export default function WaitlistSignup() {
  const [email, setEmail] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = (e) => {
    e.preventDefault()
    if (!email.trim()) return
    setError('')
    setSubmitting(true)
    fetch(`${API}/waitlist`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email.trim(), tier: 'pro' }),
    })
      .then((r) => r.json())
      .then((d) => {
        if (d.status === 'ok') {
          setDone(true)
          setEmail('')
        } else {
          setError(d.detail?.[0]?.msg || 'Invalid email')
        }
      })
      .catch(() => setError('Network error'))
      .finally(() => setSubmitting(false))
  }

  return (
    <div className="border border-cyan-glow/20 bg-cyan-glow/[0.03] px-4 py-3">
      <div className="text-[10px] text-cyan-glow/80 tracking-wider mb-1.5">
        OBSYD PRO — COMING SOON
      </div>
      <div className="text-[10px] text-neutral-500 leading-relaxed mb-2.5">
        LNG Tracking, Crack Spreads, Smart Alerts, Daily Briefing Email.
      </div>

      {done ? (
        <div className="text-[11px] text-emerald-400 font-bold">
          You're on the list.
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="flex gap-2">
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="email@example.com"
            className="flex-1 bg-surface border border-border rounded px-2.5 py-1.5 font-mono text-[11px] text-neutral-200 placeholder:text-neutral-600 focus:border-cyan-glow/40 outline-none min-w-0"
            disabled={submitting}
          />
          <button
            type="submit"
            disabled={submitting || !email.trim()}
            className="shrink-0 text-[10px] tracking-wider text-cyan-glow border border-cyan-glow/30 hover:border-cyan-glow/60 px-3 py-1.5 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {submitting ? '...' : 'JOIN WAITLIST'}
          </button>
        </form>
      )}

      {error && (
        <div className="text-[10px] text-red-400 mt-1.5">{error}</div>
      )}
    </div>
  )
}
