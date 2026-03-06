import { useState, useEffect } from 'react'

const API = '/api'

export default function SettingsPanel({ open, onClose }) {
  const [settings, setSettings] = useState(null)
  const [saving, setSaving] = useState(false)
  const [primary, setPrimary] = useState('')
  const [fallback, setFallback] = useState('')

  useEffect(() => {
    if (!open) return
    fetch(`${API}/settings`)
      .then((r) => (r.ok ? r.json() : null))
      .then((s) => {
        setSettings(s)
        setPrimary(s?.price_provider || '')
        setFallback(s?.price_fallback || '')
      })
      .catch((e) => console.error('Settings fetch:', e))
  }, [open])

  if (!open) return null

  const providers = settings?.available_providers || []
  const credits = settings?.twelvedata_credits || {}

  const handleSave = () => {
    setSaving(true)
    fetch(`${API}/settings/provider`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ primary, fallback: fallback || null }),
    })
      .then((r) => r.json())
      .then((s) => {
        if (s.status === 'ok') {
          setSettings(s)
          setSaving(false)
        } else {
          alert(s.message || 'Failed to save')
          setSaving(false)
        }
      })
      .catch(() => setSaving(false))
  }

  const changed = primary !== settings?.price_provider || fallback !== settings?.price_fallback

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 bg-black/60 z-40" onClick={onClose} />

      {/* Panel */}
      <div className="fixed top-0 right-0 h-full w-80 bg-[#0a0a0f] border-l border-border z-50 overflow-y-auto">
        <div className="px-5 py-4 border-b border-border flex items-center justify-between">
          <span className="font-mono text-sm text-neutral-300">SETTINGS</span>
          <button
            onClick={onClose}
            className="font-mono text-neutral-600 hover:text-neutral-300 text-lg"
          >
            ×
          </button>
        </div>

        <div className="px-5 py-4 space-y-5">
          {/* Data Sources */}
          <div>
            <div className="font-mono text-[10px] text-neutral-600 tracking-wider mb-3">
              DATA SOURCES
            </div>

            <label className="block mb-3">
              <span className="font-mono text-xs text-neutral-400 block mb-1">Price Provider</span>
              <select
                value={primary}
                onChange={(e) => setPrimary(e.target.value)}
                className="w-full bg-surface border border-border rounded px-3 py-1.5 font-mono text-xs text-neutral-200 focus:border-cyan-glow/50 outline-none"
              >
                {providers.map((p) => (
                  <option key={p} value={p}>
                    {p === 'twelvedata' ? 'Twelve Data' : p === 'alphavantage' ? 'Alpha Vantage' : 'FRED'}
                  </option>
                ))}
              </select>
            </label>

            <label className="block mb-3">
              <span className="font-mono text-xs text-neutral-400 block mb-1">Fallback</span>
              <select
                value={fallback}
                onChange={(e) => setFallback(e.target.value)}
                className="w-full bg-surface border border-border rounded px-3 py-1.5 font-mono text-xs text-neutral-200 focus:border-cyan-glow/50 outline-none"
              >
                <option value="">None</option>
                {providers
                  .filter((p) => p !== primary)
                  .map((p) => (
                    <option key={p} value={p}>
                      {p === 'twelvedata' ? 'Twelve Data' : p === 'alphavantage' ? 'Alpha Vantage' : 'FRED'}
                    </option>
                  ))}
              </select>
            </label>

            {changed && (
              <button
                onClick={handleSave}
                disabled={saving}
                className="w-full font-mono text-xs bg-cyan-glow/20 text-cyan-glow border border-cyan-glow/30 rounded px-3 py-1.5 hover:bg-cyan-glow/30 transition-colors disabled:opacity-50"
              >
                {saving ? 'SAVING...' : 'APPLY'}
              </button>
            )}
          </div>

          {/* API Key Status */}
          <div>
            <div className="font-mono text-[10px] text-neutral-600 tracking-wider mb-3">
              API KEYS
            </div>
            <div className="space-y-2">
              <KeyStatus label="Twelve Data" set={settings?.twelvedata_key_set} />
              <KeyStatus label="Alpha Vantage" set={settings?.alphavantage_key_set} />
              <KeyStatus label="FRED" set={settings?.fred_key_set} />
            </div>
            <div className="font-mono text-[10px] text-neutral-600 mt-2">
              Keys are configured in .env on the server.
            </div>
          </div>

          {/* Twelve Data Credits */}
          {settings?.twelvedata_key_set && (
            <div>
              <div className="font-mono text-[10px] text-neutral-600 tracking-wider mb-2">
                TWELVE DATA CREDITS
              </div>
              <div className="flex items-end gap-2">
                <span className="font-mono text-2xl font-bold text-purple-400">
                  {credits.used || 0}
                </span>
                <span className="font-mono text-xs text-neutral-500 pb-0.5">
                  / {credits.limit || 800} today
                </span>
              </div>
              <div className="w-full h-1.5 bg-neutral-800 rounded-full mt-2">
                <div
                  className="h-1.5 rounded-full bg-purple-400 transition-all"
                  style={{ width: `${Math.min(100, ((credits.used || 0) / (credits.limit || 800)) * 100)}%` }}
                />
              </div>
            </div>
          )}

          {/* Active Provider Info */}
          <div>
            <div className="font-mono text-[10px] text-neutral-600 tracking-wider mb-2">
              STATUS
            </div>
            <div className="font-mono text-xs text-neutral-400">
              Primary: <span className="text-neutral-200">{displayName(settings?.price_provider)}</span>
            </div>
            {settings?.price_fallback && (
              <div className="font-mono text-xs text-neutral-400 mt-1">
                Fallback: <span className="text-neutral-200">{displayName(settings?.price_fallback)}</span>
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  )
}

function displayName(key) {
  if (key === 'twelvedata') return 'Twelve Data'
  if (key === 'alphavantage') return 'Alpha Vantage'
  if (key === 'fred') return 'FRED'
  return key || '—'
}

function KeyStatus({ label, set }) {
  return (
    <div className="flex items-center justify-between">
      <span className="font-mono text-xs text-neutral-400">{label}</span>
      <span className={`font-mono text-[10px] ${set ? 'text-green-glow' : 'text-neutral-600'}`}>
        {set ? '● SET' : '○ NOT SET'}
      </span>
    </div>
  )
}
