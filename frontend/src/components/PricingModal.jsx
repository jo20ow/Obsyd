import { useCallback, useEffect, useState } from 'react'
import { useAuth } from '../context/AuthContext'

const SELF_HOST_FEATURES = [
  'Full feature set, all 13 data sources',
  'Bring your own API keys (AISStream, EIA, FRED, …)',
  'No usage limits, no third-party billing',
  'You handle hosting + updates yourself',
]

const CLOUD_FREE_FEATURES = [
  'Full live dashboard on obsyd.dev',
  '30-day rolling history window',
  'Up to 3 saved alerts (email delivery)',
  'No API access, no data exports',
]

const CLOUD_PRO_FEATURES = [
  'Full history (back to 2019)',
  'Unlimited saved alerts',
  'API access (rate-limited)',
  'CSV / JSON data export',
  'Daily email brief (Mon–Fri, 07:00 UTC)',
  'Custom geofence zones beyond the 6 defaults',
]

const PRO_PRICE = '€15'
const PRO_PERIOD = '/month'
const PRO_YEAR_NOTE = '€149/year (−17%)'
const GITHUB_URL = 'https://github.com/jo20ow/Obsyd'

function daysUntil(iso) {
  if (!iso) return null
  const ms = new Date(iso).getTime() - Date.now()
  return Math.max(0, Math.ceil(ms / 86400000))
}

