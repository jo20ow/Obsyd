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
 * gridstatus' "forecast vs actual" in Posture-B language: we describe ENTSO-E's
 * own forecast, we do not make one. bias = mean(actual − forecast); positive =
 * demand surprise / renewables over-delivered.
 */
export default function ForecastErrorStrip({ zone = 'DE_LU' }) {
  const { data: load } = useFetchWithError(`${API}/power/forecast-error?zone=${zone}&series=load`, { deps: [zone] })
  const { data: wind } = useFetchWithError(`${API}/power/forecast-error?zone=${zone}&series=wind`, { deps: [zone] })
  const { data: solar } = useFetchWithError(`${API}/power/forecast-error?zone=${zone}&series=solar`, { deps: [zone] })

  if (!load?.available && !wind?.available && !solar?.available) return null

  return (
    <div className="px-4 py-2 border-t border-border/30 space-y-0.5">
      <div className="font-mono text-[9px] text-neutral-600 tracking-wider uppercase">
        Forecast vs actual · last 7 days · published TSO forecast, not ours
      </div>
      <Line label="Load" err={load} />
      <Line label="Wind" err={wind} />
      <Line label="Solar" err={solar} />
    </div>
  )
}
