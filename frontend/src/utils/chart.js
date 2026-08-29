// Shared chart helpers for the EU gas panels.

import { useTheme } from '../context/ThemeContext'

// Real monospace for axis ticks (mirrors index.css --font-code; SVG text can't
// read CSS custom properties as attribute values, so the stack is repeated here).
const CODE_FONT = "ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, Consolas, monospace"

// Theme-aware chart neutrals. Recharts axes/grids take inline SVG props, which
// CSS (and therefore the html.light overrides) cannot reach — so the values live
// here as plain objects and components re-render on theme change via the hook.
// Per the dataviz method: grid hairline + recessive (one step off the surface),
// tick text in muted ink tokens (never a series color), real mono tabular digits.
// `accent` is the single-series stroke matching --color-cyan-glow per theme
// (inline SVG attrs can't resolve var(--…) either).
export const CHART_THEME = {
  dark: {
    grid: { stroke: '#262a33', strokeOpacity: 0.6, vertical: false },
    tick: { fontSize: 10, fill: '#8b8fa3', fontFamily: CODE_FONT },
    axisLine: { stroke: '#262a33' },
    accent: '#7aa5e8',
    ink: '#e5e7eb',
  },
  light: {
    grid: { stroke: '#e9e8e4', vertical: false },
    tick: { fontSize: 10, fill: '#6b7280', fontFamily: CODE_FONT },
    axisLine: { stroke: '#d9d8d3' },
    accent: '#1d4ed8',
    ink: '#374151',
  },
}

// Usage: const ct = useChartTheme()
//   <CartesianGrid {...ct.grid} /> · tick={ct.tick} · axisLine={ct.axisLine}
export function useChartTheme() {
  const { theme } = useTheme()
  return CHART_THEME[theme === 'light' ? 'light' : 'dark']
}

// Delivery-date labels are UTC dates. Without an explicit timeZone the browser
// renders UTC midnight in local time, which shifts every label a day backwards
// for viewers west of UTC.
export function fmtDate(d) {
  return new Date(d + 'T00:00:00Z').toLocaleDateString('en-US', {
    month: 'short', day: 'numeric', timeZone: 'UTC',
  })
}

export const CHART_TOOLTIP_STYLE = { background: '#0f1115', border: '1px solid #262a33', fontFamily: 'inherit', fontSize: 12, borderRadius: 8 }

// Spread THESE into a recharts <Tooltip>. Spreading CHART_TOOLTIP_STYLE itself
// passes `background` etc. as unknown Tooltip props — recharts ignores them and
// renders its white default box on the dark desk. contentStyle is the real
// prop; itemStyle/labelStyle keep the text in ink instead of the series color
// (a bright series hue is unreadable as text — the mark carries identity).
export const CHART_TOOLTIP_PROPS = {
  contentStyle: CHART_TOOLTIP_STYLE,
  itemStyle: { color: '#c9ccd6' },
  labelStyle: { color: '#8b8fa3' },
}

// Hour-of-day label for the hourly day-ahead curve (0 → "00h", 13 → "13h").
export function fmtHour(h) {
  return `${String(h).padStart(2, '0')}h`
}

// UTC timestamp label for hourly/15-min series ("Jul 11, 14:00 UTC") — every
// time on this desk is UTC, so the label must not drift with the viewer's zone.
export function fmtTs(iso) {
  const d = new Date(iso)
  if (isNaN(d)) return String(iso)
  return d.toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    hour12: false, timeZone: 'UTC',
  }) + ' UTC'
}

// Sequential color ramp (dark → cyan → amber) for choropleth fills. t in [0,1].
const _RAMP = [
  [0.0, [18, 22, 38]],
  [0.35, [20, 92, 120]],
  [0.7, [34, 185, 205]],
  [1.0, [240, 222, 96]],
]

export function rampColor(t) {
  t = Math.max(0, Math.min(1, Number.isFinite(t) ? t : 0))
  for (let i = 1; i < _RAMP.length; i++) {
    const [t1, c1] = _RAMP[i]
    if (t <= t1) {
      const [t0, c0] = _RAMP[i - 1]
      const f = t1 === t0 ? 0 : (t - t0) / (t1 - t0)
      return [0, 1, 2].map((k) => Math.round(c0[k] + (c1[k] - c0[k]) * f))
    }
  }
  return _RAMP[_RAMP.length - 1][1]
}

// Fill for countries with no value for the selected metric (visibly "no data", not zero).
export const NO_DATA_COLOR = [40, 40, 54]
