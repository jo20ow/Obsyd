/**
 * /impressum + /datenschutz — static legal pages (§ 5 DDG / § 18 MStV, Art. 13
 * DSGVO). German by design: they serve German law. Content states only what
 * the site actually does — no generator boilerplate walls.
 */

const ADDRESS = (
  <p className="leading-relaxed">
    Johannes Weisser
    <br />
    c/o MDC#weisser
    <br />
    Welserstraße 3
    <br />
    87463 Dietmannsried
    <br />
    Deutschland
  </p>
)

const EMAIL = 'obsyd.dev@pm.me'

function Impressum() {
  return (
    <>
      <h1 className="text-2xl font-bold text-neutral-100 mb-6">Impressum</h1>
      <h2 className="text-sm font-semibold text-neutral-200 mt-6 mb-2">
        Anbieter (§ 5 DDG, § 18 Abs. 1 MStV)
      </h2>
      {ADDRESS}
      <h2 className="text-sm font-semibold text-neutral-200 mt-6 mb-2">Kontakt</h2>
      <p>
        E-Mail: <a className="text-cyan-glow" href={`mailto:${EMAIL}`}>{EMAIL}</a>
      </p>
      <h2 className="text-sm font-semibold text-neutral-200 mt-6 mb-2">
        Inhaltlich verantwortlich (§ 18 Abs. 2 MStV)
      </h2>
      <p>Johannes Weisser (Anschrift wie oben)</p>
      <h2 className="text-sm font-semibold text-neutral-200 mt-6 mb-2">Hinweise</h2>
      <p className="leading-relaxed">
        OBSYD ist ein kostenloses, quelloffenes (AGPL-3.0) Beobachtungswerkzeug für
        den europäischen Strommarkt. Alle Darstellungen sind deskriptiv und beruhen
        auf öffentlichen amtlichen Quellen (ENTSO-E Transparency Platform, Fraunhofer
        Energy-Charts, GIE); sie sind keine Anlageberatung und keine Prognose. Für
        Inhalte externer verlinkter Seiten sind deren Betreiber verantwortlich.
      </p>
    </>
  )
}

function Datenschutz() {
  return (
    <>
      <h1 className="text-2xl font-bold text-neutral-100 mb-6">Datenschutzerklärung</h1>

      <h2 className="text-sm font-semibold text-neutral-200 mt-6 mb-2">Verantwortlicher</h2>
      {ADDRESS}
      <p className="mt-2">
        E-Mail: <a className="text-cyan-glow" href={`mailto:${EMAIL}`}>{EMAIL}</a>
      </p>

      <h2 className="text-sm font-semibold text-neutral-200 mt-6 mb-2">
        1. Server-Logs (Hosting)
      </h2>
      <p className="leading-relaxed">
        Beim Aufruf der Seite verarbeitet unser Server technisch bedingt IP-Adresse,
        Zeitpunkt, aufgerufene URL und User-Agent in Server-Logdateien. Rechtsgrundlage
        ist Art. 6 Abs. 1 lit. f DSGVO (berechtigtes Interesse: Betrieb, Stabilität und
        Missbrauchsabwehr). Die Logs werden turnusmäßig gelöscht und nicht mit anderen
        Daten zusammengeführt. Gehostet wird bei Hostinger (Hostinger International Ltd.)
        auf einem Server in Deutschland (Frankfurt am Main).
      </p>

      <h2 className="text-sm font-semibold text-neutral-200 mt-6 mb-2">
        2. Reichweitenmessung (Plausible)
      </h2>
      <p className="leading-relaxed">
        Wir nutzen Plausible Analytics (Plausible Insights OÜ, Estland/EU) — eine
        cookielose Reichweitenmessung ohne geräteübergreifendes Tracking und ohne
        Bildung von Nutzerprofilen; IP-Adressen werden nicht gespeichert.
        Rechtsgrundlage ist Art. 6 Abs. 1 lit. f DSGVO (berechtigtes Interesse:
        aggregierte Nutzungsstatistik).
      </p>

      <h2 className="text-sm font-semibold text-neutral-200 mt-6 mb-2">
        3. Login per Magic-Link (optional)
      </h2>
      <p className="leading-relaxed">
        Die Seite ist ohne Konto vollständig nutzbar. Wer die optionalen Funktionen
        Watchlist und Alert-Regeln nutzen möchte, meldet sich per E-Mail-Link an. Dazu
        speichern wir die E-Mail-Adresse sowie die angelegten Watchlist-Einträge und
        Alert-Regeln (Art. 6 Abs. 1 lit. b DSGVO). Der Login-Link wird über den
        Versanddienstleister Resend (Resend, Inc., USA) zugestellt; mit Resend besteht
        ein Auftragsverarbeitungsvertrag, die Übermittlung in die USA stützt sich auf
        EU-Standardvertragsklauseln (Art. 46 DSGVO). Nach dem Login setzen wir ein
        technisch notwendiges Session-Cookie (kein Tracking). Konto und alle
        zugehörigen Daten löschen wir auf formlose Anfrage an die oben genannte
        E-Mail-Adresse.
      </p>

      <h2 className="text-sm font-semibold text-neutral-200 mt-6 mb-2">4. Keine Weitergabe</h2>
      <p className="leading-relaxed">
        Eine Weitergabe personenbezogener Daten an Dritte findet über die genannten
        Auftragsverarbeiter hinaus nicht statt. Es findet keine automatisierte
        Entscheidungsfindung und kein Profiling statt.
      </p>

      <h2 className="text-sm font-semibold text-neutral-200 mt-6 mb-2">5. Ihre Rechte</h2>
      <p className="leading-relaxed">
        Sie haben nach Maßgabe der Art. 15–21 DSGVO das Recht auf Auskunft,
        Berichtigung, Löschung, Einschränkung der Verarbeitung, Datenübertragbarkeit
        und Widerspruch. Wenden Sie sich dazu an die oben genannte E-Mail-Adresse.
        Außerdem besteht ein Beschwerderecht bei einer Datenschutz-Aufsichtsbehörde;
        zuständig ist das Bayerische Landesamt für Datenschutzaufsicht (BayLDA),
        Ansbach.
      </p>

      <p className="mt-8 text-neutral-500 text-[12px]">Stand: 18. Juli 2026</p>
    </>
  )
}

export default function LegalPage({ page }) {
  return (
    <div className="min-h-screen bg-surface text-neutral-400 text-[13px]">
      <div className="max-w-2xl mx-auto px-5 py-12">
        <a href="/" className="font-mono text-[11px] tracking-[3px] text-cyan-glow">
          ← OBSYD
        </a>
        <div className="mt-8">{page === 'impressum' ? <Impressum /> : <Datenschutz />}</div>
        <div className="mt-10 pt-4 border-t border-border text-[11px] text-neutral-600">
          <a href="/impressum" className="hover:text-neutral-400">Impressum</a>
          {' · '}
          <a href="/datenschutz" className="hover:text-neutral-400">Datenschutz</a>
        </div>
      </div>
    </div>
  )
}
