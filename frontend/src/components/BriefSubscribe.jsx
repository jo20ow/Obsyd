import { useEffect, useState } from 'react'
import { useAuth } from '../context/AuthContext'

/**
 * Free daily-brief opt-in (Weg B retention hook). The brief leads with the
 * European power situation per zone. Login-gated (POST /api/email/subscribe
 * requires auth); unsubscribe is the standard link in the email footer.
 */
export default function BriefSubscribe() {
  const { user } = useAuth()
  const authed = !!user?.authenticated
  const [subscribed, setSubscribed] = useState(null) // null = unknown/loading
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!authed) return
    fetch('/api/email/subscription', { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setSubscribed(d ? !!d.subscribed : false))
      .catch(() => setSubscribed(false))
  }, [authed])

  const subscribe = async () => {
    setBusy(true)
    try {
      const r = await fetch('/api/email/subscribe', { method: 'POST', credentials: 'include' })
      if (r.ok) setSubscribed(true)
    } catch {
      /* leave state as-is; user can retry */
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="border border-border bg-surface rounded px-4 py-3 mb-3">
      <div className="font-mono text-[10px] text-neutral-500 tracking-wider mb-1">// DAILY BRIEF</div>
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="font-mono text-xs text-neutral-400">
          A free Mon–Fri email — the European power situation (DE-LU / FR / NL) plus the day's anomaly radar.
        </div>
        {!authed ? (
          <span className="font-mono text-[11px] text-neutral-500 shrink-0">Log in to subscribe</span>
        ) : subscribed ? (
          <span className="font-mono text-[11px] text-green-glow shrink-0">Subscribed ✓</span>
        ) : (
          <button
            onClick={subscribe}
            disabled={busy || subscribed === null}
            className="font-mono text-[11px] text-cyan-glow border border-cyan-glow/30 rounded px-3 py-1 hover:bg-cyan-glow/10 disabled:opacity-50 shrink-0"
          >
            {busy ? 'Subscribing…' : 'Get the free daily brief'}
          </button>
        )}
      </div>
    </div>
  )
}
