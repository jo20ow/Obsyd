export default function Header({ aisActive, gdeltActive }) {
  return (
    <header className="flex items-center justify-between border-b border-border pb-3">
      <div className="flex items-center gap-3">
        <div className="text-cyan-glow font-mono text-xl font-bold tracking-widest">
          OBSYD
        </div>
        <div className="text-neutral-500 font-mono text-xs hidden sm:block">
          // ENERGY MARKET INTELLIGENCE
        </div>
      </div>
      <div className="flex items-center gap-4">
        <StatusDot label="EIA" ok />
        <StatusDot label="FRED" ok />
        <StatusDot label="AIS" ok={aisActive} />
        <StatusDot label="GDELT" ok={gdeltActive} />
      </div>
    </header>
  )
}

function StatusDot({ label, ok }) {
  return (
    <div className="flex items-center gap-1.5 font-mono text-xs">
      <div
        className={`w-1.5 h-1.5 rounded-full ${
          ok ? 'bg-green-glow shadow-[0_0_4px_var(--color-green-glow)]' : 'bg-neutral-600'
        }`}
      />
      <span className={ok ? 'text-neutral-400' : 'text-neutral-600'}>{label}</span>
    </div>
  )
}
