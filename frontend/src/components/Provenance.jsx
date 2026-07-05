// Small provenance/trust stamp — Obsyd's "auditable, from the official record"
// identity (a differentiator vs closed dashboards). Descriptive, not a forecast.
export default function Provenance({ source, updated, className = '' }) {
  return (
    <div className={`font-mono text-[9px] text-neutral-600 ${className}`}>
      Source: {source}{updated ? ` · updated ${updated}` : ''} · from the official record, descriptive
    </div>
  )
}
