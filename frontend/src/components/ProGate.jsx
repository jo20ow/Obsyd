import { useAuth } from '../context/AuthContext'

export default function ProGate({ children, feature = 'This feature' }) {
  const { isPro, openPricing } = useAuth()

  if (isPro) return children

  return (
    <div className="relative">
      <div className="pointer-events-none select-none" style={{ filter: 'blur(4px)', opacity: 0.3 }}>
        {children}
      </div>
      <div className="absolute inset-0 flex items-center justify-center bg-[#0a0a12]/60">
        <div className="flex flex-col items-center gap-3 font-mono text-center max-w-xs px-4">
          <div className="flex items-center gap-2">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4 text-neutral-500">
              <path fillRule="evenodd" d="M10 1a4.5 4.5 0 00-4.5 4.5V9H5a2 2 0 00-2 2v6a2 2 0 002 2h10a2 2 0 002-2v-6a2 2 0 00-2-2h-.5V5.5A4.5 4.5 0 0010 1zm3 8V5.5a3 3 0 10-6 0V9h6z" clipRule="evenodd" />
            </svg>
            <span className="text-[11px] text-neutral-300">{feature}</span>
          </div>
          <button
            type="button"
            onClick={openPricing}
            className="text-[11px] tracking-wider bg-cyan-glow/10 hover:bg-cyan-glow/20 border border-cyan-glow/40 text-cyan-glow px-4 py-1.5 transition-colors"
          >
            Upgrade to Pro →
          </button>
        </div>
      </div>
    </div>
  )
}
