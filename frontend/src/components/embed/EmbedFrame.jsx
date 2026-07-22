import { EMBED_COLORS } from './embedUtils'

// Minimal chrome around every /embed/<zone>/<metric> widget: a one-line header (zone +
// metric title), the metric's own content, a freshness caption and the REQUIRED
// attribution footer. Fills the iframe completely (100vw/100vh, no page margin — see
// index.css: body already has margin:0, so nothing extra is needed there).
//
// `freshness` is optional: { label: string, stale: bool } — the caption text and
// whether to render it in the warning color. Null renders no caption (e.g. while the
// zone/metric hasn't resolved yet).

const WRAP_STYLE = {
  width: '100vw',
  height: '100vh',
  minHeight: 140,
  boxSizing: 'border-box',
  display: 'flex',
  flexDirection: 'column',
  background: EMBED_COLORS.bg,
  color: EMBED_COLORS.text,
  fontFamily: 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
  border: `1px solid ${EMBED_COLORS.panelBorder}`,
  overflow: 'hidden',
}

const HEADER_STYLE = {
  display: 'flex',
  alignItems: 'baseline',
  gap: 8,
  padding: '8px 10px 4px',
  flexShrink: 0,
}

const ZONE_STYLE = { fontSize: 12, fontWeight: 700, color: EMBED_COLORS.text }
const TITLE_STYLE = { fontSize: 10, color: EMBED_COLORS.muted, letterSpacing: '0.02em' }

const BODY_STYLE = {
  flex: 1,
  minHeight: 0,
  display: 'flex',
  flexDirection: 'column',
  padding: '0 4px',
}

const FOOTER_STYLE = {
  flexShrink: 0,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  gap: 8,
  padding: '4px 10px 6px',
  fontSize: 9,
  borderTop: `1px solid ${EMBED_COLORS.panelBorder}`,
}

const LINK_STYLE = { color: EMBED_COLORS.accent, fontWeight: 700, textDecoration: 'none' }

export default function EmbedFrame({ zoneLabel, metricTitle, freshness, children }) {
  return (
    <div style={WRAP_STYLE}>
      <div style={HEADER_STYLE}>
        <span style={ZONE_STYLE}>{zoneLabel}</span>
        <span style={TITLE_STYLE}>{metricTitle}</span>
      </div>
      <div style={BODY_STYLE}>{children}</div>
      <div style={FOOTER_STYLE}>
        <span style={{ color: freshness?.stale ? EMBED_COLORS.warn : EMBED_COLORS.faint }}>
          {freshness?.label ?? ' '}
        </span>
        <span style={{ color: EMBED_COLORS.faint, whiteSpace: 'nowrap' }}>
          <a href="https://obsyd.dev" target="_blank" rel="noopener noreferrer" style={LINK_STYLE}>
            OBSYD
          </a>
          {' · obsyd.dev — data: ENTSO-E'}
        </span>
      </div>
    </div>
  )
}
