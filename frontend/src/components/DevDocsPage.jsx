/**
 * /docs — the developer front door. A one-stop quickstart for the public data
 * API: base URL, auth (none), curl + Python in 30 seconds, endpoint overview,
 * formats, citation. Deliberately hand-written page copy (no ⓘ popovers, no
 * markdown renderer): docs/API.md on GitHub stays the deep reference, Swagger
 * (/api/docs) the interactive one — this page is the map to both.
 */

const GITHUB = 'https://github.com/jo20ow/Obsyd'
const API_MD = `${GITHUB}/blob/main/docs/API.md`

const QUICK_LINKS = [
  { label: 'Swagger UI ↗', href: '/api/docs', external: false },
  { label: 'ReDoc ↗', href: '/api/redoc', external: false },
  { label: 'OpenAPI JSON ↗', href: '/api/openapi.json', external: false },
  { label: 'Full reference (docs/API.md) ↗', href: API_MD, external: true },
]

const ENDPOINTS = [
  ['/series', 'One time series for one zone over a date range — the core endpoint. format=json|csv|parquet, resolution=hourly|daily.'],
  ['/series/catalog', 'Every queryable series, the enabled zones and the coverage window — check it before writing a line of code.'],
  ['/snapshot', 'A recent window of ONE series across EVERY enabled zone in a single request.'],
  ['/genmix', 'Generation mix over time for one zone, wide shape (one column per fuel).'],
  ['/zones', 'The bidding-zone registry with labels and flags.'],
  ['/capacity', 'Installed generation capacity per production type for a zone-year.'],
  ['/units', 'Named production units (EIC, name, fuel, nominal MW) for one zone.'],
  ['/meta', 'Sources, licenses, attribution, enabled zones, disclaimer.'],
  ['/status', 'Honest data coverage: per-source freshness and an overall healthy flag.'],
  ['/quality/*', "Data-quality layer: per-series completeness, arrival lags and a revision ledger of the source's own restatements."],
  ['/scoreboard/*', "ENTSO-E's own D-1 forecasts (load, wind, solar, residual) graded against naive baselines, per zone, monthly back to 2021."],
]

function SectionLabel({ children }) {
  return <div className="text-[10px] tracking-[3px] text-neutral-500 mb-3">{children}</div>
}

function CodeBlock({ title, children }) {
  return (
    <div className="border border-border bg-[#0a0a12] rounded overflow-hidden mb-6">
      <div className="px-4 py-2 border-b border-border/60 text-[10px] tracking-widest text-neutral-600">
        {title}
      </div>
      <pre className="px-4 py-4 text-[12px] leading-relaxed text-neutral-300 font-mono overflow-x-auto">
        {children}
      </pre>
    </div>
  )
}

function LinkButton({ href, external = false, primary = false, children }) {
  const extra = external ? { target: '_blank', rel: 'noopener noreferrer' } : {}
  return (
    <a
      href={href}
      {...extra}
      className={
        primary
          ? 'px-6 py-3 text-[11px] tracking-wider bg-cyan-glow text-[#0a0a12] hover:bg-cyan-glow/90 transition-colors font-semibold text-center'
          : 'px-6 py-3 text-[11px] tracking-wider border border-border text-neutral-400 hover:text-cyan-glow hover:border-cyan-glow/40 transition-colors text-center'
      }
    >
      {children}
    </a>
  )
}

