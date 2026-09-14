import { useMemo } from 'react'
import { marked } from 'marked'
import DocShell, { SectionLabel } from './doc/DocShell'
// The reference IS docs/API.md — bundled at build time, so obsyd.dev renders
// the same file the repo carries and the two can never drift. (Until now the
// deepest doc the project had lived only as a GitHub blob link — the survey's
// bluntest finding.)
import referenceMd from '../../../docs/API.md?raw'

/**
 * /docs/reference — the full API reference on the product's own domain.
 * marked renders our own trusted file (no user input → no sanitizer needed);
 * styling rides a scoped class so the markdown tables/code inherit the doc
 * design language.
 */
export default function DevReferencePage() {
  const html = useMemo(() => {
    const raw = marked.parse(referenceMd, { async: false })
    // Deep-linkable sections: slugified ids on h1-h3 (marked adds none itself).
    return raw.replace(/<h([1-3])>(.*?)<\/h\1>/g, (m, level, inner) => {
      const slug = inner
        .replace(/<[^>]+>/g, '')
        .toLowerCase()
        .replace(/[^a-z0-9\s-]/g, '')
        .trim()
        .replace(/\s+/g, '-')
      return `<h${level} id="${slug}">${inner}</h${level}>`
    })
  }, [])
  return (
    <DocShell maxWidth="max-w-4xl">
      <div className="flex items-baseline justify-between gap-4 mb-6">
        <SectionLabel>API REFERENCE</SectionLabel>
        <a
          href="https://github.com/jo20ow/Obsyd/blob/main/docs/API.md"
          className="font-mono text-[10px] text-neutral-600 hover:text-cyan-glow"
        >
          source: docs/API.md ↗
        </a>
      </div>
      <div
        className="obsyd-md text-[13px] leading-relaxed text-neutral-300"
        // Our own bundled markdown file — trusted content, rendered verbatim.
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </DocShell>
  )
}
