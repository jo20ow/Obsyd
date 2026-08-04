import { useMemo, useState } from 'react'
import Panel from './Panel'
import useFetchWithError from '../hooks/useFetchWithError'
import useZones from '../hooks/useZones'
import { POLL_SLOW_MS } from '../utils/poll'

const API = '/api'

// Display labels for the four graded series (engine order — the backend's
// FORECAST_PAIRS; each /summary response confirms it via series_keys).
const SERIES_LABEL = { load: 'Load', residual: 'Residual load', wind: 'Wind', solar: 'Solar' }
const SERIES_ORDER = ['load', 'residual', 'wind', 'solar']

// /summary ships all three windows in one response (client-side toggle);
// /ranking takes the window as a param — each is its own cached server compute.
const ZONE_WINDOWS = ['30d', '90d', '365d']
const RANK_WINDOWS = [30, 90, 365]

// Ranked rows shown per ranking table before "show all" — signposted zones are
// ALWAYS appended below regardless (listed, never hidden — the API's posture).
const INITIAL_RANK_ROWS = 10

// Fallback title for the bias columns, used only until /summary delivers the
// API's own bias_convention string (which rides on title= attrs, never popovers).
const BIAS_TITLE_FALLBACK =
  'bias = mean(forecast − actual) in MW: positive = the published forecast leaned high'

// Segmented toggle idiom shared with CapturePanel / GasBalancePanel.
function ToggleBtn({ id, label, view, setView }) {
  return (
    <button
      type="button"
      onClick={() => setView(id)}
      className={`font-mono text-[9px] tracking-wider px-2 py-0.5 rounded border transition-colors ${
        view === id ? 'text-cyan-glow border-cyan-glow/50 bg-cyan-glow/5' : 'text-neutral-600 border-border hover:text-neutral-400'
      }`}
    >
      {label}
    </button>
  )
}