export default function DevDocsPage() {
  return (
    <div className="min-h-screen bg-[#06060a] text-neutral-300 font-mono">
      <header className="border-b border-border">
        <div className="max-w-3xl mx-auto px-4 py-3 flex items-center justify-between">
          <a href="/" className="text-cyan-glow text-[13px] tracking-[4px] font-bold">
            ← OBSYD
          </a>
          <nav className="flex items-center gap-4 text-[10px] tracking-wider text-neutral-500">
            <a
              href={GITHUB}
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-neutral-200"
            >
              GITHUB
            </a>
            <a href="/app" className="hover:text-cyan-glow">
              OPEN THE DESK →
            </a>
          </nav>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-4 py-12">
        {/* HEADER */}
        <div className="text-[10px] tracking-[4px] text-cyan-glow mb-4">API &amp; DEVELOPER DOCS</div>
        <h1 className="text-2xl sm:text-3xl text-neutral-100 font-bold mb-4 leading-snug">
          The European power record, <span className="text-cyan-glow">one request away</span>.
        </h1>
        <p className="text-[13px] text-neutral-400 leading-relaxed max-w-2xl mb-6">
          A free, versioned public API over the canonical European power record — day-ahead prices,
          load, generation mix, forecasts, cross-border flows and more for 37 bidding zones.
          No key, no account, no registration.
        </p>
        <div className="flex flex-wrap gap-2 mb-14">
          {QUICK_LINKS.map((l) => (
            <a
              key={l.href}
              href={l.href}
              {...(l.external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
              className="font-mono text-[10px] tracking-wider border border-border rounded px-2.5 py-1 text-neutral-400 hover:text-cyan-glow hover:border-cyan-glow/40 transition-colors"
            >
              {l.label}
            </a>
          ))}
        </div>

        {/* QUICKSTART */}
        <section className="mb-14">
          <SectionLabel>// QUICKSTART</SectionLabel>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-px bg-border mb-6">
            <div className="bg-[#0a0a12] p-4">
              <div className="text-[10px] tracking-widest text-neutral-600 mb-1">BASE URL</div>
              <div className="text-[12px] text-neutral-200 break-all">https://obsyd.dev/api/v1</div>
            </div>
            <div className="bg-[#0a0a12] p-4">
              <div className="text-[10px] tracking-widest text-neutral-600 mb-1">AUTH</div>
              <div className="text-[12px] text-neutral-200">none — fully public</div>
            </div>
            <div className="bg-[#0a0a12] p-4">
              <div className="text-[10px] tracking-widest text-neutral-600 mb-1">RATE LIMIT</div>
              <div className="text-[12px] text-neutral-200">~120 req/min per IP</div>
            </div>
          </div>
          <CodeBlock title="CURL">
            <span className="text-neutral-600"># day-ahead prices for DE-LU, daily mean, last 30 days</span>
            {'\n'}
            <span className="text-neutral-600">$ </span>curl <span className="text-amber-300">&quot;https://obsyd.dev/api/v1/series?series=price.dayahead&amp;zone=DE_LU&amp;resolution=daily&quot;</span>
          </CodeBlock>
          <p className="text-[11px] text-neutral-500 leading-relaxed">
            All timestamps are UTC. &quot;Nothing found&quot; (unknown series, empty window) is HTTP 200 with{' '}
            <span className="text-neutral-300">available:false</span> and a reason — never a bare 4xx.
          </p>
        </section>

        {/* PYTHON */}
        <section className="mb-14">
          <SectionLabel>// PYTHON</SectionLabel>
          <CodeBlock title="PYTHON">
            <span className="text-neutral-600">$ </span>pip install obsyd
            {'\n\n'}
            <span className="text-cyan-glow">from</span> obsyd <span className="text-cyan-glow">import</span> Obsyd
            {'\n'}
            df = Obsyd().series(<span className="text-amber-300">&quot;price.dayahead&quot;</span>, <span className="text-amber-300">&quot;DE_LU&quot;</span>, start=<span className="text-amber-300">&quot;2024-01-01&quot;</span>)
            {'\n'}
            <span className="text-neutral-600"># → a pandas DataFrame, UTC-indexed. That&apos;s it.</span>
          </CodeBlock>
          <div className="flex flex-col sm:flex-row gap-3">
            <LinkButton href="https://pypi.org/project/obsyd/" external>
              obsyd on PyPI
            </LinkButton>
            <LinkButton href={`${GITHUB}/tree/main/clients/python`} external>
              Client + example notebooks
            </LinkButton>
          </div>
        </section>

        {/* ENDPOINTS */}
        <section className="mb-14">
          <SectionLabel>// ENDPOINTS</SectionLabel>
          <div className="border border-border bg-[#0a0a12] rounded divide-y divide-border/60">
            {ENDPOINTS.map(([path, desc]) => (
              <div key={path} className="px-4 py-2.5 flex flex-col sm:flex-row sm:items-baseline gap-1 sm:gap-4">
                <div className="text-[11px] text-cyan-glow shrink-0 sm:w-36">{path}</div>
                <div className="text-[11px] text-neutral-500 leading-relaxed">{desc}</div>
              </div>
            ))}
          </div>
          <p className="mt-3 text-[11px] text-neutral-500">
            Full parameter reference with every series key:{' '}
            <a href={API_MD} target="_blank" rel="noopener noreferrer" className="text-cyan-glow hover:underline">
              docs/API.md on GitHub
            </a>
            {' · '}interactive:{' '}
            <a href="/api/docs" className="text-cyan-glow hover:underline">
              Swagger UI
            </a>
          </p>
        </section>

        {/* FORMATS */}
        <section className="mb-14">
          <SectionLabel>// FORMATS</SectionLabel>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-px bg-border">
            <div className="bg-[#0a0a12] p-4">
              <div className="text-[11px] text-neutral-200 mb-1.5">JSON (default)</div>
              <div className="text-[11px] text-neutral-500 leading-relaxed">
                For quick reads. Responses over ~100k points answer{' '}
                <span className="text-neutral-300">available:false</span> — switch to CSV/Parquet.
              </div>
            </div>
            <div className="bg-[#0a0a12] p-4">
              <div className="text-[11px] text-neutral-200 mb-1.5">CSV — format=csv</div>
              <div className="text-[11px] text-neutral-500 leading-relaxed">
                Streamed, no point cap. Loads straight into pandas / R / a spreadsheet.
              </div>
            </div>
            <div className="bg-[#0a0a12] p-4">
              <div className="text-[11px] text-neutral-200 mb-1.5">Parquet — format=parquet</div>
              <div className="text-[11px] text-neutral-500 leading-relaxed">
                Columnar and compact — the right choice for bulk pulls into pandas/Arrow.
              </div>
            </div>
          </div>
        </section>

        {/* EXPLORE IN THE BROWSER */}
        <section className="mb-14">
          <SectionLabel>// EXPLORE IN THE BROWSER</SectionLabel>
          <p className="text-[13px] text-neutral-400 leading-relaxed max-w-2xl mb-5">
            Every series behind this API is also explorable visually — find the series and zone you
            need, then copy the query.
          </p>
          <div className="flex flex-col sm:flex-row gap-3">
            <LinkButton href="/app#explore" primary>
              Series explorer on the desk →
            </LinkButton>
            <LinkButton href="/builder">Full-screen chart builder</LinkButton>
          </div>
        </section>

        {/* CITE */}
        <section className="mb-14">
          <SectionLabel>// CITE</SectionLabel>
          <p className="text-[13px] text-neutral-400 leading-relaxed max-w-2xl mb-4">
            OBSYD is citable. If it feeds a paper, a thesis or a dataset, cite the archived release:
          </p>
          <CodeBlock title="CITATION">
            Weisser, J. OBSYD — The European Electricity Desk (open-source software &amp; data API).
            {'\n'}
            DOI: <span className="text-amber-300">10.5281/zenodo.21699869</span> · https://obsyd.dev
          </CodeBlock>
        </section>

        {/* SELF-HOST */}
        <section className="mb-14">
          <SectionLabel>// SELF-HOST</SectionLabel>
          <p className="text-[13px] text-neutral-400 leading-relaxed max-w-2xl mb-5">
            AGPL-3.0, end to end. Run the exact same API on your own infra — including a frozen copy
            of the data for reproducible research.
          </p>
          <LinkButton href={GITHUB} external>
            Source on GitHub
          </LinkButton>
        </section>
      </main>

      <footer className="border-t border-border bg-[#0a0a12]">
        <div className="max-w-3xl mx-auto px-4 py-6 text-[10px] text-neutral-600">
          <a href="/" className="text-cyan-glow hover:underline">obsyd.dev</a>
          {' · '}
          <a href={GITHUB} target="_blank" rel="noopener noreferrer" className="text-cyan-glow hover:underline">GitHub</a>
          {' · '}
          <a href="/impressum" className="text-neutral-500 hover:underline">Impressum</a>
          {' · '}
          <a href="/datenschutz" className="text-neutral-500 hover:underline">Datenschutz</a>
        </div>
      </footer>
    </div>
  )
}
