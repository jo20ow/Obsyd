// Grid-state trio for the state fill.
export default function StateLegend({ pal }) {
  return (
    <span className="flex items-center gap-3">
      <span style={{ color: pal.stateLegend.CALM }}>■ CALM</span>
      <span style={{ color: pal.stateLegend.ELEVATED }}>■ ELEVATED</span>
      <span style={{ color: pal.stateLegend.STRESSED }}>■ STRESSED</span>
    </span>
  )
}
