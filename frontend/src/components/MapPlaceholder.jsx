export default function MapPlaceholder({ zones }) {
  return (
    <div className="border border-border bg-surface rounded">
      <div className="px-4 py-2.5 border-b border-border">
        <span className="font-mono text-xs text-neutral-500">
          AIS VESSEL MAP // GEOFENCE MONITORING
        </span>
      </div>

      <div className="flex items-center justify-center h-[220px] bg-surface-light">
        <div className="text-center">
          <div className="font-mono text-sm text-neutral-600 mb-1">
            [  AIS VESSEL MAP  ]
          </div>
          <div className="font-mono text-xs text-neutral-700">
            deck.gl integration -- coming soon
          </div>
        </div>
      </div>

      <div className="px-4 py-3 border-t border-border">
        <div className="font-mono text-[10px] text-neutral-600 mb-2 tracking-wider">
          ACTIVE GEOFENCE ZONES
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
          {zones.map((z) => (
            <div
              key={z.name}
              className="border border-border bg-surface-light rounded px-3 py-2 group hover:border-cyan-glow/30 transition-colors"
            >
              <div className="font-mono text-xs text-cyan-glow">
                {z.name.toUpperCase()}
              </div>
              <div className="font-mono text-[10px] text-neutral-600 mt-0.5 leading-tight">
                {z.display_name}
              </div>
              <div className="font-mono text-[9px] text-neutral-700 mt-1 hidden group-hover:block">
                {z.bounds[0][0].toFixed(1)},{z.bounds[0][1].toFixed(1)} /
                {z.bounds[1][0].toFixed(1)},{z.bounds[1][1].toFixed(1)}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
