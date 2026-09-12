# Finding 2026-09-13 — SMARD Netzengpassmanagement: Kosten ja, Chart-API nein

**Frage:** SMARD (CC BY 4.0) führt Netzengpassmanagement-Daten — programmatisch
erreichbar?

**Befund:**
- Die öffentliche Chart-API (`/app/chart_data/{filter}/…`) kennt KEINE
  Congestion-Filter (Filterliste in bundesAPI/smard-api geprüft; die Modul-IDs
  16000418/16000419 antworten dort leer).
- Der Weg ist der **Download-Manager**: unauthentifizierter POST an
  `/nip-download-manager/nip/download/market-data` mit `moduleIds` aus der
  öffentlichen Konfiguration `/app/chart_configuration/market_data_configuration.json`
  (Superkategorie 5 „Systemstabilität", Sub 16 „Gesamtkosten"):
  `16000418` Kosten Netzsicherheitsmaßnahmen · `16000419` Kosten Countertrading ·
  (`16004391` Ausgaben Ausgleichsenergie — nicht ingestiert, Imbalance kommt via
  ENTSO-E).
- Antwort: EINE kleine CSV mit der VOLLEN Monats-Historie seit 2022-07,
  deutsches Zahlenformat, Semikolon, „-" für unpublizierte Monate.
  **Publikations-Lag 3–4 Monate** (Stand 2026-09: neuester Monat = 2026-05).
- **MENGEN (GWh) fanden sich in dieser Kategorie NICHT** — nur Kosten. Die
  Redispatch-Mengen liegen bei netztransparenz.de (Lizenz schweigt → NO-GO,
  siehe Finding 2026-07-20) bzw. in BNetzA-Quartalsberichten (PDF). Die
  Kosten-Serie ist der sauber lizenzierte Teil.

**Konsequenz:** `congestion.cost.security` + `congestion.cost.countertrading`
(DE_LU, monatlich) via `backend/power/smard.py`; Attribution
„Bundesnetzagentur | SMARD.de" in /meta. Juli 2022 als Startpunkt ist SMARDs
eigener Boden.
