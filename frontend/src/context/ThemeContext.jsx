import { createContext, useContext, useEffect, useState } from 'react'

// ThemeContext — light (default, gridstatus-like) vs dark. Applies `class="light"`
// on <html>; the light palette + neutral/recharts remaps live in index.css. Persists
// to localStorage. A pre-paint inline script in index.html sets the class before React
// mounts to avoid a flash. Follows the ModeContext pattern.

const ThemeContext = createContext()

function readInitial() {
  try {
    const t = localStorage.getItem('obsyd-theme')
    if (t === 'light' || t === 'dark') return t
  } catch { /* storage blocked */ }
  return 'light'
}

export function ThemeProvider({ children }) {
  const [theme, setThemeState] = useState(readInitial)

  useEffect(() => {
    const root = document.documentElement
    root.classList.toggle('light', theme === 'light')
  }, [theme])

  const setTheme = (t) => {
    setThemeState(t)
    try { localStorage.setItem('obsyd-theme', t) } catch { /* ignore */ }
  }
  const toggle = () => setTheme(theme === 'light' ? 'dark' : 'light')

  return (
    <ThemeContext.Provider value={{ theme, setTheme, toggle }}>
      {children}
    </ThemeContext.Provider>
  )
}

export function useTheme() {
  return useContext(ThemeContext)
}
