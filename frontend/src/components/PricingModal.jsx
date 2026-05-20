import { useEffect } from 'react'
import { useAuth } from '../context/AuthContext'

const FREE_FEATURES = [
  'Live AIS vessel tracking (6 chokepoints)',
  'PortWatch chokepoint transit counts',
  'Crude & natural gas spot prices',
  'Weekly market briefing email',
  'Public correlation engine read-out',
]

const PRO_FEATURES = [
  'Daily briefing email (Mon–Fri, 07:00 UTC)',
  'Floating storage & STS transfer alerts',
  'Crack spreads + related energy equities overlay',
  'Market Intelligence Report (5-section narrative)',
  'Custom flow-anomaly alerts via email',
  'Priority data refresh & extended history',
]

const PRO_PRICE = '19,90 €'
const PRO_PERIOD = '/Monat'
const PRO_YEAR_NOTE = '199 €/Jahr (−17 %)'

export default function PricingModal() {
  const { pricingOpen, closePricing, checkoutUrl, user, isPro } = useAuth()

  useEffect(() => {
    if (!pricingOpen) return
    const onKey = (e) => {
      if (e.key === 'Escape') closePricing()
    }
    window.addEventListener('keydown', onKey)
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = ''
    }
  }, [pricingOpen, closePricing])

  if (!pricingOpen) return null

  // Pro users can manage their subscription from the same modal eventually,
  // but for now we redirect them to their LS customer portal via update_url.
  // For free users (authed or anon), show the checkout link.
  const isAnon = !user
  const ctaLabel = isPro
    ? 'You are already Pro'
    : isAnon
      ? 'Sign up & start Pro'
      : 'Upgrade to Pro'

  const ctaHref = checkoutUrl || '#'
  const ctaDisabled = isPro || !checkoutUrl

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm"
      onClick={closePricing}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="relative w-full max-w-3xl max-h-[90vh] overflow-y-auto bg-[#0a0a12] border border-cyan-glow/30 rounded-sm font-mono"
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
            Open-source physical flow intelligence — choose how deep you go.
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-px bg-border">
          {/* Free column */}
          <div className="bg-[#0a0a12] p-6">
            <div className="text-[10px] tracking-widest text-neutral-500 mb-1">FREE</div>
            <div className="text-2xl text-neutral-200 mb-1">€0</div>
            <div className="text-[10px] text-neutral-600 mb-5">forever — no card required</div>
            <ul className="space-y-2.5">
              {FREE_FEATURES.map((f) => (
                <li key={f} className="text-[11px] text-neutral-400 leading-relaxed flex gap-2">
                  <span className="text-cyan-glow/60 mt-0.5 shrink-0">·</span>
                  <span>{f}</span>
                </li>
              ))}
            </ul>
          </div>

          {/* Pro column */}
          <div className="bg-[#0a0a12] p-6 relative">
            <div className="absolute top-3 right-3 text-[9px] tracking-[2px] text-cyan-glow bg-cyan-glow/10 px-2 py-0.5 border border-cyan-glow/30 rounded-sm">
              RECOMMENDED
            </div>
            <div className="text-[10px] tracking-widest text-cyan-glow mb-1">PRO</div>
            <div className="text-2xl text-neutral-100 mb-1">
              {PRO_PRICE}
              <span className="text-sm text-neutral-500">{PRO_PERIOD}</span>
            </div>
            <div className="text-[10px] text-neutral-600 mb-5">or {PRO_YEAR_NOTE}</div>
            <ul className="space-y-2.5">
              {PRO_FEATURES.map((f) => (
                <li key={f} className="text-[11px] text-neutral-300 leading-relaxed flex gap-2">
                  <span className="text-cyan-glow mt-0.5 shrink-0">+</span>
                  <span>{f}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>

        <div className="px-6 py-5 border-t border-border flex flex-col items-stretch gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="text-[10px] text-neutral-500 leading-relaxed sm:max-w-md">
            Payments via Lemon Squeezy (EU-VAT handled, cancel anytime in customer portal).
            OBSYD is open source — your data stays auditable.
          </div>
          {ctaDisabled ? (
            <button
              type="button"
              disabled
              className="px-5 py-2.5 text-[11px] tracking-wider border border-border text-neutral-600 cursor-not-allowed shrink-0"
            >
              {ctaLabel}
            </button>
          ) : (
            <a
              href={ctaHref}
              target="_blank"
              rel="noopener noreferrer"
              className="px-5 py-2.5 text-[11px] tracking-wider bg-cyan-glow text-[#0a0a12] hover:bg-cyan-glow/90 transition-colors text-center shrink-0 font-semibold"
            >
              {ctaLabel} →
            </a>
          )}
        </div>
      </div>
    </div>
  )
}
