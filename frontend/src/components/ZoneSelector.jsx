/**
 * ZoneSelector — zone picker for the ENERGY tab, data-driven from GET /api/v1/zones.
 *
 * Props:
 *   zone     {string}   — currently selected zone key (e.g. "DE_LU")
 *   onChange {function} — called with the new zone key on change
 *
 * A native <select> so it scales to all enabled zones (27+) without overflowing,
 * unlike the old 3-pill row. SparkSpreadHistory stays DE-LU only (signposted in-panel).
 */
import useZones from '../hooks/useZones'

export default function ZoneSelector({ zone, onChange }) {
  const { zones } = useZones()
  return (
    <select
      value={zone}
      onChange={(e) => onChange(e.target.value)}
      className="font-mono text-[10px] tracking-wider bg-[#0a0a12] border border-cyan-500/40 text-cyan-300 rounded px-2 py-1 focus:outline-none focus:border-cyan-glow"
      aria-label="Bidding zone"
    >
      {zones.map(({ key, label }) => (
        <option key={key} value={key}>{label}</option>
      ))}
    </select>
  )
}
