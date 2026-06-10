import { useCallback, useEffect, useRef, useState } from 'react'

// Module-level stale-while-revalidate cache: panels that unmount on a tab
// switch re-render instantly from the last payload while revalidating in
// the background — instead of flashing a skeleton and re-fetching cold.
const swrCache = new Map() // url -> last successful (transformed) payload

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
 */
export default function useFetchWithError(url, opts = {}) {
  const { transform, deps = [], headers, signalRef } = opts
  const [data, setData] = useState(() => (swrCache.has(url) ? swrCache.get(url) : null))
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(() => !swrCache.has(url))
  const reqRef = useRef(0)

  const run = useCallback(
    async (controller) => {
      const myReq = ++reqRef.current
      // Serve stale data immediately while revalidating; only show the
      // loading state when we have nothing cached for this URL yet.
      if (swrCache.has(url)) {
        setData(swrCache.get(url))
        setLoading(false)
      } else {
        setLoading(true)
      }
      setError(null)
      try {
        const res = await fetch(url, {
          credentials: 'include',
          headers,
          signal: controller.signal,
        })
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`)
        }
        const json = await res.json()
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

  return { data, loading, error, refetch }
}
