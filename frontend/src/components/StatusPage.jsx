import DocShell, { LinkButton, SectionLabel } from './doc/DocShell'
import useFetchWithError from '../hooks/useFetchWithError'

const API = '/api'

function Dot({ ok }) {
  return (
    <span className={`inline-block w-2 h-2 rounded-full mr-2 ${ok ? 'bg-emerald-500' : 'bg-amber-500'}`} />
  )
}

/**
 * /status — the public health page. Until now /api/v1/status was JSON-only and
 * its UI lived inside the app's EXPLORE tab; a data consumer deciding whether
 * to build on the API had no page to look at. This renders the same endpoint:
 * per-source freshness with the desk's honesty conventions (a stale source is
 * SHOWN stale — IE_SEM's source-side gap is a documented fact, not an outage).
 */
export default function StatusPage() {
  const { data, loading, error } = useFetchWithError(`${API}/v1/status`)
  const sources = data?.sources || []
  const stale = sources.filter((s) => !s.fresh)
  const zones = sources.filter((s) => s.key.includes(':'))
  const core = sources.filter((s) => !s.key.includes(':'))

  return (
    <DocShell maxWidth="max-w-3xl">
      <div className="flex items-baseline justify-between gap-4 mb-6">
        <SectionLabel>DATA STATUS</SectionLabel>
        <a href="/api/v1/status" className="font-mono text-[10px] text-neutral-600 hover:text-cyan-glow">
          JSON: /api/v1/status ↗
        </a>
      </div>

      {loading && !data && (
        <div className="font-mono text-[11px] text-neutral-500 animate-pulse">Loading…</div>
      )}
      {error && !data && (
        <div className="font-mono text-[11px] text-red-400">Status endpoint unreachable — that IS the status.</div>
      )}

      {data && (
        <>
          <div className="bg-surface border border-border rounded p-4 mb-6 font-mono text-[12px] text-neutral-300">
            <Dot ok={stale.length === 0} />
            {data.fresh_count}/{data.total} monitored feeds fresh
            {stale.length > 0 && (
              <span className="text-neutral-500"> — stale feeds are listed below with their windows; a
                known source-side gap stays visibly stale rather than being hidden.</span>
            )}
          </div>

          {stale.length > 0 && (
            <div className="mb-6">
              <div className="font-mono text-[10px] text-amber-400 mb-2 smallcaps">STALE</div>
              <div className="space-y-1">
                {stale.map((s) => (
                  <div key={s.key} className="font-mono text-[11px] text-neutral-400">
                    <Dot ok={false} />{s.key}
                    <span className="text-neutral-600"> · last seen {s.last_seen ?? 'never'} · window {s.max_age_days}d</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-8">
            {[['CORE FEEDS', core], ['PER-ZONE FEEDS', zones]].map(([title, list]) => (
              <div key={title} className="mb-6">
                <div className="font-mono text-[10px] text-neutral-500 mb-2 smallcaps">{title}</div>
                <div className="space-y-1">
                  {list.map((s) => (
                    <div key={s.key} className="font-mono text-[11px] text-neutral-400 flex items-baseline">
                      <Dot ok={s.fresh} />
                      <span className="truncate">{s.key}</span>
                      <span className="ml-auto pl-3 text-neutral-600 shrink-0">{s.max_age_days}d window</span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>

          <p className="text-[12px] text-neutral-500 leading-relaxed max-w-2xl">
            Freshness windows are per-source honesty thresholds, not SLAs: a monthly series is
            legitimately weeks old. Completeness and the source&apos;s own restatements are on the
            desk&apos;s EXPLORE tab (Data Quality &amp; Revisions Ledger), with the raw numbers at{' '}
            <a href="/api/v1/quality/summary" className="text-cyan-glow hover:underline">/api/v1/quality/*</a>.
          </p>
          <div className="mt-6">
            <LinkButton href="/docs">API docs →</LinkButton>
          </div>
        </>
      )}
    </DocShell>
  )
}
