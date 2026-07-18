#!/usr/bin/env node
// Zone-coherence sweep — the regression guard for the 2026-07-18 bug where
// switching zones kept every panel silently showing the PREVIOUS zone
// (useFetchWithError served the old url's payload with no loading state).
//
// For each hop in the sweep it clicks the zone in the desk nav and asserts:
//   1. the POWER SITUATION chip never shows the previous zone after the click
//      (a short 400 ms grace covers the same-frame race), and
//   2. the chip shows the NEW zone's label within 5 s.
// It also reports the observed switch latency per hop.
//
// Usage:
//   node scripts/verify-zone-coherence.mjs [base-url]
//   (default base http://localhost:5199 — start `npx vite` with an /api proxy,
//    or pass https://obsyd.dev to sweep production)
// Needs playwright-core resolvable (npx -y playwright-core is NOT enough — run
// from a dir with it installed, or set CHROMIUM_PATH to a Chrome/Chromium
// binary and install playwright-core once: npm i --no-save playwright-core).

import { exit } from 'process'

const BASE = process.argv[2] || 'http://localhost:5199'
// key = pill/dropdown value in the desk nav, label = what panels display.
const SWEEP = [
  { key: 'DE-LU', label: 'DE-LU' },
  { key: 'NL', label: 'NL' },
  { key: 'FR', label: 'FR' },
  { key: 'IT-Sicilia', label: 'IT-Sicilia' },
  { key: 'SE2', label: 'SE2' },
  { key: 'DE-LU', label: 'DE-LU' },
]

let chromium
try {
  ({ chromium } = await import('playwright-core'))
} catch {
  console.error('playwright-core not resolvable — npm i --no-save playwright-core')
  exit(2)
}

const browser = await chromium.launch(
  process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {},
)
const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } })

const situationChip = () =>
  page.evaluate(() => {
    const el = [...document.querySelectorAll('*')].find(
      (e) => e.childElementCount === 0 && e.textContent.includes('POWER SITUATION'),
    )
    return el?.closest('div[class*="border"]')?.innerText.split('\n')[1]?.trim() || null
  })

const pickZone = async (key) => {
  const pill = page.locator('#desk-nav button', { hasText: key })
  if (await pill.count()) return pill.first().click()
  return page.locator('#desk-nav select').selectOption({ label: key })
}

await page.goto(`${BASE}/app?zone=DE_LU#energy`, { timeout: 60000, waitUntil: 'domcontentloaded' })
await page.waitForSelector('#desk-nav', { timeout: 30000 })
await page.waitForTimeout(4000)

let failures = 0
let prev = 'DE-LU'
for (const { key, label } of SWEEP.slice(1)) {
  await pickZone(key)
  const t0 = Date.now()
  let settled = null
  let staleSeen = false
  while (Date.now() - t0 < 5000) {
    const chip = await situationChip()
    const dt = Date.now() - t0
    if (chip === prev && dt > 400) staleSeen = true
    if (chip === label) { settled = dt; break }
    await page.waitForTimeout(100)
  }
  const ok = settled != null && !staleSeen
  if (!ok) failures++
  console.log(
    `${prev} → ${label}: ${ok ? 'PASS' : 'FAIL'}` +
    (settled != null ? ` (sichtbar nach ${settled} ms)` : ' (nicht innerhalb 5 s sichtbar)') +
    (staleSeen ? ' — ALTE ZONE nach >400 ms noch angezeigt' : ''),
  )
  prev = label
  await page.waitForTimeout(1200)
}

await browser.close()
console.log(failures === 0 ? 'ZONE COHERENCE: ALL PASS' : `ZONE COHERENCE: ${failures} FAILURES`)
exit(failures === 0 ? 0 : 1)
