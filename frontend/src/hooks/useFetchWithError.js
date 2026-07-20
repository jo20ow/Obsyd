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
// 503 a different visitor's real request). The entry is removed the instant
// its fetch settles, so this dedupes only genuinely CONCURRENT callers — a
// later poll or refetch always starts a fresh request. Skipped entirely when
// the caller passes custom `headers`, so a request under different
// credentials never risks sharing another caller's response.
//
// The physical fetch is wired to whichever caller happens to be first for a
// given url (the "leader"); a follower just awaits that same promise. If the
// leader's own component unmounts first, its AbortController cancels the
// shared fetch and every follower sees an AbortError too — `run()` below
// already treats AbortError as a silent no-op (same as the un-deduped path),
// and a still-mounted follower gets fresh data on its own next natural
// refetch. This only matters when two DIFFERENT components request the
// exact same url at the exact same moment; the common single-caller path is
// untouched — still tied to its own controller, still cancels on unmount
// exactly as before this map existed.
const inFlightRequests = new Map() // url -> Promise<rawJson>

function fetchRawJson(url, { headers, signal }) {
  return fetch(url, { credentials: 'include', headers, signal }).then((res) => {
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.json()
  })
}

function dedupedFetchRawJson(url, { headers, signal }) {
  if (headers) return fetchRawJson(url, { headers, signal }) // custom headers: never share
  const existing = inFlightRequests.get(url)
  if (existing) return existing
  const p = fetchRawJson(url, { headers, signal })
  inFlightRequests.set(url, p)
  p.finally(() => {
    if (inFlightRequests.get(url) === p) inFlightRequests.delete(url)
  })
  return p
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
