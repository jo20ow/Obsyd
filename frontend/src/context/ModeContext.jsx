import { createContext, useContext, useState } from 'react'

const ModeContext = createContext()

export function ModeProvider({ children }) {
  const [mode, setMode] = useState(() => {
    try {
      return localStorage.getItem('obsyd-mode') || 'all'
    } catch {
      return 'all'
    }
  })

  const changeMode = (m) => {
    setMode(m)
    try { localStorage.setItem('obsyd-mode', m) } catch {}
  }

  return (
    <ModeContext.Provider value={{ mode, setMode: changeMode }}>
      {children}
    </ModeContext.Provider>
  )
}

export function useMode() {
  return useContext(ModeContext)
}
