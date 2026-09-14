#!/usr/bin/env node
// Records the trader "aha" against live obsyd.dev for the r/energytrading launch:
// switch the desk from one zone to a far pricier one, watch the situation header
// jump, then the driver card explains WHY — same click, a completely different
// market, with the reason attached. Injects a visible cursor so clicks read.
//
// Usage:
//   CHROMIUM_PATH=/path/to/chrome node record-demo.mjs [FROM_ZONE] [TO_ZONE]
//   (defaults DE_LU → IT_NORD; pick the day's widest spread from the desk)
// Output (in cwd): a .webm — convert to a Reddit-native MP4 + a GIF with:
//   WEBM=$(ls page@*.webm)
//   ffmpeg -ss 3.9 -i "$WEBM" -c:v libx264 -crf 20 -pix_fmt yuv420p \
//     -movflags +faststart -vf scale=1280:720 obsyd-demo.mp4
//   ffmpeg -ss 3.9 -i "$WEBM" -vf "fps=15,scale=900:-1,palettegen" pal.png
//   ffmpeg -ss 3.9 -i "$WEBM" -i pal.png -lavfi "fps=15,scale=900:-1,paletteuse" obsyd-demo.gif
// (the 3.9s trim drops the page-load head; re-check the first frame after rendering)
//
// Needs playwright-core resolvable and a Chrome/Chromium at $CHROMIUM_PATH.

import { chromium } from 'playwright-core'

const FROM = process.argv[2] || 'DE_LU'
const TO = process.argv[3] || 'IT_NORD'
const W = 1280, H = 720

const browser = await chromium.launch(
  process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {},
)
const context = await browser.newContext({
  viewport: { width: W, height: H },
  deviceScaleFactor: 2,
  recordVideo: { dir: process.cwd(), size: { width: W, height: H } },
})
const page = await context.newPage()

// A branded cursor dot that follows the real pointer + pulses on click.
await page.addInitScript(() => {
  window.addEventListener('DOMContentLoaded', () => {
    const d = document.createElement('div')
    d.id = '__cur'
    d.style.cssText = [
      'position:fixed', 'left:0', 'top:0', 'width:18px', 'height:18px',
      'margin:-9px 0 0 -9px', 'border-radius:50%', 'z-index:2147483647',
      'pointer-events:none', 'background:rgba(34,211,238,0.35)',
      'border:2px solid #22d3ee', 'box-shadow:0 0 12px 2px rgba(34,211,238,0.6)',
      'transition:transform 0.08s ease', 'transform:translate(640px,360px)',
    ].join(';')
    document.body.appendChild(d)
    let x = 640, y = 360
    document.addEventListener('mousemove', (e) => {
      x = e.clientX; y = e.clientY
      d.style.transform = `translate(${x}px,${y}px)`
    }, true)
    document.addEventListener('mousedown', () => { d.style.transform = `translate(${x}px,${y}px) scale(1.6)` }, true)
    document.addEventListener('mouseup', () => { d.style.transform = `translate(${x}px,${y}px) scale(1)` }, true)
  })
})
await page.addInitScript(() => localStorage.setItem('obsyd-theme', 'dark'))

const wait = (ms) => page.waitForTimeout(ms)
async function glideTo(sel) {
  const box = await page.locator(sel).first().boundingBox()
  if (box) await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 30 })
}

await page.goto(`https://obsyd.dev/app?zone=${FROM}#energy`, { waitUntil: 'domcontentloaded', timeout: 60000 })
await page.waitForSelector('#desk-nav', { timeout: 30000 })
await page.mouse.move(640, 360)
await wait(3500)          // page-load head — trimmed off in ffmpeg (-ss 3.9)
await wait(2500)          // beat 1: FROM zone on screen

await glideTo('#desk-nav select')
await wait(500)
await page.locator('#desk-nav select').selectOption(TO)
await wait(300)
await page.waitForFunction(() => {
  const el = [...document.querySelectorAll('*')].find(e => e.childElementCount === 0 && e.textContent.includes('POWER SITUATION'))
  return el && !/DE-LU/.test(el.closest('div[class*="border"]')?.innerText || '')
}, { timeout: 8000 }).catch(() => {})
await wait(3200)          // beat 2: the price jump lands

await page.evaluate(() => {
  const el = [...document.querySelectorAll('*')].find(e => e.childElementCount === 0 && /DRIVERS/.test(e.textContent))
  el?.closest('div[class*="border"]')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
})
await wait(4000)          // beat 3: the driver card — the "why"

await context.close()
await browser.close()
console.log('wrote', await page.video().path())
