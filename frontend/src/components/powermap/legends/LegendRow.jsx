/* Row chrome for the OVERLAY legends (flow arcs, line outages): a full-width
   strip under the map carrying its own top border, padding and type — an
   overlay toggles independently of the active fill, so it cannot share the
   footer row the FILLS registry owns. The fill legends are the other shape
   entirely (bare spans); ./index.js states both contracts.

   `tone` is the only colour an overlay varies: a feed that failed outright has
   to shout. `tight` drops the row gap on the one-line status rows — they hold
   a single short string that can never wrap, so the row gap would be dead CSS. */
export default function LegendRow({ tone = 'text-neutral-600', tight = false, children }) {
  const cls = [
    'flex flex-wrap items-center gap-x-3',
    !tight && 'gap-y-0.5',
    'px-4 py-1.5 border-t border-border font-mono text-[9px]',
    tone,
  ].filter(Boolean).join(' ')
  return <div className={cls}>{children}</div>
}
