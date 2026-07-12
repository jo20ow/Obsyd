// Shared chart helpers for the EU gas panels.

// Delivery-date labels are UTC dates. Without an explicit timeZone the browser
// renders UTC midnight in local time, which shifts every label a day backwards
// for viewers west of UTC.
export function fmtDate(d) {
  return new Date(d + 'T00:00:00Z').toLocaleDateString('en-US', {
    month: 'short', day: 'numeric', timeZone: 'UTC',
  })
}

export const CHART_TOOLTIP_STYLE = { background: '#0f1115', border: '1px solid #262a33', fontFamily: 'inherit', fontSize: 12, borderRadius: 8 }

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
