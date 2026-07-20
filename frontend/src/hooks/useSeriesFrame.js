import { useMemo } from 'react'
import useFetchWithError from './useFetchWithError'
import { rangeStart } from '../utils/ranges'

const API = '/api'

// Chart-Builder hard cap: also the number of fixed fetch slots below. Six keeps
// a full chart well inside the v1 API's shared 120/min-per-IP rate bucket even
// on a fast zone/series edit.
export const MAX_SERIES_ROWS = 6

function seriesUrl(row, start, resolution) {
  return `${API}/v1/series?series=${encodeURIComponent(row.series)}&zone=${encodeURIComponent(row.zone)}&start=${start}&resolution=${resolution}`
}

/**
 * Multi-series/multi-zone outer-join over GET /api/v1/series — the
 * Chart-Builder's data layer. Generalizes SeriesExplorer's original 2-line
 * (primary + one compare) join into up to MAX_SERIES_ROWS independent
 * (series, zone) rows.
 *
 * `rows` is `[{series, zone, ...anything}]` (up to MAX_SERIES_ROWS; extra
 * fields such as `label`/`color` are opaque to this hook and echoed back
 * verbatim on the matching `perRow` entry, so a caller can carry UI-only
 * metadata through the join without this hook knowing about it).
 *
 * React hooks cannot be called in a loop, so — exactly like
 * ZoneCompareChart's MAX_COMPARE slots — there are always exactly
 * MAX_SERIES_ROWS useFetchWithError calls. A slot beyond the real row count
 * re-points at row 0's URL, which is already in the SWR cache: it costs no
 * extra request, just a cache hit.
 *
 * Returns `{ frame, loading, error, perRow }`:
 *   - `frame`: time-sorted outer join, one entry per distinct timestamp seen
 *     across ANY active row: `{ t, v0, v1, ..., v<n-1> }`, null where a row
 *     has no point at that `t`.
 *   - `perRow`: one entry per active row (input order), each the input row
 *     plus `{ key: 'v<i>', unit, count, available, reason, downloadUrl,
 *     loading, error, partialHours }` — partialHours is set only in daily
 *     resolution, when the last point on record averages < 24 hours (a day
 *     still filling in).
 *   - `loading`/`error`: whole-frame convenience flags (error mirrors row 0,
 *     the row that gates the chart rendering anything at all).
 */
export default function useSeriesFrame(rows, range, resolution = 'daily') {
  const start = rangeStart(range)
  const activeRows = useMemo(
    () => (rows || []).filter((r) => r?.series && r?.zone).slice(0, MAX_SERIES_ROWS),
    [rows]
  )
  const primary = activeRows[0] || null
  const slot = (i) => activeRows[i] || primary

  // A completely empty `rows` list (no primary yet) still needs a URL for
  // every hook call — fall back to a harmless constant so the hook count and
  // order never change.
  const FALLBACK_URL = `${API}/v1/series?series=price.dayahead&zone=DE_LU&start=${start}&resolution=${resolution}`
  const urlOf = (i) => {
    const r = slot(i)
    return r ? seriesUrl(r, start, resolution) : FALLBACK_URL
  }

  const r0 = useFetchWithError(urlOf(0), { deps: [start, resolution] })
  const r1 = useFetchWithError(urlOf(1), { deps: [start, resolution] })
  const r2 = useFetchWithError(urlOf(2), { deps: [start, resolution] })
  const r3 = useFetchWithError(urlOf(3), { deps: [start, resolution] })
  const r4 = useFetchWithError(urlOf(4), { deps: [start, resolution] })
  const r5 = useFetchWithError(urlOf(5), { deps: [start, resolution] })
  const responses = [r0, r1, r2, r3, r4, r5]

  const tkey = resolution === 'daily' ? 'date' : 'datetime_utc'
  const rowSig = activeRows.map((r) => `${r.series}:${r.zone}`).join(',')

  const { frame, perRow } = useMemo(() => {
    const n = activeRows.length
    const byT = new Map()
    const perRowOut = []
    for (let i = 0; i < n; i++) {
      const row = activeRows[i]
      const resp = responses[i].data
      const points = resp?.data || []
      const key = `v${i}`
      for (const p of points) {
        const t = p[tkey]
        if (!byT.has(t)) byT.set(t, { t })
        byT.get(t)[key] = p.value == null ? null : p.value
      }
      const last = resolution === 'daily' ? points.at(-1) : null
      perRowOut.push({
        ...row,
        key,
        unit: resp?.unit ?? null,
        count: resp?.count ?? points.length,
        available: resp ? resp.available !== false : null,
        reason: resp?.reason ?? null,
        downloadUrl: `${seriesUrl(row, start, resolution)}&format=csv`,
        loading: responses[i].loading,
        error: responses[i].error,
        partialHours: last?.hours != null && last.hours < 24 ? last.hours : null,
      })
    }
    const frameOut = [...byT.values()].sort((a, b) => (a.t < b.t ? -1 : a.t > b.t ? 1 : 0))
    return { frame: frameOut, perRow: perRowOut }
    // responses[i].data are the real per-row payloads; rowSig covers row identity/order,
    // start/resolution/tkey cover the query window — together the full input surface.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [r0.data, r1.data, r2.data, r3.data, r4.data, r5.data, rowSig, tkey, resolution, start])

  const loading = activeRows.length > 0 && responses.slice(0, activeRows.length).some((r) => r.loading)
  const error = activeRows.length > 0 && responses[0].error && !responses[0].data ? responses[0].error : null

  return { frame, loading, error, perRow }
}
