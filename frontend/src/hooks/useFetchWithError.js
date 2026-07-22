import { useCallback, useEffect, useRef, useState } from 'react'

// Module-level stale-while-revalidate cache: panels that unmount on a tab
// switch re-render instantly from the last payload while revalidating in
// the background — instead of flashing a skeleton and re-fetching cold.
const swrCache = new Map() // url -> last successful (transformed) payload

// Module-level in-flight-request map: concurrent callers for the SAME url
// share ONE physical fetch's raw JSON. Fixed-slot components (ZoneCompareChart's
// compare zones, useSeriesFrame's 6 series rows) point several sibling hook
// instances at the identical url in the same render — each used to fire its
// own GET, multiplying load against the backend's shared heavy_query_guard
// semaphore (at the limit, one visitor's fixed-slot waste could transiently
// 503 a different visitor's real request). Skipped entirely when the caller
// passes custom `headers`, so a request under different credentials never
// risks sharing another caller's response.
//
// Each entry owns its OWN AbortController (never any single caller's) plus a
// subscriber count:
//   - a caller's own unmount/abort only decrements the count and rejects
//     THAT caller's returned promise with AbortError; the PHYSICAL fetch is
//     aborted only once the LAST subscriber leaves — a solo caller's unmount
//     still cancels its fetch exactly as before this map existed, and a
//     leader's early unmount no longer strands a still-mounted follower.
//   - reusing an entry is guarded by `entry.controller.signal.aborted`, so a
//     StrictMode double-invoke (dev: setup→cleanup→setup runs synchronously,
//     before the settled entry's removal microtask has a chance to run) or a
//     genuinely quick unmount/remount never joins an already-doomed entry —
//     it starts a fresh fetch instead of awaiting a promise that can only
//     ever reject.
//   - the entry is removed as soon as its fetch settles, on BOTH the resolve
//     and the reject branch (never an unhandled rejection), so a later poll
//     or refetch always starts against a clean slate.
const inFlightRequests = new Map() // url -> { promise, controller, subscribers }

function fetchRawJson(url, { headers, signal }) {
  return fetch(url, { credentials: 'include', headers, signal }).then((res) => {
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.json()
  })
}

function dedupedFetchRawJson(url, { headers, signal }) {
  if (headers) return fetchRawJson(url, { headers, signal }) // custom headers: never share
  let entry = inFlightRequests.get(url)
  if (!entry || entry.controller.signal.aborted) {   // never share a dead fetch (StrictMode / quick remount)
    const controller = new AbortController()
    const promise = fetchRawJson(url, { headers, signal: controller.signal })
    entry = { promise, controller, subscribers: 0 }
    inFlightRequests.set(url, entry)
    const remove = () => { if (inFlightRequests.get(url) === entry) inFlightRequests.delete(url) }
    promise.then(remove, remove)                     // both-ways handler: no unhandled rejection
  }
  entry.subscribers++
  signal.addEventListener('abort', () => {           // abort the PHYSICAL fetch only when the LAST subscriber leaves
    if (--entry.subscribers === 0) entry.controller.abort()
  }, { once: true })
  return new Promise((resolve, reject) => {          // each caller still gets ITS OWN AbortError on ITS OWN unmount
    if (signal.aborted) return reject(new DOMException('aborted', 'AbortError'))
    signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true })
    entry.promise.then(resolve, reject)
  })
}

/**
 * Tiny replacement for the `.catch(() => {})` pattern that hides
 * errors in many panels. Always returns the same shape and never
 * leaves a panel in a permanent loading-spinner if the fetch fails.
 *
 *   const { data, loading, error, refetch } = useFetchWithError(url, opts)
 *
 * Aborts on unmount + on re-fetch. Serves cached data instantly on
 * remount (stale-while-revalidate).
 *
 * `opts.transform(json)` lets the panel adapt the payload shape in
 * one place (e.g. unwrap `{ data: ... }`).
 *
 * `opts.deps` extends the dependency list so callers can refetch
 * when their own state changes.
 *
 * `opts.pollMs` re-fetches on an interval so today-views keep filling in
 * without a reload (the ingest runs every 30 min; panels poll slower).
 * Polling pauses while the tab is hidden and resumes — with an immediate
 * refresh — when it becomes visible again.
 */
export default function useFetchWithError(url, opts = {}) {
  const { transform, deps = [], headers, signalRef, pollMs } = opts
  const [data, setData] = useState(() => (swrCache.has(url) ? swrCache.get(url) : null))
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(() => !swrCache.has(url))
  const reqRef = useRef(0)

  const run = useCallback(
    async (controller) => {
      const myReq = ++reqRef.current
      // Serve stale data immediately while revalidating — but ONLY data that
      // belongs to THIS url. When the url changes (zone/range switch) and the
      // new url has no cache yet, `data` must drop to null: keeping the old
      // url's payload made every panel silently show zone A labeled as
      // current while zone B was loading (or forever, if the fetch failed).
      if (swrCache.has(url)) {
        setData(swrCache.get(url))
        setLoading(false)
      } else {
        setData(null)
        setLoading(true)
      }
      setError(null)
      try {
        const json = await dedupedFetchRawJson(url, { headers, signal: controller.signal })
        if (myReq !== reqRef.current) return // a newer call superseded us
        const payload = transform ? transform(json) : json
        swrCache.set(url, payload)
        setData(payload)
      } catch (e) {
        if (e.name === 'AbortError') return
        if (myReq !== reqRef.current) return
        setError(e.message || String(e))
      } finally {
        if (myReq === reqRef.current) setLoading(false)
      }
    },
    // headers and transform are typically stable / inline — depending on
    // them retriggers more often than the caller expects. Callers should
    // pass primitives via `deps`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [url, ...deps]
  )

  const refetch = useCallback(() => {
    const controller = new AbortController()
    if (signalRef) signalRef.current = controller
    run(controller)
    return () => controller.abort()
  }, [run, signalRef])

  useEffect(() => {
    const controller = new AbortController()
    if (signalRef) signalRef.current = controller
    run(controller)
    return () => controller.abort()
  }, [run, signalRef])

  useEffect(() => {
    if (!pollMs) return
    let controller = null
    const tick = () => {
      if (document.hidden) return // don't burn requests for a backgrounded tab
      controller = new AbortController()
      run(controller)
    }
    const interval = setInterval(tick, pollMs)
    // Coming back to the tab: refresh immediately instead of waiting a full cycle.
    const onVisible = () => { if (!document.hidden) tick() }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      clearInterval(interval)
      document.removeEventListener('visibilitychange', onVisible)
      controller?.abort()
    }
  }, [run, pollMs])

  return { data, loading, error, refetch }
}
