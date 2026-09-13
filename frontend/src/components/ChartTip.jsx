/**
 * MultiTip — shared recharts Tooltip CONTENT for multi-series charts.
 *
 * The default tooltip renders every row in the same ink (our own light-theme
 * CSS even forces it to one color), so on a stacked fuel chart nothing tied a
 * row to its band — you read "Hydro Reservoir : 1.7 GW" and had to guess
 * which layer that was. Per the design doctrine (text wears ink, a MARK
 * carries identity): each row gets its series' color as a dot, the text stays
 * in theme tokens. The box uses token classes (bg-surface/border-border), so
 * it follows the theme without the `.recharts-default-tooltip` CSS override.
 *
 * Drop-in: <Tooltip content={<MultiTip />} formatter={…} labelFormatter={…} />
 * — the formatter/labelFormatter props keep their recharts signatures.
 */
export default function MultiTip({ active, payload, label, formatter, labelFormatter }) {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-surface border border-border rounded-lg px-3 py-2 text-[12px] shadow-sm">
      <div className="text-neutral-500 mb-1">
        {labelFormatter ? labelFormatter(label, payload) : label}
      </div>
      {payload.map((entry, i) => {
        const [val, name] = formatter
          ? formatter(entry.value, entry.name, entry, i, payload)
          : [entry.value, entry.name]
        return (
          <div key={`${entry.dataKey}-${i}`} className="flex items-center gap-1.5 leading-relaxed">
            <span
              className="inline-block w-2 h-2 rounded-full shrink-0"
              style={{ background: entry.color || entry.payload?.fill || '#888' }}
            />
            <span className="text-neutral-500">{name}</span>
            <span className="ml-auto pl-3 text-neutral-200 font-mono text-[11px]">{val}</span>
          </div>
        )
      })}
    </div>
  )
}
