// The honest "so what?" for a physical anomaly: how the relevant price moved after
// comparable past events (median +7d/+30d, n). Descriptive co-movement — not a
// forecast. Shared by the situation bar and the anomaly radar so they never drift.

const fmtPct = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(1)}%`)

export default function AnomalyContextLine({ context, className = '' }) {
  if (!context) return null
  return (
    <div className={`font-mono text-[10px] text-neutral-400 leading-snug ${className}`}>
      <span className="text-neutral-500">↳ context:</span> last {context.n} {context.event_label} → {context.price_label}{' '}
      <span className="text-neutral-200">{fmtPct(context.median_30d_pct)}</span> @30d ({fmtPct(context.median_7d_pct)} @7d)
      <span className="text-neutral-600"> · not a forecast</span>
    </div>
  )
}
