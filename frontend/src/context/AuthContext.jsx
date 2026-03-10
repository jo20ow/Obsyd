import { createContext, useContext, useState, useEffect, useCallback } from 'react'

const AuthContext = createContext()

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(() => {
    fetch('/api/auth/me', { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data?.authenticated) {
          setUser(data)
        } else {
          setUser(null)
        }
      })
      .catch(() => setUser(null))
      .finally(() => setLoading(false))
  }, [])

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

  const isPro = user?.tier === 'pro'

  return (
    <AuthContext.Provider value={{ user, isPro, loading, refresh, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  return useContext(AuthContext)
}
