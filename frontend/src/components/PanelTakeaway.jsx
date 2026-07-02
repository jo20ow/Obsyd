// A persistent, readable "This means: …" line — the plain-language take-away that
// tells a newcomer what a number IMPLIES. Deliberately legible (not an 8px caption
// or a hidden info dot), because teaching is the point.

const TONE = {
  info: 'text-neutral-300 border-neutral-700',
  warn: 'text-yellow-300/90 border-yellow-500/40',
  alert: 'text-red-300/90 border-red-500/40',
}

export default function PanelTakeaway({ children, tone = 'info', className = '' }) {
  if (!children) return null
  return (
    <div className={`font-mono text-[11px] leading-snug border-l-2 pl-2 ${TONE[tone] || TONE.info} ${className}`}>
      <span className="text-neutral-500">This means: </span>
      {children}
    </div>
  )
}