// MW to 0–1 decimals (repo number convention): whole MW normally, one decimal
// only when the value is small enough that rounding would erase it.
const fmtMW = (v) => (v == null ? '—' : Math.abs(v) < 10 ? v.toFixed(1) : Math.round(v).toLocaleString('en-US'))
const fmtBias = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${fmtMW(v)}`)
const fmtPct1 = (v) => (v == null ? '—' : `${v.toFixed(1)}%`)

// Skill vs a naive baseline, as the compact phrase — signed %, colored by sign.
function SkillCell({ skill }) {
  if (skill == null) {
    return <span className="text-neutral-700" title="No day in this window carries this baseline">—</span>
  }
  const pct = Math.abs(skill * 100).toFixed(1)
  if (skill > 0) return <span className="text-emerald-400">beats naive by {pct}%</span>
  if (skill < 0) return <span className="text-orange-400">trails naive by {pct}%</span>
  return <span className="text-neutral-500">matches naive</span>
}

// Structured ⓘ legend — BordersLegend's <dl> pattern. The API's `note` /
// `bias_convention` strings are never rendered raw in this popover (repo rule);
// the long-form prose lives in HOW TO READ on the EUROPE tab.
function ScoreboardLegend() {
  const rows = [
    ['MAE', 'mean absolute error (MW) — the typical hourly miss, direction ignored.'],
    ['RMSE', 'root-mean-square error (MW) — punishes big misses harder; RMSE well above MAE = spiky errors.'],
    ['Bias', 'mean(forecast − actual) in MW — positive = the published forecast leaned high.'],
    ['MAPE', 'the miss as % of the actual — load only by design (wind/solar hit honest zeros, residual crosses zero).'],
    ['nMAE', 'MAE as % of installed capacity (A68; wind = onshore + offshore) — comparable across fleet sizes.'],
    ['beats naive', 'skill = 1 − MAE/MAE_naive vs persistence (same hour yesterday) and seasonal (same hour last week) — both built from published actuals alone. Positive = the published forecast beat the no-model yardstick.'],
    ['n=', 'days (and hours) both forecast and actual existed — every score states its sample.'],
  ]
  return (
    <div className="space-y-2">
      <dl className="space-y-1.5">
        {rows.map(([term, def]) => (
          <div key={term} className="grid grid-cols-[74px_1fr] gap-x-2">
            <dt className="text-cyan-glow/90">{term}</dt>
            <dd className="text-neutral-400 leading-snug">{def}</dd>
          </div>
        ))}
      </dl>
      <div className="pt-1 border-t border-border/40 text-neutral-500">
        Grades ENTSO-E&rsquo;s own published D-1 forecasts — OBSYD makes no forecasts.
        Full definitions: HOW TO READ on the EUROPE tab.
      </div>
    </div>
  )
}

// One zone's report card at one window — per series MAE/RMSE/bias (+MAPE for
// load) and skill vs both naive baselines. Tables and styled divs only.
function ZoneTable({ data, window, biasTitle }) {
  const byKey = Object.fromEntries((data.series ?? []).map((s) => [s.series, s]))
  const keys = data.series_keys?.length ? data.series_keys : SERIES_ORDER
  return (
    <div className="px-2 pt-2 overflow-x-auto">
      <table className="w-full font-mono text-[10px]">
        <thead>
          <tr className="text-[8px] text-neutral-600 uppercase tracking-wider">
            <th className="text-left px-2 py-1">Series</th>
            <th className="text-right px-2 py-1" title="Mean absolute error over the window (MW)">MAE MW</th>
            <th className="text-right px-2 py-1" title="Root-mean-square error over the window (MW)">RMSE MW</th>
            <th className="text-right px-2 py-1" title={biasTitle}>Bias MW</th>
            <th className="text-right px-2 py-1" title="Mean absolute percentage error — load only by design">MAPE</th>
            <th className="text-left px-2 py-1" title="Skill vs persistence: the actual at the same hour yesterday as the naive forecast">vs persistence</th>
            <th className="text-left px-2 py-1" title="Skill vs seasonal: the actual at the same hour last week as the naive forecast">vs seasonal</th>
            <th className="text-right px-2 py-1" title="Days covered in this window (scored hours on hover)">n</th>
          </tr>
        </thead>
        <tbody>
          {keys.map((k) => {
            const s = byKey[k]
            const w = s?.windows?.[window]
            if (!s || !w) {
              // Absent series / empty window: named and explained, never a
              // silently missing row.
              return (
                <tr key={k} className="border-t border-border/30 text-neutral-700">
                  <td className="px-2 py-1.5 text-neutral-600">{SERIES_LABEL[k] ?? k}</td>
                  <td colSpan={7} className="px-2 py-1.5 text-[9px]">
                    {!s ? 'not scored — this zone does not carry the forecast/actual pair' : 'no scored days in this window'}
                  </td>
                </tr>
              )
            }
            return (
              <tr key={k} className="border-t border-border/30">
                <td className="px-2 py-1.5 text-neutral-300 whitespace-nowrap">{SERIES_LABEL[k] ?? k}</td>
                <td className="px-2 py-1.5 text-right num text-neutral-300">{fmtMW(w.mae)}</td>
                <td className="px-2 py-1.5 text-right num text-neutral-400">{fmtMW(w.rmse)}</td>
                <td className="px-2 py-1.5 text-right num text-neutral-300" title={biasTitle}>{fmtBias(w.bias)}</td>
                <td className="px-2 py-1.5 text-right num text-neutral-400">
                  {k === 'load' ? fmtPct1(w.mape) : <span className="text-neutral-700" title="MAPE is load-only by design">—</span>}
                </td>
                <td className="px-2 py-1.5 whitespace-nowrap"><SkillCell skill={w.skill_persistence} /></td>
                <td className="px-2 py-1.5 whitespace-nowrap"><SkillCell skill={w.skill_seasonal} /></td>
                <td className="px-2 py-1.5 text-right num text-neutral-500 whitespace-nowrap" title={`${w.n_hours} scored hours`}>
                  n={w.days_covered}d
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// One series' cross-zone ranking table. The API delivers ranked rows first
// (best = lowest error) and signposted rows last (rank: null) — signposted
// zones stay visible below the cap, greyed, with the reason in the row (body
// text; the ⓘ popover stays API-string-free).
function RankingTable({ name, block, zoneLabel, currentZone }) {
  const [showAll, setShowAll] = useState(false)
  const rows = block?.ranking ?? []
  const ranked = rows.filter((r) => r.rank != null)
  const signposted = rows.filter((r) => r.rank == null)
  const visible = showAll ? ranked : ranked.slice(0, INITIAL_RANK_ROWS)
  const metric = block?.metric
  const metricHead = metric === 'mape' ? 'MAPE' : metric === 'nmae_pct' ? 'nMAE' : 'MAE MW'
  const showMaeCol = metric !== 'mae' // absolute MW context beside the % metric
  const cols = 4 + (showMaeCol ? 1 : 0)

  const metricCell = (r) => {
    if (metric === 'mape') return fmtPct1(r.mape)
    if (metric === 'nmae_pct') {
      return (
        <span title={r.capacity_mw != null ? `${fmtMW(r.mae)} MW MAE ÷ ${fmtMW(r.capacity_mw)} MW installed (A68)` : undefined}>
          {fmtPct1(r.nmae_pct)}
        </span>
      )
    }
    return fmtMW(r.mae)
  }

  return (
    <div className="border border-border/40 rounded">
      <div className="px-2 py-1.5 border-b border-border/30">
        <span className="font-mono text-[9px] text-neutral-400 uppercase tracking-wider">
          {SERIES_LABEL[name] ?? name} · ranked by {metricHead}
        </span>
        {block?.caveat && (
          <div className="font-mono text-[9px] text-neutral-600 leading-snug pt-0.5">{block.caveat}</div>
        )}
      </div>
      <div className="px-1 py-1 overflow-x-auto">
        <table className="w-full font-mono text-[10px]">
          <thead>
            <tr className="text-[8px] text-neutral-600 uppercase tracking-wider">
              <th className="text-right px-2 py-1" title="Rank 1 = the published forecast the actuals stayed closest to">#</th>
              <th className="text-left px-2 py-1">Zone</th>
              <th className="text-right px-2 py-1">{metricHead}</th>
              {showMaeCol && <th className="text-right px-2 py-1" title="Absolute window MAE (MW), for scale">MAE MW</th>}
              <th className="text-right px-2 py-1" title="Days covered in this window (scored hours on hover)">n</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((r) => {
              const isCurrent = r.zone === currentZone
              return (
                <tr key={r.zone} className={`border-t border-border/30 ${isCurrent ? 'bg-white/[0.03]' : ''}`}>
                  <td className="px-2 py-1 text-right num text-neutral-500">{r.rank}</td>
                  <td className={`px-2 py-1 whitespace-nowrap ${isCurrent ? 'text-cyan-glow' : 'text-neutral-300'}`}>{zoneLabel(r.zone)}</td>
                  <td className="px-2 py-1 text-right num text-neutral-300">{metricCell(r)}</td>
                  {showMaeCol && <td className="px-2 py-1 text-right num text-neutral-500">{fmtMW(r.mae)}</td>}
                  <td className="px-2 py-1 text-right num text-neutral-600 whitespace-nowrap" title={`${r.n_hours} scored hours`}>{r.days_covered}d</td>
                </tr>
              )
            })}
            {signposted.map((r) => (
              <tr key={r.zone} className="border-t border-border/30 text-neutral-600">
                <td className="px-2 py-1 text-right">—</td>
                <td className="px-2 py-1 whitespace-nowrap">{zoneLabel(r.zone)}</td>
                <td colSpan={cols - 2} className="px-2 py-1 text-[9px] leading-snug">
                  MAE {fmtMW(r.mae)} MW · {r.signposted}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {ranked.length > INITIAL_RANK_ROWS && (
        <div className="px-2 pb-1.5">
          <button
            onClick={() => setShowAll((v) => !v)}
            className="font-mono text-[9px] tracking-wider border border-border rounded px-1.5 py-0.5 text-neutral-500 hover:text-cyan-glow hover:border-cyan-glow/40 transition-colors"
          >
            {showAll ? 'show fewer' : `show all ${ranked.length} ranked`}
          </button>
        </div>
      )}
    </div>
  )
}

/**
 * The forecast scoreboard over GET /api/v1/scoreboard/{summary,ranking}:
 * grades ENTSO-E's own published D-1 forecasts (load / residual / wind /
 * solar) against its published actuals — OBSYD makes no forecasts. ZONE view
 * is one zone's report card at 30/90/365 days; RANKING view orders all zones
 * per series by the comparable metric (load: MAPE; wind/solar: nMAE % of A68
 * installed capacity; residual: absolute MAE, caveated). Zones a
 * normalization can't cover are listed signposted, never hidden.
 */
export default function ForecastScoreboardPanel({ zone = 'DE_LU' }) {
  const [view, setView] = useState('zone')
  const [zoneWindow, setZoneWindow] = useState('30d')
  const [rankWindow, setRankWindow] = useState(90)

  const { zones: zoneList } = useZones()
  const zoneLabels = useMemo(() => new Map(zoneList.map((z) => [z.key, z.label || z.key])), [zoneList])
  const zoneLabel = (key) => zoneLabels.get(key) || key

  // Summary is cheap (rate-limited only) and drives the default view → always
  // fetched. Ranking is heavy-guarded server-side → fetched only while shown
  // (the hook's null-url idle); all request state lives in the URL (#115).
  const { data: sum, loading: sumLoading, error: sumError } = useFetchWithError(
    `${API}/v1/scoreboard/summary?zone=${zone}`, { deps: [zone], pollMs: POLL_SLOW_MS },
  )
  const { data: rank, loading: rankLoading, error: rankError } = useFetchWithError(
    view === 'ranking' ? `${API}/v1/scoreboard/ranking?window=${rankWindow}` : null,
    { deps: [view, rankWindow], pollMs: POLL_SLOW_MS },
  )

  const active = view === 'ranking' ? rank : sum
  // The API's own sign statement rides on title= attrs only — never a popover.
  const biasTitle = sum?.bias_convention || BIAS_TITLE_FALLBACK

  return (
    <Panel
      id="forecast-scoreboard"
      title={view === 'ranking' ? 'FORECAST SCOREBOARD · ALL ZONES' : `FORECAST SCOREBOARD · ${zoneLabel(zone)}`}
      info={<ScoreboardLegend />}
      infoWide
      freshness={active}
      collapsible
      headerRight={
        <div className="flex items-center gap-1">
          <ToggleBtn id="zone" label="ZONE" view={view} setView={setView} />
          <ToggleBtn id="ranking" label="RANKING" view={view} setView={setView} />
        </div>
      }
    >
      {view === 'zone' && (
        <>
          <div className="flex items-center justify-between px-4 pt-2.5">
            <div className="flex items-center gap-1">
              {ZONE_WINDOWS.map((w) => (
                <ToggleBtn key={w} id={w} label={w.toUpperCase()} view={zoneWindow} setView={setZoneWindow} />
              ))}
            </div>
            <span className="font-mono text-[9px] text-neutral-600 hidden sm:inline">trailing UTC days · D-1 forecast vs actual</span>
          </div>
          {sumLoading && !sum && (
            <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">Loading scoreboard…</div>
          )}
          {!sumLoading && sumError && !sum && (
            <div className="px-4 py-6 text-center font-mono text-[10px] text-red-400">Fetch error — retrying on next refresh.</div>
          )}
          {sum && !sum.available && (
            <div className="px-4 py-4 font-mono text-[10px] text-neutral-500">
              No forecast scores exist yet for this zone — the nightly scorer writes one row per zone,
              series and UTC day once both the published forecast and the published actual exist.
            </div>
          )}
          {sum?.available && <ZoneTable data={sum} window={zoneWindow} biasTitle={biasTitle} />}
        </>
      )}

      {view === 'ranking' && (
        <>
          <div className="flex items-center justify-between px-4 pt-2.5">
            <div className="flex items-center gap-1">
              {RANK_WINDOWS.map((w) => (
                <ToggleBtn key={w} id={w} label={`${w}D`} view={rankWindow} setView={setRankWindow} />
              ))}
            </div>
            <span className="font-mono text-[9px] text-neutral-600 hidden sm:inline">lower error = better · rank 1 = closest to actuals</span>
          </div>
          {rankLoading && !rank && (
            <div className="px-4 py-6 text-center font-mono text-[10px] text-neutral-600 animate-pulse">Loading ranking…</div>
          )}
          {!rankLoading && rankError && !rank && (
            <div className="px-4 py-6 text-center font-mono text-[10px] text-red-400">Fetch error — retrying on next refresh.</div>
          )}
          {rank && !rank.available && (
            <div className="px-4 py-4 font-mono text-[10px] text-neutral-500">
              No forecast scores exist yet in this window — the nightly scorer has not graded any zone here.
            </div>
          )}
          {rank?.available && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 px-3 pt-3 pb-1">
              {SERIES_ORDER.filter((k) => rank.series?.[k]).map((k) => (
                <RankingTable key={k} name={k} block={rank.series[k]} zoneLabel={zoneLabel} currentZone={zone} />
              ))}
            </div>
          )}
        </>
      )}

      <div className="px-4 py-2 mt-2 border-t border-border font-mono text-[9px] text-neutral-700">
        Grades ENTSO-E&rsquo;s own published D-1 forecasts against its published actuals — OBSYD makes no
        forecasts. Naive yardsticks built from published actuals alone: persistence = same hour yesterday,
        seasonal = same hour last week. Public at GET /api/v1/scoreboard/summary + /ranking.
      </div>
    </Panel>
  )
}
