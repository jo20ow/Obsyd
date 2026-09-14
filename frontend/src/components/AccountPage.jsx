import { useCallback, useEffect, useState } from 'react'
import DocShell, { LinkButton, SectionLabel } from './doc/DocShell'
import { useAuth } from '../context/AuthContext'

const API = '/api'
const btn = 'font-mono text-[10px] tracking-wider border rounded px-2 py-1 transition-colors'

/**
 * /account — API-key management for logged-in users (the backend at
 * /api/v1/keys existed for a while with curl as the only client; the survey
 * called that out). Keys are OPTIONAL — the public API stays keyless — and
 * exist for usage tracking and future higher-throughput tiers. The raw key is
 * shown exactly once, matching the server's storage model (hash only).
 */
export default function AccountPage() {
  const { user, loading: authLoading, logout } = useAuth()
  const [keys, setKeys] = useState(null)
  const [usage, setUsage] = useState(null)
  const [label, setLabel] = useState('')
  const [freshKey, setFreshKey] = useState(null) // shown once after creation
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  const refresh = useCallback(() => {
    fetch(`${API}/v1/keys`, { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d) => setKeys(d.keys || []))
      .catch(() => setKeys(null))
    fetch(`${API}/v1/keys/usage`, { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d) => setUsage(d.usage || []))
      .catch(() => setUsage(null))
  }, [])

  useEffect(() => {
    if (user?.authenticated) refresh()
  }, [user, refresh])

  const create = (e) => {
    e.preventDefault()
    setBusy(true); setErr(null)
    fetch(`${API}/v1/keys`, {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label: label.trim() || null }),
    })
      .then(async (r) => {
        const d = await r.json()
        if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`)
        setFreshKey(d); setLabel(''); refresh()
      })
      .catch((e2) => setErr(String(e2.message || e2)))
      .finally(() => setBusy(false))
  }

  const revoke = (id) => {
    fetch(`${API}/v1/keys/${id}`, { method: 'DELETE', credentials: 'include' })
      .then(() => refresh())
  }

  if (!authLoading && !user?.authenticated) {
    return (
      <DocShell maxWidth="max-w-md">
        <SectionLabel className="mb-4">ACCOUNT</SectionLabel>
        <div className="bg-surface border border-border rounded p-6 space-y-3">
          <p className="text-[12px] text-neutral-400 leading-relaxed">
            API keys, usage and alert rules live behind a free account — a magic
            link, no password, no card.
          </p>
          <LinkButton href="/login" primary>Log in →</LinkButton>
        </div>
      </DocShell>
    )
  }

  return (
    <DocShell maxWidth="max-w-3xl">
      <div className="flex items-baseline justify-between gap-4 mb-6">
        <SectionLabel>ACCOUNT · API KEYS</SectionLabel>
        {user?.email && (
          <span className="font-mono text-[10px] text-neutral-600">
            {user.email} · <button onClick={logout} className="hover:text-neutral-400">log out</button>
          </span>
        )}
      </div>

      <p className="text-[12px] text-neutral-500 leading-relaxed max-w-2xl mb-6">
        Keys are optional — the public API works without one. A key identifies your
        requests (send it as <code className="font-mono text-[11px]">X-Api-Key</code> or{' '}
        <code className="font-mono text-[11px]">Authorization: Bearer</code>) and gives you the
        usage view below. Up to 5 active keys; revoking is immediate.
      </p>

      {freshKey && (
        <div className="border border-emerald-500/40 bg-surface rounded p-4 mb-6">
          <div className="font-mono text-[10px] text-emerald-400 mb-2 smallcaps">
            NEW KEY — SHOWN ONLY ONCE, STORE IT NOW
          </div>
          <div className="flex items-center gap-2">
            <code className="font-mono text-[12px] text-neutral-200 break-all">{freshKey.key}</code>
            <button
              className={`${btn} border-emerald-500/40 text-emerald-400 shrink-0`}
              onClick={() => navigator.clipboard?.writeText(freshKey.key)}
            >
              COPY
            </button>
          </div>
        </div>
      )}

      <form onSubmit={create} className="flex items-center gap-2 mb-6">
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="label (optional, e.g. 'research pipeline')"
          maxLength={80}
          className="flex-1 bg-surface border border-border rounded px-3 py-1.5 font-mono text-[11px] text-neutral-200 placeholder:text-neutral-600 focus:border-cyan-glow/40 outline-none"
        />
        <button type="submit" disabled={busy}
          className={`${btn} border-cyan-glow/30 text-cyan-glow hover:bg-cyan-glow/10 disabled:opacity-40`}>
          {busy ? '…' : 'CREATE KEY'}
        </button>
      </form>
      {err && <div className="font-mono text-[10px] text-amber-400 mb-4">{err}</div>}

      <div className="bg-surface border border-border rounded mb-8 overflow-x-auto">
        <table className="w-full font-mono text-[11px]">
          <thead>
            <tr className="text-[9px] text-neutral-600 uppercase tracking-wider border-b border-border/60">
              <th className="text-left px-3 py-2">Key</th>
              <th className="text-left px-3 py-2">Label</th>
              <th className="text-left px-3 py-2">Created</th>
              <th className="text-left px-3 py-2">Last used</th>
              <th className="text-right px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {(keys || []).map((k) => (
              <tr key={k.id} className={`border-b border-border/30 ${k.revoked_at ? 'opacity-50' : ''}`}>
                <td className="px-3 py-2 text-neutral-300">{k.prefix}…</td>
                <td className="px-3 py-2 text-neutral-400">{k.label || '—'}</td>
                <td className="px-3 py-2 text-neutral-500">{k.created_at?.slice(0, 10)}</td>
                <td className="px-3 py-2 text-neutral-500">
                  {k.revoked_at ? `revoked ${k.revoked_at.slice(0, 10)}` : (k.last_used_at?.slice(0, 16).replace('T', ' ') || 'never')}
                </td>
                <td className="px-3 py-2 text-right">
                  {!k.revoked_at && (
                    <button onClick={() => revoke(k.id)}
                      className={`${btn} border-border text-neutral-500 hover:text-red-400 hover:border-red-400/40`}>
                      REVOKE
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {keys?.length === 0 && (
              <tr><td colSpan={5} className="px-3 py-3 text-neutral-600">No keys yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <SectionLabel className="mb-3">USAGE · LAST 30 DAYS</SectionLabel>
      <div className="bg-surface border border-border rounded overflow-x-auto">
        <table className="w-full font-mono text-[11px]">
          <thead>
            <tr className="text-[9px] text-neutral-600 uppercase tracking-wider border-b border-border/60">
              <th className="text-left px-3 py-2">Day</th>
              <th className="text-left px-3 py-2">Key</th>
              <th className="text-right px-3 py-2">Requests</th>
              <th className="text-right px-3 py-2" title="Data points served — rows across /series, /snapshot and downloads">Points</th>
            </tr>
          </thead>
          <tbody>
            {(usage || []).map((r) => (
              <tr key={`${r.day}-${r.key_id}`} className="border-b border-border/30">
                <td className="px-3 py-2 text-neutral-400">{r.day}</td>
                <td className="px-3 py-2 text-neutral-500">{r.prefix}…</td>
                {/* Fixed en-US grouping: the browser's locale turned 635476
                    points into "635.476", which reads as ~635 to half the
                    audience — a metering number must be unambiguous. */}
                <td className="px-3 py-2 text-right text-neutral-300">{r.requests.toLocaleString('en-US')}</td>
                <td className="px-3 py-2 text-right text-neutral-300">{r.points.toLocaleString('en-US')}</td>
              </tr>
            ))}
            {usage?.length === 0 && (
              <tr><td colSpan={4} className="px-3 py-3 text-neutral-600">No keyed requests yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </DocShell>
  )
}
