import { useAuth } from '../context/AuthContext'
import AuthButton from './AuthButton'
import DocShell, { LinkButton, SectionLabel } from './doc/DocShell'

/**
 * /login — the magic-link login as its own UNLISTED route. The sidebar login
 * button was removed on purpose (the public chrome stays slim); accounts still
 * exist for the free per-user features (alerts, watchlist) and for premium
 * sessions, so the FORM needs a home. Nothing links here except the alerts
 * tab's login prompt; not in the sitemap.
 */
export default function LoginPage() {
  const { user } = useAuth()
  return (
    <DocShell maxWidth="max-w-md">
      <SectionLabel className="mb-4">LOG IN</SectionLabel>
      <div className="bg-surface border border-border rounded p-6 space-y-4">
        <p className="text-[12px] text-neutral-400 leading-relaxed">
          A magic link, no password: enter your email, click the link that
          arrives, and this browser is signed in. Accounts are free and power
          the per-user features (alert rules, watchlist).
        </p>
        <AuthButton />
        {user?.authenticated && (
          <div className="pt-2">
            <LinkButton href="/app" primary>Open the desk →</LinkButton>
          </div>
        )}
      </div>
    </DocShell>
  )
}
