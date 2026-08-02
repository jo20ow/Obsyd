import { useEffect, useState } from 'react'
import useFetchWithError from '../../hooks/useFetchWithError'

const API = '/api'

// The four data feeds behind the map. The API GETs go through useFetchWithError:
// its module-level SWR cache + in-flight dedupe are keyed by URL, so
// /power/overview is physically SHARED with PowerOverviewMatrix/NarrativeHero
// (concurrent mounts collapse into one GET). Like those consumers we pass NO
// `transform` — the URL-keyed cache must hold the same (raw) payload shape for
// every caller of a URL, so unwrapping happens here, after the hook. No pollMs
// anywhere: the map is a one-request-at-mount view.
export default function useMapData() {
  // Static asset, not an API — a plain one-shot fetch keeps it out of the
  // SWR/dedupe machinery (nothing else requests it, nothing to share).
  const [geo, setGeo] = useState(null)
  useEffect(() => {
    fetch('/geo/eu-zones.geojson').then((r) => r.json()).then(setGeo).catch((e) => console.error('PowerMap geo:', e))
  }, [])

  const { data: overview } = useFetchWithError(`${API}/power/overview`)
  const rows = overview ? overview.zones || [] : null

  // Hourly day-ahead price matrix for the scrubber; only trusted when the
  // endpoint says it is available.
  const { data: snapRaw } = useFetchWithError(`${API}/v1/snapshot?series=price.dayahead&hours=168`)
  const snap = snapRaw?.available ? snapRaw : null

  // Deliberately duplicates BordersPanel's GET (server caches the computation;
  // the dedupe above makes a concurrent mount share one physical request);
  // one request at mount, no polling — the arcs say "latest", not "live".
  const { data: bordersRaw } = useFetchWithError(`${API}/power/borders?days=30`)
  const borders = bordersRaw?.available ? bordersRaw.borders || [] : null

  return { geo, rows, snap, borders }
}
