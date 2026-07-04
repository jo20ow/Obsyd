import useFetchWithError from './useFetchWithError'

// Single source for the enabled bidding zones, from GET /api/v1/zones.
// Feeds every zone selector/navigation so they scale as zones are enabled
// (no more hardcoded 3-zone lists). Falls back to the original three until loaded.
const FALLBACK = [
  { key: 'DE_LU', label: 'DE-LU' },
  { key: 'FR', label: 'FR' },
  { key: 'NL', label: 'NL' },
]

export default function useZones() {
  const { data } = useFetchWithError('/api/v1/zones')
  const all = data?.zones || []
  const enabled = all.filter((z) => z.enabled)
  return {
    zones: enabled.length ? enabled : FALLBACK, // enabled zones, [{key,label,has_flows}]
    all,
    defaultZone: data?.default || 'DE_LU',
  }
}
