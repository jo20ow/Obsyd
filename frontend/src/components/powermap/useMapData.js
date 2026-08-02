import { useEffect, useState } from 'react'
import useFetchWithError from '../../hooks/useFetchWithError'

const API = '/api'

// Stable empty-array fallback: a fresh `|| []` literal would mint a new
// identity on every render and defeat the downstream memos (effRows, arcs).
const EMPTY = []

// Per-fill extra data feeds — fill key -> url, see `extra` below. Fetched
// ONLY while that fill is selected: the price and state fills must not pay for
// the marginal-tech estimate (a compute-on-read endpoint), and new
// fill-specific data goes through this seam, not into another component.
const EXTRA_BY_FILL = {
  tech: `${API}/power/marginal/overview`,
}

// The four shared data feeds behind the map, plus the active fill's own one
// (`extra`, EXTRA_BY_FILL). The API GETs go through useFetchWithError:
// its module-level SWR cache + in-flight dedupe are keyed by URL, so
// /power/overview is physically SHARED with PowerOverviewMatrix/NarrativeHero
// (concurrent mounts collapse into one GET). Like those consumers we pass NO
// `transform` — the URL-keyed cache must hold the same (raw) payload shape for
// every caller of a URL, so unwrapping happens here, after the hook. No pollMs
// anywhere: the map is a one-request-at-mount view.
//
// Takes the ACTIVE fill key so fill-specific feeds can hang off it (`extra`).
export default function useMapData(fill) {
  // Static asset, not an API — a plain one-shot fetch keeps it out of the
  // SWR/dedupe machinery (nothing else requests it, nothing to share).
  const [geo, setGeo] = useState(null)
  const [geoError, setGeoError] = useState(null)
  useEffect(() => {
    fetch('/geo/eu-zones.geojson')
      .then((r) => r.json())
      .then(setGeo)
      .catch((e) => {
        console.error('PowerMap geo:', e)
        setGeoError(e.message || String(e))
      })
  }, [])

  const { data: overview, error: overviewError } = useFetchWithError(`${API}/power/overview`)
  const rows = overview ? overview.zones || EMPTY : null

  // Hourly day-ahead price matrix for the scrubber; only trusted when the
  // endpoint says it is available.
  const { data: snapRaw, error: snapError } = useFetchWithError(`${API}/v1/snapshot?series=price.dayahead&hours=168`)
  const snap = snapRaw?.available ? snapRaw : null

  // Deliberately duplicates BordersPanel's GET (server caches the computation;
  // the dedupe above makes a concurrent mount share one physical request);
  // one request at mount, no polling — the arcs say "latest", not "live".
  const { data: bordersRaw, error: bordersError } = useFetchWithError(`${API}/power/borders?days=30`)
  const borders = bordersRaw?.available ? bordersRaw.borders || EMPTY : null

  // Parity with the old raw-fetch version: overview/borders failures LOG (the
  // old .catch(console.error) behavior) but draw no UI — the map degrades to
  // context fills / no arcs. The snapshot error stays silent here (the old
  // code swallowed it); all four errors are RETURNED so a later PR can
  // surface them.
  useEffect(() => { if (overviewError) console.error('PowerMap overview:', overviewError) }, [overviewError])
  useEffect(() => { if (bordersError) console.error('PowerMap borders:', bordersError) }, [bordersError])

  // The active fill's own feed. Hooks cannot be called conditionally, and the
  // fetch hook keys its SWR cache, its in-flight dedupe AND its effect deps on
  // the url — an `enabled` flag would have duplicated that url state in a
  // second place. So the hook learned the cheaper contract instead: a NULL url
  // = idle, no request (inert for its ~90 other callers, all of which pass a
  // real url). One unconditional call here, url only while the owning fill is
  // selected; after ONE COMPLETED fetch, switching fills back repaints
  // instantly from the url-keyed SWR cache while it revalidates behind the
  // scenes (switching away MID-FLIGHT aborts before the cache write, so that
  // return trip still loads cold). Passed on RAW (no `available` gate like snap/
  // borders): the fill needs the coverage metadata to count its own gaps.
  const { data: extra, error: extraError } = useFetchWithError(EXTRA_BY_FILL[fill] ?? null)
  useEffect(() => { if (extraError) console.error(`PowerMap ${fill} feed:`, extraError) }, [extraError, fill])

  return {
    geo, rows, snap, borders, extra,
    errors: { geo: geoError, overview: overviewError, snapshot: snapError, borders: bordersError, extra: extraError },
  }
}
