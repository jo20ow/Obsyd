// Renders the landing's product screenshots (public/desk-{dark,light}.png)
// from the LIVE desk — re-run after visual changes. Needs CHROMIUM_PATH
// (see screenshot-sweep.mjs) and the frontend dir as cwd.
import { chromium } from 'playwright-core'

const CHROME = process.env.CHROMIUM_PATH
if (!CHROME) throw new Error('Set CHROMIUM_PATH to a Chrome/Chromium binary')

const browser = await chromium.launch({ executablePath: CHROME })
for (const theme of ['dark', 'light']) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 860 }, deviceScaleFactor: 2 })
  await ctx.addInitScript((t) => { try { localStorage.setItem('obsyd-theme', t) } catch {} }, theme)
  const page = await ctx.newPage()
  await page.goto('https://obsyd.dev/app', { waitUntil: 'networkidle', timeout: 60000 }).catch(() => {})
  await page.waitForTimeout(6000)
  await page.screenshot({ path: `public/desk-${theme}.png` })
  await ctx.close()
  console.log('shot', theme)
}
await browser.close()
