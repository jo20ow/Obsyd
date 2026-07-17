import { useCallback, useEffect, useState } from 'react'
import { useAuth } from '../context/AuthContext'
import WatchlistPanel from './WatchlistPanel'

const API = '/api'

/**
 * Pro-tier alert-rule builder + notification inbox.
 *
 * Three sections in one panel:
 *   1. Existing rules (list with toggle/delete)
 *   2. Add-rule form (dynamic params per template)
 *   3. Notification inbox (recent triggers, mark-seen)
 *
 * Templates schema is fetched once from /api/alerts/templates and used
 * to render param inputs (enum -> select, number -> input). Validation
 * happens server-side; we only block obvious empty-required cases.
 */
export default function AlertRulesPanel() {
  const { user } = useAuth()
  const isLoggedIn = user?.authenticated
  const [templates, setTemplates] = useState(null)
  const [rules, setRules] = useState([])
  const [tier, setTier] = useState('free')
  const [trialRuleLimit, setTrialRuleLimit] = useState(3)
  const [notifications, setNotifications] = useState([])
  const [unseen, setUnseen] = useState(0)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const refresh = useCallback(() => {
    setError(null)
    Promise.all([
      fetch(`${API}/alerts/templates`).then((r) => (r.ok ? r.json() : {})),
      fetch(`${API}/alerts/rules`, { credentials: 'include' })
        .then((r) => (r.ok ? r.json() : { rules: [], tier: 'free', trial_rule_limit: 3 })),
      fetch(`${API}/alerts/notifications`, { credentials: 'include' })
        .then((r) => (r.ok ? r.json() : { events: [], unseen: 0 })),
    ])
      .then(([tmpl, rulesData, notif]) => {
        setTemplates(tmpl)
        setRules(rulesData.rules || [])
        setTier(rulesData.tier || 'free')
        setTrialRuleLimit(rulesData.trial_rule_limit || 3)
        setNotifications(notif.events || [])
        setUnseen(notif.unseen || 0)
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  // Anonymous visitors hit a login prompt — alerts are free but per-account.
  if (!isLoggedIn) {
    return (
      <div className="border border-border bg-surface rounded p-6 text-center font-mono">
        <div className="text-[10px] tracking-wider text-cyan-glow mb-2">ALERT RULES</div>
        <div className="text-[12px] text-neutral-400 mb-3 max-w-md mx-auto leading-relaxed">
          Custom supply-disruption alerts — free. Log in to set up your rules; every firing
          lands in your on-site alert inbox when something deviates.
        </div>
        <div className="text-[11px] text-neutral-500">
          Use <span className="text-cyan-glow">LOG IN</span> in the sidebar (a magic link, no password).
        </div>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="border border-border bg-surface rounded p-6 font-mono text-[10px] text-neutral-600 animate-pulse text-center">
        ALERTS // LOADING ...
      </div>
    )
  }

  const activeRuleCount = rules.filter((r) => r.is_active).length
  const trialCapReached = tier === 'trial' && activeRuleCount >= trialRuleLimit

  const toggleRule = async (rule) => {
    setBusy(true)
    try {
      const res = await fetch(`${API}/alerts/rules/${rule.id}`, {
        method: 'PATCH',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_active: !rule.is_active }),
      })
      if (!res.ok) throw new Error('toggle failed')
      refresh()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  const deleteRule = async (rule) => {
    if (!confirm(`Delete rule "${rule.name}"?`)) return
    setBusy(true)
    try {
      const res = await fetch(`${API}/alerts/rules/${rule.id}`, {
        method: 'DELETE',
        credentials: 'include',
      })
      if (!res.ok) throw new Error('delete failed')
      refresh()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  const markSeen = async (event) => {
    if (event.seen) return
    try {
      await fetch(`${API}/alerts/notifications/${event.id}/seen`, {
        method: 'POST',
        credentials: 'include',
      })
      refresh()
    } catch (e) {
      // soft fail — inbox is non-critical
      console.error('mark-seen:', e)
    }
  }

  return (
    <div className="space-y-3">
      {/* What you watch — drives the brief + feed, and the rules below */}
      <WatchlistPanel />

      {error && (
        <div className="border border-red-500/30 bg-red-500/5 px-4 py-2 text-[11px] text-red-300 font-mono">
          {error}
        </div>
      )}

      {/* Quota header */}
      <div className="flex items-center justify-between px-4 py-2 border border-border bg-surface rounded font-mono text-[10px] text-neutral-500">
        <div>
          {activeRuleCount} active rule{activeRuleCount === 1 ? '' : 's'} ·{' '}
          <span className="text-cyan-glow">{tier === 'trial' ? `trial cap ${trialRuleLimit}` : 'unlimited'}</span>
          {unseen > 0 && (
            <span className="ml-3 text-cyan-glow">· {unseen} new alert{unseen === 1 ? '' : 's'}</span>
          )}
        </div>
        <div className="text-neutral-700">{tier.toUpperCase()}</div>
      </div>

      {/* Add rule */}
      <AddRuleForm
        templates={templates}
        disabled={busy || trialCapReached}
        capWarning={trialCapReached ? `Trial cap reached (${trialRuleLimit}). Disable a rule or upgrade.` : null}
        onCreated={refresh}
        setError={setError}
      />

      {/* Existing rules */}
      <div className="border border-border bg-surface rounded">
        <div className="px-4 py-2 border-b border-border font-mono text-[10px] text-neutral-500">
          YOUR RULES ({rules.length})
        </div>
        {rules.length === 0 ? (
          <div className="px-4 py-6 text-center font-mono text-[11px] text-neutral-600">
            No rules yet. Use the form above to create your first one.
          </div>
        ) : (
          rules.map((rule) => (
            <RuleRow
              key={rule.id}
              rule={rule}
              templates={templates}
              onToggle={() => toggleRule(rule)}
              onDelete={() => deleteRule(rule)}
              disabled={busy}
            />
          ))
        )}
      </div>

      {/* Inbox */}
      <div className="border border-border bg-surface rounded">
        <div className="flex items-center justify-between px-4 py-2 border-b border-border font-mono text-[10px] text-neutral-500">
          <span>NOTIFICATION INBOX ({notifications.length})</span>
          {unseen > 0 && <span className="text-cyan-glow">{unseen} unread</span>}
        </div>
        {notifications.length === 0 ? (
          <div className="px-4 py-6 text-center font-mono text-[11px] text-neutral-600">
            No alerts triggered yet. They'll appear here as your rules match.
          </div>
        ) : (
          notifications.map((e) => (
            <button
              key={e.id}
              type="button"
              onClick={() => markSeen(e)}
              className={`block w-full text-left px-4 py-3 border-b border-border last:border-b-0 hover:bg-cyan-glow/[0.02] transition-colors ${
                e.seen ? 'opacity-60' : ''
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-[12px] text-neutral-200">
                  {!e.seen && <span className="inline-block w-1.5 h-1.5 rounded-full bg-cyan-glow mr-2" />}
                  {e.title}
                </span>
                <span className="font-mono text-[10px] text-neutral-600 shrink-0">
                  {fmtAgo(e.triggered_at)}
                </span>
              </div>
              <div className="font-mono text-[10px] text-neutral-500 mt-1 leading-relaxed">
                {e.detail}
              </div>
            </button>
          ))
        )}
      </div>
    </div>
  )
}

function AddRuleForm({ templates, disabled, capWarning, onCreated, setError }) {
  const [ruleType, setRuleType] = useState('')
  const [params, setParams] = useState({})
  const [name, setName] = useState('')
  const [submitting, setSubmitting] = useState(false)

  // Reset params when the template changes — defaults from schema.
  useEffect(() => {
    if (!ruleType || !templates?.[ruleType]) {
      setParams({})
      return
    }
    const schema = templates[ruleType].params_schema || {}
    const next = {}
    for (const [key, spec] of Object.entries(schema)) {
      if (spec.default !== undefined) next[key] = spec.default
      else if (spec.type === 'enum' && spec.options?.length) next[key] = spec.options[0]
    }
    setParams(next)
  }, [ruleType, templates])

  if (!templates) return null

  const submit = async (e) => {
    e.preventDefault()
    if (!ruleType) return
    setSubmitting(true)
    setError(null)
    try {
      const res = await fetch(`${API}/alerts/rules`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rule_type: ruleType, name, params }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `HTTP ${res.status}`)
      }
      setRuleType('')
      setParams({})
      setName('')
      onCreated()
    } catch (err) {
      setError(String(err.message || err))
    } finally {
      setSubmitting(false)
    }
  }

  const tpl = ruleType ? templates[ruleType] : null

  return (
    <form onSubmit={submit} className="border border-border bg-surface rounded p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="font-mono text-[10px] text-neutral-500 tracking-wider">
          + ADD RULE
        </div>
        {capWarning && (
          <div className="font-mono text-[10px] text-amber-400">{capWarning}</div>
        )}
      </div>

      <select
        value={ruleType}
        onChange={(e) => setRuleType(e.target.value)}
        disabled={disabled || submitting}
        className="w-full bg-[#0a0a12] border border-border rounded px-2.5 py-2 font-mono text-[11px] text-neutral-200 outline-none focus:border-cyan-glow/40"
      >
        <option value="">— pick a template —</option>
        {Object.entries(templates).map(([key, t]) => (
          <option key={key} value={key}>
            {t.label}
          </option>
        ))}
      </select>

      {tpl && (
        <>
          <div className="font-mono text-[10px] text-neutral-600 leading-relaxed">
            {tpl.summary}
          </div>

          <input
            type="text"
            placeholder={`name (defaults to "${tpl.label}")`}
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={disabled || submitting}
            className="w-full bg-[#0a0a12] border border-border rounded px-2.5 py-1.5 font-mono text-[11px] text-neutral-200 outline-none focus:border-cyan-glow/40"
          />

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {Object.entries(tpl.params_schema).map(([key, spec]) => (
              <ParamInput
                key={key}
                name={key}
                spec={spec}
                value={params[key]}
                onChange={(v) => setParams((p) => ({ ...p, [key]: v }))}
                disabled={disabled || submitting}
              />
            ))}
          </div>

          <button
            type="submit"
            disabled={disabled || submitting}
            className="px-4 py-2 font-mono text-[11px] tracking-wider bg-cyan-glow text-[#0a0a12] hover:bg-cyan-glow/90 disabled:opacity-50 transition-colors"
          >
            {submitting ? 'Saving …' : 'Create rule'}
          </button>
        </>
      )}
    </form>
  )
}

function ParamInput({ name, spec, value, onChange, disabled }) {
  const label = name.replace(/_/g, ' ')
  const required = spec.required ? ' *' : ''
  if (spec.type === 'enum') {
    return (
      <label className="block font-mono text-[10px] text-neutral-500">
        {label}{required}
        <select
          value={value ?? ''}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
          className="mt-1 w-full bg-[#0a0a12] border border-border rounded px-2 py-1.5 font-mono text-[11px] text-neutral-200 outline-none focus:border-cyan-glow/40"
        >
          {!spec.required && <option value="">—</option>}
          {spec.options.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      </label>
    )
  }
  if (spec.type === 'number') {
    return (
      <label className="block font-mono text-[10px] text-neutral-500">
        {label}{required}{' '}
        {(spec.min !== undefined || spec.max !== undefined) && (
          <span className="text-neutral-700">
            ({spec.min ?? '−∞'}–{spec.max ?? '∞'})
          </span>
        )}
        <input
          type="number"
          value={value ?? ''}
          min={spec.min}
          max={spec.max}
          step="any"
          onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))}
          disabled={disabled}
          className="mt-1 w-full bg-[#0a0a12] border border-border rounded px-2 py-1.5 font-mono text-[11px] text-neutral-200 outline-none focus:border-cyan-glow/40"
        />
      </label>
    )
  }
  return null
}

function RuleRow({ rule, templates, onToggle, onDelete, disabled }) {
  const tpl = templates?.[rule.rule_type]
  return (
    <div className="px-4 py-3 border-b border-border last:border-b-0 flex items-start gap-3">
      <div className="flex-1 min-w-0">
        <div className="font-mono text-[12px] text-neutral-200 flex items-center gap-2">
          {!rule.is_active && (
            <span className="text-[9px] tracking-wider text-neutral-600 border border-border px-1 py-0.5">
              PAUSED
            </span>
          )}
          {rule.name}
        </div>
        <div className="font-mono text-[10px] text-neutral-600 mt-0.5">
          {tpl?.label || rule.rule_type} ·{' '}
          {Object.entries(rule.params)
            .map(([k, v]) => `${k}=${v}`)
            .join(' · ')}
        </div>
        {rule.last_triggered_at && (
          <div className="font-mono text-[9px] text-neutral-700 mt-1">
            last triggered {fmtAgo(rule.last_triggered_at)}
            {rule.cooldown_until && new Date(rule.cooldown_until) > new Date() && (
              <> · cooldown until {new Date(rule.cooldown_until).toLocaleTimeString('en-GB', {
                hour: '2-digit', minute: '2-digit', timeZone: 'UTC',
              })} UTC</>
            )}
          </div>
        )}
      </div>
      <div className="flex flex-col gap-1 shrink-0">
        <button
          type="button"
          onClick={onToggle}
          disabled={disabled}
          className="font-mono text-[10px] text-cyan-glow/80 hover:text-cyan-glow disabled:opacity-50"
        >
          {rule.is_active ? 'pause' : 'resume'}
        </button>
        <button
          type="button"
          onClick={onDelete}
          disabled={disabled}
          className="font-mono text-[10px] text-red-400/80 hover:text-red-400 disabled:opacity-50"
        >
          delete
        </button>
      </div>
    </div>
  )
}

function fmtAgo(iso) {
  if (!iso) return ''
  const ms = Date.now() - new Date(iso).getTime()
  if (isNaN(ms)) return ''
  const mins = Math.floor(ms / 60000)
  if (mins < 1) return 'now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}
