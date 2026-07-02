import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import { fmtDate } from '../utils/chart'

const API = '/api'

// "now"-derived bounds at module load (reading the clock inside render is impure);
// cosmetic only (this-week highlight), refreshes on reload.
const TODAY_ISO = new Date().toISOString().slice(0, 10)
const WEEK_END = new Date(Date.now() + 7 * 864e5).toISOString().slice(0, 10)

function weekday(d) {
  return new Date(d + 'T00:00:00Z').toLocaleDateString('en-US', { weekday: 'short' })
}

export default function EconPanel() {
  const { data, loading, error } = useFetchWithError(`${API}/econ/calendar?days=21`)

  if (error) {
    return (
      <div className="border border-red-500/20 bg-surface rounded px-4 py-3">
        <div className="font-mono text-[10px] text-red-400">ECON CALENDAR // FETCH ERROR</div>
      </div>
    )
  }

  const rows = data?.data ?? []
  const todayIso = TODAY_ISO
  const weekEnd = WEEK_END

  return (
    <Panel
      id="econ"
      title="ECONOMIC CALENDAR · US"
      info="Upcoming major US macro releases (next 3 weeks) from the FRED release schedule — jobs, CPI, PPI, PCE, GDP, retail sales and more. Schedule only: consensus/forecast estimates are licensed (not free), so this shows WHEN data drops, not the survey number. Not investment advice."
      collapsible
      headerRight={data?.available && <span className="font-mono text-[9px] text-neutral-600">FRED schedule</span>}
    >
      {loading && !data && (
        <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">Loading calendar…</div>
      )}

      {!data?.available && !loading && (
        <div className="px-4 py-4 font-mono text-[11px] text-neutral-500">Release calendar unavailable — check back shortly.</div>
      )}

      {data?.available && (
        <>
          <div className="pb-1">
            {rows.map((r, i) => {
              const showDate = i === 0 || rows[i - 1].date !== r.date
              const thisWeek = r.date <= weekEnd
              const isToday = r.date === todayIso
              return (
                <div key={`${r.date}-${r.release}-${i}`}>
                  {showDate && (
                    <div className={`px-4 pt-3 pb-1 font-mono text-[10px] tracking-wider ${thisWeek ? 'text-cyan-glow' : 'text-neutral-500'}`}>
                      {weekday(r.date)} · {fmtDate(r.date)}{isToday ? ' · TODAY' : ''}{showDate && thisWeek && !isToday ? ' · this week' : ''}
                    </div>
                  )}
                  <div className="px-4 py-1 font-mono text-[12px] text-neutral-300 flex items-start gap-2">
                    <span className="text-neutral-700">•</span>
                    <span>{r.label}</span>
                  </div>
                </div>
              )
            })}
          </div>
          <div className="px-4 py-2 font-mono text-[9px] text-neutral-700">
            Source: FRED release schedule · schedule only (no consensus — licensed) · not investment advice
          </div>
        </>
      )}
    </Panel>
  )
}