export default function PricingModal() {
  const {
    pricingOpen,
    closePricing,
    checkoutUrl,
    user,
    isPro,
    trialEndsAt,
    trialEligible,
    startTrial,
  } = useAuth()
  const [trialBusy, setTrialBusy] = useState(false)
  const [trialError, setTrialError] = useState(null)

  const handleClose = useCallback(() => {
    setTrialError(null)
    closePricing()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!pricingOpen) return
    const onKey = (e) => {
      if (e.key === 'Escape') handleClose()
    }
    window.addEventListener('keydown', onKey)
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = prevOverflow
    }
  }, [pricingOpen, handleClose])

  if (!pricingOpen) return null

  const isAnon = !user
  const trialDaysLeft = daysUntil(trialEndsAt)
  const onTrial = isPro && trialEndsAt

  // CTA decision tree (Cloud Pro flow only — self-host has its own button)
  let primary = null
  let secondary = null
  let footnote = null

  if (isPro && !onTrial) {
    primary = { label: 'You are already Pro', disabled: true }
    footnote = 'Manage your subscription from the email Lemon Squeezy sent you.'
  } else if (onTrial) {
    primary = {
      label: `Subscribe to keep Pro (${trialDaysLeft}d left in trial)`,
      href: checkoutUrl,
    }
    footnote = `Trial ends ${new Date(trialEndsAt).toLocaleDateString()}. Subscribing now converts seamlessly — no double-billing.`
  } else if (isAnon) {
    primary = checkoutUrl
      ? { label: 'Subscribe to Cloud Pro', href: checkoutUrl }
      : { label: 'Pricing not yet available', disabled: true }
    footnote =
      'Already have an account? Use LOG IN in the header — the 14-day in-app trial is for signed-in users only.'
  } else if (trialEligible) {
    primary = {
      label: trialBusy ? 'Starting…' : 'Start 14-day free trial',
      onClick: async () => {
        setTrialBusy(true)
        setTrialError(null)
        const result = await startTrial()
        setTrialBusy(false)
        if (!result.ok) {
          setTrialError(result.detail?.detail || 'Could not start trial — please try again.')
          return
        }
        handleClose()
      },
      disabled: trialBusy,
    }
    secondary = checkoutUrl
      ? { label: `or subscribe (${PRO_PRICE}${PRO_PERIOD})`, href: checkoutUrl }
      : null
    footnote = 'No card required for the trial. We notify you 3 days before it ends.'
  } else {
    primary = checkoutUrl
      ? { label: 'Subscribe to Cloud Pro', href: checkoutUrl }
      : { label: 'Pricing not yet available', disabled: true }
    footnote = 'Trial already used on this account — subscribe directly to reactivate.'
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm"
      onClick={closePricing}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="relative w-full max-w-5xl max-h-[90vh] overflow-y-auto bg-[#0a0a12] border border-cyan-glow/30 rounded-sm font-mono"
      >
        <button
          type="button"
          onClick={closePricing}
          aria-label="Close"
          className="absolute top-3 right-3 text-neutral-500 hover:text-neutral-300 text-lg leading-none p-1"
        >
          ×
        </button>

        <div className="px-6 pt-6 pb-2 border-b border-border">
          <div className="text-[10px] tracking-[3px] text-cyan-glow mb-1">OBSYD PRICING</div>
          <div className="text-sm text-neutral-300">
            Open source under AGPL-3.0 — self-host free, or use the hosted cloud.
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-px bg-border">
          {/* SELF-HOST */}
          <div className="bg-[#0a0a12] p-6 flex flex-col">
            <div className="text-[10px] tracking-widest text-neutral-500 mb-1">SELF-HOST</div>
            <div className="text-2xl text-neutral-200 mb-1">€0</div>
            <div className="text-[10px] text-neutral-600 mb-5">AGPL-3.0 · your infra</div>
            <ul className="space-y-2.5 flex-1">
              {SELF_HOST_FEATURES.map((f) => (
                <li key={f} className="text-[11px] text-neutral-400 leading-relaxed flex gap-2">
                  <span className="text-neutral-600 mt-0.5 shrink-0">·</span>
                  <span>{f}</span>
                </li>
              ))}
            </ul>
            <a
              href={GITHUB_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-5 px-3 py-2 text-[10px] tracking-wider border border-border text-neutral-400 hover:text-cyan-glow hover:border-cyan-glow/40 transition-colors text-center"
            >
              View on GitHub →
            </a>
          </div>

          {/* CLOUD FREE */}
          <div className="bg-[#0a0a12] p-6 flex flex-col">
            <div className="text-[10px] tracking-widest text-neutral-500 mb-1">CLOUD FREE</div>
            <div className="text-2xl text-neutral-200 mb-1">€0</div>
            <div className="text-[10px] text-neutral-600 mb-5">on obsyd.dev · no card</div>
            <ul className="space-y-2.5 flex-1">
              {CLOUD_FREE_FEATURES.map((f) => (
                <li key={f} className="text-[11px] text-neutral-400 leading-relaxed flex gap-2">
                  <span className="text-neutral-600 mt-0.5 shrink-0">·</span>
                  <span>{f}</span>
                </li>
              ))}
            </ul>
            <a
              href="/app"
              className="mt-5 px-3 py-2 text-[10px] tracking-wider border border-border text-neutral-400 hover:text-cyan-glow hover:border-cyan-glow/40 transition-colors text-center"
            >
              Open the dashboard →
            </a>
          </div>

          {/* CLOUD PRO */}
          <div className="bg-[#0a0a12] p-6 relative flex flex-col">
            <div className="absolute top-3 right-3 text-[9px] tracking-[2px] text-cyan-glow bg-cyan-glow/10 px-2 py-0.5 border border-cyan-glow/30 rounded-sm">
              {onTrial ? `TRIAL · ${trialDaysLeft}d` : 'RECOMMENDED'}
            </div>
            <div className="text-[10px] tracking-widest text-cyan-glow mb-1">CLOUD PRO</div>
            <div className="text-2xl text-neutral-100 mb-1">
              {PRO_PRICE}
              <span className="text-sm text-neutral-500">{PRO_PERIOD}</span>
            </div>
            <div className="text-[10px] text-neutral-600 mb-5">or {PRO_YEAR_NOTE}</div>
            <ul className="space-y-2.5 flex-1">
              {CLOUD_PRO_FEATURES.map((f) => (
                <li key={f} className="text-[11px] text-neutral-300 leading-relaxed flex gap-2">
                  <span className="text-cyan-glow mt-0.5 shrink-0">+</span>
                  <span>{f}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>

        <div className="px-6 py-5 border-t border-border flex flex-col gap-3">
          {trialError && (
            <div className="text-[10px] text-red-400 border border-red-500/30 bg-red-500/5 px-3 py-2">
              {trialError}
            </div>
          )}
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="text-[10px] text-neutral-500 leading-relaxed sm:max-w-md">
              {footnote || 'Payments via Lemon Squeezy (EU-VAT handled, cancel anytime).'}
              {' '}OBSYD is open source — your data stays auditable.
            </div>
            <div className="flex flex-col items-stretch gap-1 sm:items-end shrink-0">
              {renderCta(primary)}
              {secondary && (
                <a
                  href={secondary.href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[10px] text-neutral-500 hover:text-cyan-glow transition-colors text-center sm:text-right"
                >
                  {secondary.label}
                </a>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function renderCta(cta) {
  if (!cta) return null
  if (cta.disabled || (!cta.href && !cta.onClick)) {
    return (
      <button
        type="button"
        disabled
        className="px-5 py-2.5 text-[11px] tracking-wider border border-border text-neutral-600 cursor-not-allowed"
      >
        {cta.label}
      </button>
    )
  }
  if (cta.href) {
    return (
      <a
        href={cta.href}
        target="_blank"
        rel="noopener noreferrer"
        className="px-5 py-2.5 text-[11px] tracking-wider bg-cyan-glow text-[#0a0a12] hover:bg-cyan-glow/90 transition-colors text-center font-semibold"
      >
        {cta.label} →
      </a>
    )
  }
  return (
    <button
      type="button"
      onClick={cta.onClick}
      className="px-5 py-2.5 text-[11px] tracking-wider bg-cyan-glow text-[#0a0a12] hover:bg-cyan-glow/90 transition-colors text-center font-semibold"
    >
      {cta.label} →
    </button>
  )
}
