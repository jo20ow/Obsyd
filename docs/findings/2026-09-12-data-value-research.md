# Finding 2026-09-12 — "Macht die Daten wertvoller": Recherche-Synthese

Drei parallele Recherchen (freie Quellen + Lizenz-Prüfung; Peer-Scan + CO₂-Methodik;
Repo-Inventur). Leitfrage der neuen Ausrichtung: Obsyd = offener Datensockel für den
europäischen Strommarkt — was macht die Daten wertvoller, zugänglicher, glaubwürdiger?
Hartes Kriterium für alles: frei + programmatisch + REDISTRIBUTIERBAR.

## Tier 1 — Abgeleitete Serien aus Bestandsdaten (kein neuer Upstream, keine Lizenzfrage)

### 1. CO₂-Intensität je Zone, stündlich — GRÖSSTER EINZELHEBEL
- Formel: Σ gen.<psr> × Emissionsfaktor / Σ gen — de-facto-Standard (Electricity Maps,
  Ember, lowcarbonpower rechnen genau so). **Niemand bietet das heute als offene
  stündliche zonale API** — Electricity Maps verkauft es; Obsyd wäre First Mover im
  Open-Tier. Neue Zielgruppen: carbon-aware computing, Journalismus, Lehre.
- Faktoren: IPCC AR5 WGIII Annex III (Schlömer 2014) Lifecycle-Mediane (frei, maximal
  zitierbar) + Electricity-Maps-Faktortabelle aus electricitymaps-contrib — **AGPL-3.0,
  dieselbe Lizenz wie Obsyd**, direkt nutzbar mit Attribution. Deren Konventionen
  übernehmen: unknown=700/575 (lifecycle/direct), Storage-Discharge = Grid-Average.
- Zwei Serien: lifecycle + direct; v1 produktionsbasiert (keine Flüsse nötig).
  v2 später: flow-traced Consumption-Intensität (Tranberg et al. 2019,
  arXiv:1812.06679) — Flows vorhanden, keine gepflegte offene Referenzimplementierung
  existiert → wäre selbst ein zitierbares Asset.
