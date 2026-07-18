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
> GIE). AGPL-3.0, self-hostable, no accounts, no paywall. Public read API with
> CSV/Parquet, and a Python client: `pip install obsyd` gets you any series as a
> DataFrame in two lines (example notebooks in the repo).
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

---

# Erweiterung 2026-07-18 — Reddit-first-Fahrplan (ersetzt die Reihenfolge oben)

Owner-Stärke ist Reddit → Reddit-Anteil ausgebaut. Grundregeln: nie zwei Subs am
selben Tag (Sitewide-Spam-Filter), vom gealterten Privat-Account posten (nie ein
frischer „obsyd"-Account), erste 2–3 h nach jedem Post aktiv antworten, Di–Do
14–16 Uhr MESZ für EU+US-Overlap.

| Tag | Kanal | Was |
|---|---|---|
| So/Mo | r/selfhosted | Soft-Launch (Entwurf unten) — schüttelt Setup-/README-Lücken raus |
| Di 15–17 MESZ | Show HN | Haupttermin (Entwurf oben), 3–4 h am Rechner bleiben |
| Mi | r/energy | Entwurf oben, als Text-Post in Ich-Form |
| Do | r/dataisbeautiful | [OC]-Heatmap (unten) — größter Reichweiten-Hebel |
| Fr / Wo. 2 | r/Energiewirtschaft | deutscher Post (unten) |
| Wo. 2 | r/InternetIsBeautiful, r/opensource | Kurzfassungen |
| Wo. 2, parallel | Data Is Plural, Console.dev, Changelog News, awesome-selfhosted-PR | Submit-und-vergessen |

LinkedIn (Entwurf oben) nur, wenn die Klarnamen-Ausnahme von der
Anonym-Positionierung bewusst gewollt ist — Owner-Entscheidung.
Falls Show HN < 5 Punkte: nicht löschen, Repost nach 4–6 Wochen ist legitim.

---

## r/selfhosted (Soft-Launch, So/Mo)

**Titel:**

> OBSYD — self-hosted European power-grid desk (37 bidding zones, 15-min day-ahead
> prices, outage board). FastAPI + SQLite + React, AGPL

**Text:**

> I run this on a single small VPS: FastAPI + one SQLite file (~28M rows of hourly
> data — single-writer ingest, PK-clustered range scans, no ops) + a static React
> build behind a reverse proxy. Data comes from ENTSO-E Transparency, Fraunhofer
> Energy-Charts (CC BY 4.0) and GIE — all free and redistributable, you bring your
> own (free) API keys.
>
> It's the whole desk: day-ahead prices at the market's real 15-minute resolution,
> residual load & Dunkelflaute, generation mix, cross-border flows, reservoir
> levels vs seasonal norm, and a revision-aware plant-outage board. Everything is
> "vs this zone's own history", every panel shows its data age, a stalled feed
> says STALE.
>
> AGPL-3.0, no accounts, no telemetry phoning home, public read API with CSV/Parquet
> + a Python client (`pip install obsyd`, pandas DataFrames).
> Hosted version to try before you deploy: https://obsyd.dev — code:
> https://github.com/jo20ow/Obsyd. Setup notes in the README; feedback on the
> self-host path very welcome.

---

## r/dataisbeautiful (Do)

**Bild generieren (am Post-Tag!):**

```
cd docs/launch && node render-price-heatmap.mjs            # gestern (kompletter Tag)
node render-price-heatmap.mjs 2026-07-XX                   # oder: bester Tag der letzten 2–3
```

Den visuell stärksten KOMPLETTEN Tag nehmen (Solar-Tal + Negativpreis-Cluster +
Spread; ein Hochpreistag wie der 17.07. ist flau, ein Sonnentag wie der 18.07. mit
blauen Negativ-Zellen ist ideal). PNG entsteht daneben; ohne playwright-core das
HTML im Browser öffnen und bei 2× screenshotten. Referenz-Sample liegt daneben.

**Titel (Muster [OC]-Regeln: Datenquelle + Tool im Kommentar, Bild statisch):**

> [OC] One day of day-ahead electricity prices across all 37 European bidding
> zones — from €7/MWh in northern Sweden to €170 in Sicily

**Erster Kommentar (Pflicht-Kommentar mit Quelle/Tool):**

> Data: ENTSO-E Transparency Platform (day-ahead auction results, hourly, UTC).
> Rendered with a small HTML/Node script; the interactive desk behind it is
> https://obsyd.dev — free & open source (AGPL), no accounts.
> Zones are sorted by daily mean. The pale valley around midday is solar pushing
> prices down; blue cells are hours where the price went below zero. The
> top-to-bottom gap is Europe's grid bottleneck story: Italy imports expensive
> evening power while northern Sweden sits on cheap hydro it can't export south.

---

## r/Energiewirtschaft (Fr / Woche 2, deutsch)

**Titel:**

> Kostenloses Open-Source-Dashboard für den europäischen Strommarkt — 37 Gebotszonen,
> Day-Ahead in 15-min-Auflösung, Kraftwerksausfälle live (ENTSO-E)

**Text:**

> Seit Oktober 2025 handelt der Day-Ahead in 15-Minuten-Produkten, die meisten
> freien Seiten zeigen weiter Stundenmittel. Ich habe deshalb OBSYD gebaut — einen
> kostenlosen „gridstatus für Europa": alle 37 Gebotszonen, Preise in echter
> Auktionsauflösung, Residuallast & Dunkelflaute (kalibriert gegen die eigene
> Zonen-Historie statt fester Schwellen), Erzeugungsmix, Cross-Border-Flüsse,
> Speicherfüllstände gegen die Saisonnorm und ein revisionssicheres Ausfall-Board
> aus dem ENTSO-E-Unavailability-Feed (die meisten Roh-Meldungen sind withdrawn —
> wer sie zählt, erfindet Gigawatts).
>
> Zwei Regeln überall: keine nackte Zahl (alles „vs. eigene Historie"), und
> ehrliches Datenalter — ein hängender Feed sagt STALE. Keine Prognosen, kein
> Edge-Claim: deskriptiv, prüfbar, AGPL.
>
> https://obsyd.dev — Code: https://github.com/jo20ow/Obsyd. Feedback von Leuten
> aus der Branche wäre Gold, gerade zu dem, was für den Desk-Alltag noch fehlt.
