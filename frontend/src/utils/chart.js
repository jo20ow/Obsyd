// Shared chart helpers for the EU gas panels.

export function fmtDate(d) {
  return new Date(d + 'T00:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

export const CHART_TOOLTIP_STYLE = { background: '#0a0a12', border: '1px solid #2a2a3a', fontFamily: 'monospace', fontSize: 10 }
