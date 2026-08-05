import useFetchWithError from '../hooks/useFetchWithError'

const API = '/api'

function gw(mw) {
  return `${mw >= 0 ? '+' : ''}${(mw / 1000).toFixed(1)} GW`
}

function Line({ label, err }) {
  if (!err?.available) return null
  const lean = err.bias_mw > 200 ? 'leaned low' : err.bias_mw < -200 ? 'leaned high' : 'was on target'
  return (
    <div className="font-mono text-[10px] text-neutral-500">
      <span className="text-neutral-400">{label}</span>: forecast {lean} — actual ran{' '}
      <span className={err.bias_mw >= 0 ? 'text-cyan-glow' : 'text-orange-400'}>{gw(err.bias_mw)}</span>{' '}
      vs forecast on average (typical miss {(err.mae_mw / 1000).toFixed(1)} GW, {err.n_hours}h)
    </div>
  )
}

/**
 * Quantifies the published TSO day-ahead forecast against what happened —
 * A "forecast vs actual" strip in Posture-B language: we describe ENTSO-E's
 * own forecast, we do not make one. bias = mean(actual − forecast); positive =
 * demand surprise / renewables over-delivered.
 */
export default function ForecastErrorStrip({ zone = 'DE_LU' }) {
  const { data: load, error: loadErr } = useFetchWithError(`${API}/power/forecast-error?zone=${zone}&series=load`, { deps: [zone] })
  const { data: wind, error: windErr } = useFetchWithError(`${API}/power/forecast-error?zone=${zone}&series=wind`, { deps: [zone] })
  const { data: solar, error: solarErr } = useFetchWithError(`${API}/power/forecast-error?zone=${zone}&series=solar`, { deps: [zone] })

  // Ephemeral strip: absence of forecast-error data is a normal state and stays
  // silent — but a FETCH error must not masquerade as "nothing to report".
  if (loadErr && windErr && solarErr)
    return (
      <div className="px-4 py-2 border-t border-border/30 font-mono text-[9px] text-red-400">
        forecast vs actual // fetch error
      </div>
    )
  if (!load?.available && !wind?.available && !solar?.available) return null

  return (
    <div className="px-4 py-2 border-t border-border/30 space-y-0.5">
      <div className="flex items-center justify-between gap-2">
        <div className="font-mono text-[9px] text-neutral-600 tracking-wider uppercase">
          Forecast vs actual · last 7 days · published TSO forecast, not ours
        </div>
        {/* Cross-tab jump via the URL hash: App.jsx's hashchange listener is the
            existing deep-link mechanism that switches the active tab for any hash
            matching a TAB key — no prop threading through PowerLoadForecastPanel
            needed. The delayed scroll waits for the ANALYTICS panels to mount;
            if they haven't yet, scrollIntoView on a missing node is a no-op and
            the user still lands on the right tab. */}
        <a
          href="#analytics"
          onClick={() => {
            setTimeout(() => {
              document.getElementById('panel-forecast-scoreboard')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
            }, 250)
          }}
          className="shrink-0 font-mono text-[9px] tracking-wider border border-border rounded px-1.5 py-0.5 text-neutral-500 hover:text-cyan-glow hover:border-cyan-glow/40 transition-colors"
          title="Open the full forecast scoreboard on the ANALYTICS tab — MAE/RMSE/bias, skill vs naive baselines, all-zone ranking"
        >
          full scoreboard →
        </a>
      </div>
      <Line label="Load" err={load} />
      <Line label="Wind" err={wind} />
      <Line label="Solar" err={solar} />
    </div>
  )
}