- Glaubwürdigkeit (Posture B): Jahres-Abgleich gegen EEA-Länderindikator publizieren
  („unsere Stunden-Mittel vs. offizielle Jahreszahl"); Präzisions-Claim „estimated,
  technology-average, ±10–20 % vs. Jahresstatistik"; A75-Caveats offen deklarieren
  (unknown-Kategorien, DE ≥100-MW-Erfassung, CHP-Allokation). Fehlerband-Referenz:
  Unnewehr et al. 2021 (arXiv:2110.07999; EUTL-kalibrierte Faktoren = Upgrade-Pfad,
  unit_generation existiert schon).
- Andocken: Pattern C (Serie in power_hourly, z. B. co2.lifecycle/co2.direct) →
  /api/v1 + CSV/Parquet + Katalog GRATIS; Nightly-Recompute-Doktrin nach records.py;
  wie residual.* in REVISION_EXCLUDED_PREFIXES aufnehmen. Präzedenz für Faktor-als-
  Konstante: spark.py (NATURAL_GAS_CO2_T_PER_MWH_TH, IPCC 2006).

### 2. Negativstunden-Statistik als First-Class-Serie — QUICK WIN
Monatliche Counts/Tiefe/Streaks je Zone. Detektion existiert (products.py zählt
negative_hours; PowerPriceDaily). Meistzitierte EU-Strommetrik (Presse, eigene
220k-Views-Story), niemand served sie als API über 37 Zonen — Energy-Charts hat sie
nur als Website-Chart, nicht als API.

### 3. Capture Rates persistieren — QUICK WIN
backend/power/capture.py existiert compute-on-read. Promotion zu gespeicherter
Monats-Serie + CSV. PPA-/Kannibalisierungs-Frage = DIE Renewables-Finanzfrage; frei
gibt es das nur für DE (netztransparenz Marktwerte), Rest ist Pexapark-Paywall.

### 4. Weitere Kandidaten (gebündelt, eine Rechenfamilie)
Day-ahead-vs-Imbalance-Spread (EU-Analog zu gridstatus' DART-Datasets; beide Serien
in power_hourly) · Preis-/Residuallast-Duration-Curves + Base/Peak-Stats ·
Spread-/Konvergenz-Index über gekoppelte Zonen (nur ACER publiziert das, jährlich
als PDF). Record-Tracker existiert (/api/power/records) — gridstatus zeigt, dass
das eine beworbene Seite wert ist.

## Tier 2 — Neue Quellen mit sauberer Lizenz

### 5. IDA-Intraday-Auktionspreise via ENTSO-E — ÜBERRASCHUNGSFUND, fast gratis
Seit 2024-06-13 EU-weit; Clearing-Preise IDA1–3 stehen AUF der Transparency Platform
(entsoe-py: query_intraday_prices). Gleiche API, gleicher Token, gleiche Rechtslage
wie A44. Füllt die dokumentierte „kein Intraday"-Lücke (README) mit dem einzigen
redistributierbaren Intraday-Preis der EU. Erst Coverage-Probe je Zone
(probe_entsoe.py-Muster). Continuous-Intraday bleibt bestätigt NO-GO (EPEX-Lizenz).

### 6. GB-Zone via Elexon + NESO — GRÖSSTER ABSOLUTER WERT, größter Aufwand
- Elexon Insights API: komplett offen, KEIN Key, Lizenz explizit „copy, publish,
  distribute … including commercially", Attribution „Contains BMRS data © Elexon
  Limited copyright and database right [year]". Preise (MID), Imbalance, Fuel-Mix
  (5-min), Demand, per-BMU, ~10 Jahre Historie.
- NESO: Open Licence (OGL-basiert, je Dataset prüfen) + Carbon Intensity API
  (CC BY 4.0, Historie 2018, sogar 96h-Forecast) + Demand-Historie ab 2009.
- Repo-Befund: GB existiert bereits als flow.GB-Gegenpartei (energy_charts_flows);
  Zonen-Registry braucht nullable-eic (Präzedenz: ec_country: None bei 12 Zonen);
  Per-Source-Zonenlisten sind der etablierte Escape-Hatch (HYDRO_ZONES etc.).

### 7. SMARD Netzengpassmanagement (DE) — kleine, einzigartige Vertikale
CC BY 4.0 (explizit „shared, redistributed"), Redispatch-/Countertrading-MENGEN UND
KOSTEN seit 07/2022, monatlich. Congestion-Costs zeigt heute niemand im Obsyd-Umfeld.
Caveat: liegt im Download-Center (CSV/XLSX), evtl. nicht in der JSON-Chart-API
(bundesAPI/smard-api dokumentiert die Chart-Route). War als unverified lead schon in
docs/findings/2026-07-20-regelleistung-capacity-prices.md notiert.

## Tier 3 — Briefe statt Code (kosten nichts, entsperren viel)

- **JAO (Flow-Based Core/Nordic):** wertvollster Einzeldatensatz für die Core-NTC-
  Lücke (min/max Net Positions, MaxBEX, Shadow Prices; Core DA ab 2022-06). ABER
  T&C: „cannot be disseminated … unless expressly authorized in advance by JAO in
  writing" → NO-GO ohne schriftliche Erlaubnis. Aktion: EINE Mail an contact@jao.eu
  (frei, AGPL, Attribution — plausibel bewilligbar; CACM-Transparenzpflicht).
  Parallel prüfen: ENTSO-E-TP-Item 11.1.B „DA Flow Based Allocations" — wäre
  lizenz-sauber über den bestehenden Kanal, Befüllung unklar.
- **Ember Carbon Price Viewer:** behauptet CC BY 4.0 für alles — würde EUA/Clean-Spark
  entsperren (spark.py::clean_spark liegt fertig getestet). Quelle der Preise (ICE via
  Quandl) + stabiler Download unbestätigt → eine Mail an Ember.
- **NO-GO bestätigt/bleibend:** netztransparenz.de (Lizenz schweigt; für reBAP/ID-AEP
  gäbe es keinen Ersatz → ggf. ÜNB fragen), ESIOS (Lizenz unauffindbar, REE ist
  börsennotiert), EPEX/NordPool/EEX continuous, regelleistung.net, UMMs.
- **MaStR (DL-DE-BY-2.0, sauber):** „wenn Zeit ist" — 3,1-GB-XML-ETL, DE-only,
  füllt die dokumentierte A71-Lücke (52 GW erfasst vs. 295 GW installiert).

## Empfohlene Reihenfolge (Wert pro Aufwand × Datensockel-Identität)

1. **CO₂-Intensität** (der Hebel: neue Zielgruppe + nächste Content-Story in einem)
2. **Negativstunden- + Capture-Serie** (Tage, nicht Wochen; bedient bewiesene Nachfrage)
3. **IDA-Coverage-Probe → Ingest** (füllt „kein Intraday" quasi gratis)
4. **Zwei Mails: JAO + Ember** (parallel, kosten einen Abend)
5. **SMARD-Redispatch** (kleine eigenständige Scheibe)
6. **GB via Elexon/NESO** (eigener Meilenstein, danach ist Obsyd nicht mehr „EU-27",
   sondern „Europa")

Backfill-Nebenfund der Repo-Inventur: BACKFILL_START=2015 ist mechanisch sicher
re-runbar (raw_cache + upsert); wo die echten ENTSO-E-Böden je Doctype liegen, ist
unerforscht — probe_entsoe.py ist das Werkzeug. Tiefere Historie = billiger
Datenwert für Forscher.
