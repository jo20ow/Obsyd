import { useAuth } from '../context/AuthContext'

export default function ProGate({ children, feature = 'This feature' }) {
  const { isPro } = useAuth()

  if (isPro) return children

  return (
    <div className="relative">
      <div className="pointer-events-none select-none" style={{ filter: 'blur(4px)', opacity: 0.3 }}>
        {children}
      </div>
      <div className="absolute inset-0 flex items-center justify-center bg-[#0a0a12]/50">
        <div className="flex items-center gap-2.5 font-mono">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4 text-neutral-600">
            <path fillRule="evenodd" d="M10 1a4.5 4.5 0 00-4.5 4.5V9H5a2 2 0 00-2 2v6a2 2 0 002 2h10a2 2 0 002-2v-6a2 2 0 00-2-2h-.5V5.5A4.5 4.5 0 0010 1zm3 8V5.5a3 3 0 10-6 0V9h6z" clipRule="evenodd" />
          </svg>
          <span className="text-[11px] text-neutral-400">{feature}</span>
          <span className="text-[10px] text-neutral-600">—</span>
          <span className="text-[10px] text-cyan-glow/60">Unlock with Pro</span>
          <span className="text-[10px] text-neutral-600">€9/mo</span>
        </div>
      </div>
    </div>
  )
}
