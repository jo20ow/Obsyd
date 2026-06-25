import { createContext, useContext, useState, useEffect, useCallback } from 'react'

const AuthContext = createContext()

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [checkoutUrl, setCheckoutUrl] = useState(null)
  const [trialEndsAt, setTrialEndsAt] = useState(null)
  const [trialEligible, setTrialEligible] = useState(false)
  const [loading, setLoading] = useState(true)
  const [pricingOpen, setPricingOpen] = useState(false)

  const refresh = useCallback(() => {
    fetch('/api/auth/me', { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data?.authenticated) {
          setUser(data)
        } else {
          setUser(null)
        }
        // checkout_url is returned for both authed-free and anon users
        setCheckoutUrl(data?.checkout_url || null)
        setTrialEndsAt(data?.trial_ends_at || null)
        setTrialEligible(Boolean(data?.trial_eligible))
      })
      .catch(() => {
        setUser(null)
        setCheckoutUrl(null)
        setTrialEndsAt(null)
        setTrialEligible(false)
      })
      .finally(() => setLoading(false))
  }, [])

  const startTrial = useCallback(async () => {
    const res = await fetch('/api/auth/start-trial', {
      method: 'POST',
      credentials: 'include',
    })
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}))
      return { ok: false, status: res.status, detail }
    }
    const data = await res.json()
    // Refresh so the rest of the UI immediately reflects pro tier.
    refresh()
    return { ok: true, ...data }
  }, [refresh])

  useEffect(() => {
    refresh()

    // Check for auth callback in URL
    const params = new URLSearchParams(window.location.search)
    const authResult = params.get('auth')
    if (authResult) {
      // Clean URL
      window.history.replaceState({}, '', window.location.pathname)
      if (authResult === 'success') {
        refresh()
      }
    }
  }, [refresh])

  const logout = useCallback(() => {
    fetch('/api/auth/logout', { method: 'POST', credentials: 'include' })
      .then(() => setUser(null))
      .catch(() => setUser(null))
  }, [])

  // TEMP paywall kill-switch: VITE_DISABLE_PROGATE=1 unlocks every gated panel
  // for all visitors. Reversible — drop the env var to restore the paywall.
  const isPro = import.meta.env.VITE_DISABLE_PROGATE === '1' || user?.tier === 'pro'
  const openPricing = useCallback(() => setPricingOpen(true), [])
  const closePricing = useCallback(() => setPricingOpen(false), [])

  return (
    <AuthContext.Provider
      value={{
        user,
        isPro,
        loading,
        refresh,
        logout,
        checkoutUrl,
        trialEndsAt,
        trialEligible,
        startTrial,
        pricingOpen,
        openPricing,
        closePricing,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  return useContext(AuthContext)
}
