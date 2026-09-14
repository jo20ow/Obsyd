import { useMemo } from 'react'
import { marked } from 'marked'
import DocShell, { SectionLabel } from './doc/DocShell'
// Bundled at build time — the site renders the same CHANGELOG.md the repo
// carries, so the two can never drift.
import changelogMd from '../../../CHANGELOG.md?raw'

/** /changelog — release notes for data consumers, on the product's own domain. */
export default function ChangelogPage() {
  const html = useMemo(() => marked.parse(changelogMd, { async: false }), [])
  return (
    <DocShell maxWidth="max-w-3xl">
      <div className="flex items-baseline justify-between gap-4 mb-6">
        <SectionLabel>CHANGELOG</SectionLabel>
        <a
          href="https://github.com/jo20ow/Obsyd/blob/main/CHANGELOG.md"
          className="font-mono text-[10px] text-neutral-600 hover:text-cyan-glow"
        >
          source: CHANGELOG.md ↗
        </a>
      </div>
      <div
        className="obsyd-md text-[13px] leading-relaxed text-neutral-300"
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </DocShell>
  )
}
