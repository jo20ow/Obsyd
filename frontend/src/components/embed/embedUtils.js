// Shared helpers for the /embed/<zone>/<metric> iframe widgets (Task P10).
//
// The embed pages intentionally do NOT reuse the desk's Tailwind color tokens
// (bg-surface / border-border / text-cyan-glow / text-neutral-*): those tokens are
// CSS custom properties (and a few hard utility overrides) that ThemeContext flips
// via an `html.light` class — and light is the SITE'S actual default theme (see
// index.html's pre-paint script). An embed dropped into a stranger's page must look
// the same dark, compact widget regardless of what the top-level obsyd.dev document's
// theme happens to be, so every embed component below uses plain hex values instead.

export const VALID_METRICS = ['price', 'genmix', 'load']

export const METRIC_TITLES = {
  price: 'Day-ahead price',
  genmix: 'Generation mix',
  load: 'Load',
}

// The zone registry's full label (e.g. "IT-Nord") isn't known client-side without an
// extra fetch; day-ahead/hourly doesn't return one at all. Mirrors the same shortcut
// PowerDayAheadPanel/LiveNowPanel already use elsewhere on the desk.
export function zoneLabel(zone) {
  return zone === 'DE_LU' ? 'DE-LU' : zone
}

// Days between an ISO date string (UTC midnight) and now — negative for a future
// delivery date (normal for day-ahead prices, which publish ~1 day ahead).
export function daysSince(dateStr) {
  if (!dateStr) return null
  const d = new Date(dateStr + 'T00:00:00Z')
  if (Number.isNaN(d.getTime())) return null
  return Math.floor((Date.now() - d.getTime()) / 86_400_000)
}

// Dark palette shared by every embed component — deliberately plain hex, see above.
export const EMBED_COLORS = {
  bg: '#0b0d12',
  panelBorder: '#20232b',
  text: '#c9ccd6',
  muted: '#7d8394',
  faint: '#5b6070',
  accent: '#2dd4bf',
  negative: '#f87171',
  warn: '#f59e0b',
  grid: '#1e1e2e',
}

export const MSG_STYLE = {
  flex: 1,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  textAlign: 'center',
  padding: '8px 12px',
  fontSize: 11,
  color: EMBED_COLORS.muted,
}
