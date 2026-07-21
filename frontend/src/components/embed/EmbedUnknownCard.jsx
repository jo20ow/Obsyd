import { EMBED_COLORS, VALID_METRICS } from './embedUtils'

// Explicit "this embed URL is wrong" card — NEVER a silent DE_LU fallback. Shown for
// an unrecognized zone or metric so a broken embed link fails loudly (to whoever
// pasted it) instead of quietly always showing Germany.
export default function EmbedUnknownCard({ message }) {
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center',
      justifyContent: 'center', gap: 6, padding: '10px 14px', textAlign: 'center' }}>
      <div style={{ fontSize: 11, color: EMBED_COLORS.text }}>{message}</div>
      <div style={{ fontSize: 10, color: EMBED_COLORS.muted }}>
        Valid metrics: {VALID_METRICS.join(', ')}
      </div>
      <a href="https://obsyd.dev" target="_blank" rel="noopener noreferrer"
        style={{ fontSize: 10, color: EMBED_COLORS.accent, textDecoration: 'none', fontWeight: 700 }}>
        See all zones at obsyd.dev →
      </a>
    </div>
  )
}
