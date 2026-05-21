import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Tiny replacement for the `.catch(() => {})` pattern that hides
 * errors in many panels. Always returns the same shape and never
 * leaves a panel in a permanent loading-spinner if the fetch fails.
 *
 *   const { data, loading, error, refetch } = useFetchWithError(url, opts)
 *
 * Aborts on unmount + on re-fetch.
 *
 * `opts.transform(json)` lets the panel adapt the payload shape in
 * one place (e.g. unwrap `{ data: ... }`).
 *
 * `opts.deps` extends the dependency list so callers can refetch
 * when their own state changes.
 */
export default function useFetchWithError(url, opts = {}) {
  const { transform, deps = [], headers, signalRef } = opts
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const reqRef = useRef(0)

  const run = useCallback(
    async (controller) => {
      const myReq = ++reqRef.current
      setLoading(true)
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
        setData(transform ? transform(json) : json)
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
