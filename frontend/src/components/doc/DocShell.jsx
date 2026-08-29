/**
 * Shared primitives for the document-style pages (landing, /docs, legal) and
 * section headers everywhere — the ONE place the academic-institutional page
 * language lives. Multi-export file, following the Panel.jsx pattern.
 *
 * All colors come from tokens/utility classes so both themes keep working;
 * typography leans on --font-serif (headings/wordmark) and --font-code (code).
 */

const GITHUB = 'https://github.com/jo20ow/Obsyd'

// Serif small-caps wordmark — the brand mark on every document page.
export function Wordmark({ href = '/', back = false }) {
  return (
    <a href={href} className="font-serif smallcaps text-[17px] font-semibold tracking-wide text-cyan-glow hover:opacity-80 transition-opacity">
      {back ? '← Obsyd' : 'Obsyd'}
    </a>
  )
}

// Section eyebrow: short accent overline + small-caps label. Replaces the four
// divergent variants (`// LABEL` eyebrows, tracking-[3px] caps). Pass UPPERCASE
// children — .smallcaps restyles without changing textContent, which keeps
// text-matching harnesses (verify-zone-coherence) green.
export function SectionLabel({ children, className = '' }) {
  return (
    <div className={className}>
      <div className="w-6 h-[2px] bg-cyan-glow mb-2" />
      <div className="smallcaps text-[12px] font-medium text-neutral-500">{children}</div>
    </div>
  )
}

// Code figure in REAL monospace (--font-code) with a quiet caption bar.
export function CodeBlock({ title, className = '', children }) {
  return (
    <figure className={`border border-border bg-neutral-900 rounded overflow-hidden ${className}`}>
      {title && (
        <figcaption className="px-4 py-2 border-b border-border/60 smallcaps text-[11px] text-neutral-500">
          {title}
        </figcaption>
      )}
      <pre className="px-4 py-4 text-[12px] leading-relaxed text-neutral-300 font-code overflow-x-auto">
        {children}
      </pre>
    </figure>
  )
}

// CTA link: primary = solid accent, secondary = hairline border. Sentence case,
// no letterspacing — buttons are apparatus, not decoration.
export function LinkButton({ href, external = false, primary = false, children }) {
  const extra = external ? { target: '_blank', rel: 'noopener noreferrer' } : {}
  return (
    <a
      href={href}
      {...extra}
      className={
        primary
          ? 'px-5 py-2.5 text-[13px] font-medium bg-cyan-glow text-surface hover:opacity-90 rounded text-center transition-opacity'
          : 'px-5 py-2.5 text-[13px] border border-border text-neutral-300 hover:text-cyan-glow hover:border-cyan-glow/50 rounded text-center transition-colors'
      }
    >
      {children}
    </a>
  )
}

// Page frame for document pages: paper background (body token), top bar with
// wordmark + quiet nav, measured column, legal footer.
export default function DocShell({ children, maxWidth = 'max-w-3xl', nav = null }) {
  return (
    <div className="min-h-screen text-neutral-300">
      <header className="border-b border-border bg-surface">
        <div className={`${maxWidth} mx-auto px-5 py-3 flex items-center justify-between`}>
          <Wordmark back />
          <nav className="flex items-center gap-4 text-[11px] text-neutral-500">
            {nav}
            <a href={GITHUB} target="_blank" rel="noopener noreferrer" className="hover:text-neutral-300">
              GitHub
            </a>
            <a href="/app" className="text-cyan-glow hover:opacity-80">
              Open the desk →
            </a>
          </nav>
        </div>
      </header>
      <main className={`${maxWidth} mx-auto px-5 py-12`}>{children}</main>
      <footer className="border-t border-border bg-surface">
        <div className={`${maxWidth} mx-auto px-5 py-6 text-[11px] text-neutral-600`}>
          <a href="/" className="text-cyan-glow hover:underline">obsyd.dev</a>
          {' · '}
          <a href={GITHUB} target="_blank" rel="noopener noreferrer" className="text-cyan-glow hover:underline">GitHub</a>
          {' · '}
          <a href="/impressum" className="hover:text-neutral-400">Impressum</a>
          {' · '}
          <a href="/datenschutz" className="hover:text-neutral-400">Datenschutz</a>
        </div>
      </footer>
    </div>
  )
}
