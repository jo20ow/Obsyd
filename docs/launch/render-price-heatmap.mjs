#!/usr/bin/env node
// Renders the r/dataisbeautiful launch image: one day of day-ahead prices,
// hour by hour, across all 37 bidding zones — zones sorted by daily mean, so
// the continent reads as a gradient and the spread story is instant.
//
// Usage:  node render-price-heatmap.mjs [YYYY-MM-DD]
// Default: YESTERDAY (UTC) — today's 22:00/23:00 UTC hours belong to tomorrow's
// CET trading day and are missing until the next SDAC auction, which would
// leave a gray tail on most rows. Yesterday is always complete.
// Output: price-heatmap-<date>.html (+ .png if playwright-core is resolvable;
//         otherwise open the HTML in a browser and screenshot it at 2x).
//
// Color doctrine (see the dataviz method): magnitude = ONE warm ramp, light →
// dark, linear from 0 to the day's max; negative prices are a different THING,
// not just "less", so they get the opposing blue pole, called out in the
// legend. Identity is never color-alone: the mean column carries the numbers.

import { writeFileSync } from 'fs'

const API = 'https://obsyd.dev'
const date = process.argv[2] || new Date(Date.now() - 86400e3).toISOString().slice(0, 10)
const next = new Date(Date.parse(date) + 86400e3).toISOString().slice(0, 10)

const meta = await (await fetch(`${API}/api/v1/meta`)).json()
const zones = {}
for (const z of meta.zones) {
  const r = await (await fetch(
    `${API}/api/v1/series?series=price.dayahead&zone=${z.key}&start=${date}&end=${next}&resolution=hourly`,
  )).json()
  const hours = {}
  for (const row of r.data || []) hours[+row.datetime_utc.slice(11, 13)] = row.value
  if (Object.keys(hours).length) zones[z.key] = { label: z.label, hours }
  await new Promise((res) => setTimeout(res, 150))
}

const rows = Object.entries(zones)
  .map(([key, z]) => {
    const vals = Object.values(z.hours)
    return { key, ...z, mean: vals.reduce((a, b) => a + b, 0) / vals.length }
  })
  .sort((a, b) => b.mean - a.mean)

const all = rows.flatMap((r) => Object.values(r.hours))
const max = Math.max(...all)
const negHours = all.filter((v) => v < 0).length
const spread = Math.round(rows[0].mean - rows[rows.length - 1].mean)

// Warm ramp, light → dark, lightness monotone by construction.
const RAMP = ['#fff5eb', '#fed9b6', '#fdae6b', '#f0731d', '#c74a06', '#8c2d04', '#5c1a03']
const NEG = '#2a78d6'
function color(v) {
  if (v == null) return '#eceae4'
  if (v < 0) return NEG
  const t = Math.max(0, Math.min(1, v / max)) * (RAMP.length - 1)
  const i = Math.min(RAMP.length - 2, Math.floor(t)), f = t - i
  const hex = (c) => [1, 3, 5].map((p) => parseInt(c.slice(p, p + 2), 16))
  const [a, b] = [hex(RAMP[i]), hex(RAMP[i + 1])]
  return `rgb(${a.map((x, j) => Math.round(x + (b[j] - x) * f)).join(',')})`
}

const CELL = 30, ROW = 17
const dateLabel = new Date(date + 'T12:00:00Z')
  .toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' })

const body = rows.map((r) => `
  <tr>
    <td class="zl">${r.label}</td>
    ${Array.from({ length: 24 }, (_, h) => {
      const v = r.hours[h]
      return `<td class="c" style="background:${color(v)}" title="${r.label} ${String(h).padStart(2, '0')}:00 UTC · ${v == null ? 'no data' : '€' + v.toFixed(0)}"></td>`
    }).join('')}
    <td class="mean">€${r.mean.toFixed(0)}</td>
  </tr>`).join('')

const legendStops = Array.from({ length: 60 }, (_, i) => color((i / 59) * max)).join(',')

const html = `<!doctype html><html><head><meta charset="utf-8"><style>
  * { margin: 0; padding: 0; box-sizing: border-box }
  body { background: #fcfcfb; color: #0b0b0b; font-family: system-ui, -apple-system, 'Segoe UI', sans-serif; padding: 40px 44px 28px }
  h1 { font-size: 25px; font-weight: 700; letter-spacing: -0.3px }
  .sub { color: #52514e; font-size: 14px; margin: 7px 0 22px }
  table { border-collapse: separate; border-spacing: 1.5px 1.5px }
  .zl { font-size: 10.5px; color: #52514e; text-align: right; padding-right: 8px; white-space: nowrap; font-variant-numeric: tabular-nums }
  .c { width: ${CELL}px; height: ${ROW}px; border-radius: 2px }
  .mean { font-size: 10.5px; color: #0b0b0b; padding-left: 9px; font-variant-numeric: tabular-nums; font-weight: 600 }
  .hh { font-size: 10px; color: #898781; text-align: left; padding-top: 5px; font-weight: 400 }
  .legend { display: flex; align-items: center; gap: 14px; margin-top: 18px; font-size: 11.5px; color: #52514e }
  .bar { width: 190px; height: 10px; border-radius: 3px; background: linear-gradient(to right, ${legendStops}) }
  .negsw { display: inline-block; width: 12px; height: 12px; border-radius: 2px; background: ${NEG}; vertical-align: -2px; margin-right: 5px }
  .foot { margin-top: 16px; font-size: 11px; color: #898781 }
</style></head><body>
  <h1>Same day, same market — and a €${spread}/MWh gap across Europe</h1>
  <div class="sub">Day-ahead electricity prices on ${dateLabel}, hour by hour (UTC), all ${rows.length} European bidding zones, sorted by daily mean.</div>
  <table>
    <tr><td></td>${Array.from({ length: 24 }, (_, h) => `<td class="hh">${h % 3 === 0 ? String(h).padStart(2, '0') : ''}</td>`).join('')}<td class="hh">mean</td></tr>
    ${body}
  </table>
  <div class="legend">
    <span>€0</span><div class="bar"></div><span>€${Math.round(max)}/MWh</span>
    ${negHours ? `<span style="margin-left:14px"><span class="negsw"></span>negative price (${negHours} zone-hours this day)</span>` : ''}
  </div>
  <div class="foot">Data: ENTSO-E Transparency Platform · chart: obsyd.dev — free &amp; open source (AGPL-3.0)</div>
</body></html>`

const htmlPath = `price-heatmap-${date}.html`
writeFileSync(htmlPath, html)
console.log('wrote', htmlPath, `| zones ${rows.length} | max €${max} | spread €${spread} | neg hours ${negHours}`)

try {
  const { chromium } = await import('playwright-core')
  const exe = process.env.CHROMIUM_PATH
  const browser = await chromium.launch(exe ? { executablePath: exe } : {})
  const page = await browser.newPage({ viewport: { width: 1080, height: 860 }, deviceScaleFactor: 2 })
  await page.goto('file://' + process.cwd() + '/' + htmlPath)
  await page.screenshot({ path: `price-heatmap-${date}.png`, fullPage: true })
  await browser.close()
  console.log('wrote', `price-heatmap-${date}.png`)
} catch {
  console.log('playwright-core not resolvable — open the HTML in a browser and screenshot at 2x.')
}
