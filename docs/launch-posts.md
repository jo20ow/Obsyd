# Launch-Post-Entwürfe (Stand 2026-07-11)

Reihenfolge nach Erwartungswert: HN zuerst (Di–Mi, 15–17 Uhr MESZ = 9–11 EST),
r/energy + Lobsters innerhalb 48 h nachziehen, LinkedIn parallel. Vor dem Posten:
Plausible-Konto aktivieren (sonst launchen wir blind) und einmal alle Links klicken.

---

## Show HN

**Titel (≤ 80 Zeichen):**

> Show HN: A free "gridstatus for Europe" – 37 zones, 15-min prices, outage board

**Text:**

> I built OBSYD because following the European power grid meant reconciling a dozen
> ENTSO-E Transparency queries by hand. gridstatus.io solved this for US ISOs; nothing
> free and legible existed for Europe.
>
> One desk, 37 bidding zones: day-ahead prices at the market's real 15-minute
> resolution (SDAC has traded 15-min products since Oct 2025 — most sites still show
> hourly averages), residual load & Dunkelflaute, generation mix, cross-border flows,
> Nordic/Alpine reservoir levels vs their seasonal norm, and a live generation-outage
> board built on ENTSO-E's unavailability feed — revision-aware, because most raw
> outage messages are withdrawn revisions and counting them fabricates gigawatts.
>
> Two design rules I tried to hold everywhere: never a naked number (everything is
> "vs this zone's own history"), and honest about data age — every panel shows how
> old its data is, and a stalled feed says STALE instead of pretending. There's a
> measured forecast-error strip instead of any prediction of our own.
>
> All official, redistributable sources (ENTSO-E, Fraunhofer Energy-Charts CC BY 4.0,
> GIE). AGPL-3.0, self-hostable, no accounts, no paywall. Public read API with CSV.
>
> https://obsyd.dev — code: https://github.com/jo20ow/Obsyd
>
> Stack: FastAPI + SQLite (~25M-row hourly store on a small VPS), React/Recharts.
> Happy to answer anything about wrangling ENTSO-E's XML.

**HN-Kommentar-Vorbereitung (häufige Fragen):**
- „Warum SQLite?" → single-writer ingest, PK-geclusterte Range-Scans, 25M Zeilen
  problemlos; ein VPS, keine Ops.
- „Intraday?" → bewusst nicht: IDA-Preise sind nicht frei redistributierbar. Day-ahead
  QH + Imbalance QH sind es.
- „Wie ehrlich ist 37 Zonen?" → alle mit Preis/Load/Mix; Spark DE-referenziert (TTF-Leg
  überall), Flows via Energy-Charts wo Interconnector existieren; Lücken sind gelabelt.

---

## r/energy (Cross-Post-Basis für r/selfhosted mit Fokus AGPL/Self-Host)

**Titel:**

> I made a free, open-source dashboard for the European power grid — 37 bidding zones,
> 15-minute day-ahead prices, live plant-outage board (ENTSO-E data)

**Text:** (HN-Text kürzen, ersten Absatz behalten, Stack-Absatz weglassen,
Abschluss:) „It's completely free and AGPL — I built it because I wanted it to exist.
Feedback from anyone who actually trades or analyses EU power would be gold."

---

## LinkedIn (persönlicher, DE/EN je nach Netzwerk)

> Seit Oktober 2025 handelt der europäische Day-Ahead-Markt in 15-Minuten-Produkten —
> die meisten frei zugänglichen Seiten zeigen immer noch Stundenmittel.
>
> Ich habe deshalb OBSYD gebaut: ein kostenloser „gridstatus für Europa". 37 Gebotszonen,
> Preise in echter Auktionsauflösung, Residuallast & Dunkelflaute, ein Live-Board für
> Kraftwerksausfälle (revisionssicher aus dem ENTSO-E-Unavailability-Feed) und
> Speicher-Füllstände gegen ihre saisonale Norm.
>
> Zwei Regeln überall: keine nackte Zahl (alles „vs. eigene Historie"), und Ehrlichkeit
> über das Datenalter — ein hängender Feed sagt STALE statt so zu tun als ob.
>
> Open Source (AGPL), keine Accounts, keine Paywall: https://obsyd.dev

---

## Checkliste vor dem Absenden

- [ ] Plausible-Konto + Goal „app-open" aktiv
- [ ] `/api/v1/status` grün (alle Freshness-SPECS)
- [ ] OG-Preview testen (opengraph.xyz) — Bild zeigt „37 European bidding zones"
- [ ] Ein frischer Blick auf EUROPE-Tab mit leerem Cache (anonymer Browser)
- [ ] HN-Account-Karma-Regeln: als Submitter im Thread aktiv bleiben (erste 3 h)
