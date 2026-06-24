/**
 * ZoneSelector — small pill-button group for the ENERGY tab.
 *
 * Props:
 *   zone     {string}   — currently selected zone key (e.g. "DE_LU")
 *   onChange {function} — called with the new zone key on click
 *
 * SparkSpreadHistory has no zone column and is intentionally DE-LU only;
 * only the three multi-zone panels (DayAhead, Grid, GenerationMix) use this.
 */

const ZONES = [
  { key: 'DE_LU', label: 'DE-LU' },
  { key: 'FR',    label: 'FR'    },
  { key: 'NL',    label: 'NL'    },
]

export default function ZoneSelector({ zone, onChange }) {
  return (
    <div className="flex items-center gap-1">
      {ZONES.map(({ key, label }) => (
        <button
          key={key}
          onClick={() => onChange(key)}
          className={[
            'font-mono text-[9px] tracking-wider px-2 py-0.5 rounded border transition-colors',
            zone === key
              ? 'border-cyan-500/60 text-cyan-300 bg-cyan-500/10'
              : 'border-border/40 text-neutral-500 hover:text-neutral-300 hover:border-border/60 bg-transparent',
          ].join(' ')}
        >
          {label}
        </button>
      ))}
    </div>
  )
}
