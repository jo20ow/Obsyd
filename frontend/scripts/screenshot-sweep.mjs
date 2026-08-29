#!/usr/bin/env node
// Visual-regression sweep: full-page screenshots of every reachable surface in
// both themes, for before/after eyeballing around design passes.
//
// Usage:
//   node scripts/screenshot-sweep.mjs [base-url] [--out dir]
//   (default base http://localhost:5199, default out screenshots/sweep;
//    pass https://obsyd.dev to sweep production)
// Needs playwright-core resolvable; set CHROMIUM_PATH to a Chrome/Chromium
// binary if no bundled browser is installed (same contract as
// verify-zone-coherence.mjs).

import { mkdirSync } from 'fs'
import { join } from 'path'
import { exit, argv, env } from 'process'

const args = argv.slice(2)
const outIdx = args.indexOf('--out')
const OUT = outIdx !== -1 ? args[outIdx + 1] : 'screenshots/sweep'
const BASE = args.find((a, i) => !a.startsWith('--') && i !== outIdx + 1) || 'http://localhost:5199'

// App routes wait for the desk shell (#desk-nav) + a settle for async panels;
// static routes only need the network to go quiet.
const ROUTES = [
  { path: '/', name: 'landing', app: false },
  { path: '/docs', name: 'docs', app: false },
  { path: '/impressum', name: 'impressum', app: false },
  { path: '/datenschutz', name: 'datenschutz', app: false },
  { path: '/app#europe', name: 'tab-europe', app: true },
  { path: '/app#energy', name: 'tab-power', app: true },
  { path: '/app#analytics', name: 'tab-analytics', app: true },
  { path: '/app#gas', name: 'tab-gas', app: true },
  { path: '/app#explore', name: 'tab-explore', app: true },
  { path: '/app#alerts', name: 'tab-alerts', app: true },
]

let chromium
try {
  ({ chromium } = await import('playwright-core'))
} catch {
  console.error('playwright-core not resolvable — npm i --no-save playwright-core')
  exit(2)
}

mkdirSync(OUT, { recursive: true })
const browser = await chromium.launch(
  env.CHROMIUM_PATH ? { executablePath: env.CHROMIUM_PATH } : {},
)

for (const theme of ['light', 'dark']) {
  const ctx = await browser.newContext({ viewport: { width: 1500, height: 1000 } })
  // Pin the theme and force every collapsible panel open so sweeps compare
  // full content, not whatever collapse state localStorage happens to hold.
  await ctx.addInitScript((t) => {
    localStorage.setItem('obsyd-theme', t)
    localStorage.setItem('obsyd-panel-how-to-read', '0')
  }, theme)
  const page = await ctx.newPage()
  for (const r of ROUTES) {
    const file = join(OUT, `${r.name}-${theme}.png`)
    try {
      await page.goto(`${BASE}${r.path}`, { waitUntil: 'domcontentloaded', timeout: 60000 })
      if (r.app) {
        await page.waitForSelector('#desk-nav', { timeout: 30000 })
        await page.waitForTimeout(4000)
      } else {
        await page.waitForLoadState('networkidle', { timeout: 30000 }).catch(() => {})
        await page.waitForTimeout(500)
      }
      await page.screenshot({ path: file, fullPage: true })
      console.log(`ok   ${file}`)
    } catch (e) {
      console.error(`FAIL ${file} — ${e.message.split('\n')[0]}`)
    }
  }
  await ctx.close()
}

await browser.close()
console.log(`\nSweep complete → ${OUT}`)
