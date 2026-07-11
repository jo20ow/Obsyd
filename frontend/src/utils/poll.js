// Central poll cadences for the auto-refreshing views. The ENTSO-E ingest runs
// every 30 minutes, so anything faster than these just re-reads the same data.
export const POLL_FAST_MS = 5 * 60 * 1000 // situation views: hero, overview, narrative
export const POLL_SLOW_MS = 10 * 60 * 1000 // detail panels: grid, mix, flows, outages
