# Live-Charts: eine Zone groß, Vergleich optional

**Datum:** 2026-07-14
**Betroffen:** `frontend/src/components/LiveCharts.jsx` (EUROPE-Tab), `MiniMixCard.jsx`, neu `ZoneCompareChart.jsx`

## Problem

`LiveCharts` rendert für jede Sektion (Prices / Fuel Mix / Load / Residual) ein Karten-Grid über
sechs hart kodierte Kernzonen (`CORE = DE_LU, FR, NL, BE, ES, AT`). Ergebnis: sechs kleine Charts
nebeneinander, jeder zu klein zum Lesen, keiner davon der, den der Nutzer gerade sehen will — und
inhaltlich eine schlechtere Dopplung der Alle-Zonen-Sicht (Matrix + Karte), die auf demselben Tab
darüber steht.

## Ziel

Default: **eine Zone, groß** — die global gewählte Zone (default DE-LU). Andere Zonen kommen nur
dazu, wenn der Nutzer sie aktiv zum Vergleich zuschaltet (max. 3).

## Design

### 1. Zustand & Persistenz

- **Primärzone = globale ViewState-Zone** (`useViewState().zone`, default `DE_LU`, gesteuert vom
  Shell-Selector). Kein zweiter Zonen-Begriff.
- **Vergleichsliste**: neuer lokaler State in `LiveCharts`, Array von Zonen-Keys, **Default leer**,
  Maximum 3.
- **Persistenz**: URL-Query `?cmp=FR,NL` via `history.replaceState` — dasselbe Muster wie
  `SeriesExplorer` (`s`/`vs`/`res`). Initialwert wird beim Mount aus der URL gelesen. Kein
  localStorage (Zone + Range reisen bereits über die ViewState-Spine).
- **Invariante**: die Primärzone darf nie in der Vergleichsliste stehen. Wechselt der Nutzer die
  globale Zone auf eine, die verglichen wird, wird sie aus der Vergleichsliste entfernt.
- Die Vergleichsauswahl gilt **für alle vier Sektionen gemeinsam**, nicht pro Tab.

### 2. Linien-Sektionen (Prices, Load, Residual) → `ZoneCompareChart.jsx`

Ein großer Chart statt N kleiner Karten. Overlay: Primärzone + je Vergleichszone eine weitere Linie.

- **Props**: `title`, `series`, `zones` (Array: Primär zuerst, dann Vergleich), `unit`, `scale`,
  `color` (Primärfarbe der Sektion).
- **Fetching**: vier **feste** `useFetchWithError`-Slots (Hooks dürfen nicht in einer Schleife
  laufen). Slot 0 = Primärzone; Slots 1–3 = Vergleichszone oder — wenn ungenutzt — dieselbe URL wie
  Slot 0, die dann aus dem SWR-Cache bedient wird (kein Extra-Request). Exakt der Trick aus
  `SeriesExplorer` (`cmpZoneEff = compareZone || zone`).
- **Merge**: Reihen aller Slots auf `date` gemergt zu `{ t, [zoneKey]: value * scale }`.
- **Chart**: Recharts `LineChart`, Höhe ~280px (statt 120px), `connectNulls`, gleiche Achsen-/
  Grid-/Tooltip-Styles wie `MiniSeriesCard` (`CHART_TOOLTIP_STYLE`, `fmtDate`).
- **Farben**: Primär = Sektionsfarbe (Prices cyan `#22d3ee`, Load violett `#a78bfa`, Residual amber
  `#f59e0b`). Vergleichszonen = feste Palette in fixer Reihenfolge, aus dem vorhandenen Vokabular
  (`#f472b6`, `#4ade80`, `#818cf8`) — die Farbe hängt an der Position in der Vergleichsliste, nicht
  an der Zone.
- **Legende**: pro Zone Farb-Swatch, Label, letzter Wert, CSV-↓ (die `/api/v1/series?...&format=csv`-
  URL existiert je Zone ohnehin).
- **Leere Zone**: liefert eine Zone keine Zeilen, wird sie in der Legende explizit als „no data"
  markiert, statt still als Leerlinie zu verschwinden (Prinzip: Panels verschwinden nie kommentarlos).

`MiniSeriesCard.jsx` hat außer `LiveCharts` keinen Aufrufer (verifiziert per grep) und wird gelöscht.

### 3. Fuel Mix → getrennte Karten

Ein gestapelter Mix lässt sich nicht überlagern, deshalb bleibt der Mix bei Karten:

- **Ohne Vergleich**: eine `MiniMixCard` über die volle Breite, höherer Chart.
- **Mit Vergleich**: Grid aus 1+n Karten (2 Spalten ab `md`), Primärzone zuerst.
- `MiniMixCard` bekommt dafür nur ein optionales `height`-Prop (Default = heutiger Wert), sonst
  unverändert.

### 4. Picker-UI

Eine Zeile unter den Sektions-Tabs:

```
Compare:  [FR] [NL] [BE] [ES] [AT]   [+ more… ▾]
```

- Chips = bisherige Kernzonen ohne die aktuelle Primärzone, Klick togglet.
- Dropdown „+ more…" listet alle enabled Zonen aus `useZones()` (37), die weder Primär- noch bereits
  Vergleichszone sind.
- Bei 3 gewählten Vergleichszonen sind weitere Chips/Optionen deaktiviert.
- Gewählte Zonen erscheinen aktiv markiert und lassen sich per Klick (bzw. ×) wieder entfernen.

## Nicht enthalten (bewusst)

- Kein Spread-/Δ-Toggle — den hat `SeriesExplorer` bereits; hier wäre er nur bei genau einer
  Vergleichszone sinnvoll.
- Keine neuen Backend-Endpoints: alles läuft über das bestehende `/api/v1/series` bzw. `/api/v1/genmix`.
- Keine Änderung an der globalen Zonen-/Range-Spine.

## Verifikation

Das Frontend hat keine Testsuite (kein Vitest, nur ESLint + Vite-Build). Also:

1. `npm run lint` und `npm run build` grün.
2. App lokal starten und durchklicken:
   - Prices / Load / Residual jeweils ohne Vergleich (eine große Linie) und mit 1–3 Vergleichszonen.
   - Fuel Mix ohne Vergleich (eine breite Karte) und mit Vergleich (1+n Karten).
   - Zonenwechsel im Shell-Selector auf eine Zone, die gerade verglichen wird → sie verschwindet aus
     der Vergleichsliste.
   - Reload mit `?cmp=FR,NL` → Auswahl wird wiederhergestellt.
   - Eine Zone ohne Daten für die Serie → „no data" in der Legende, kein leerer Chart.
3. Netzwerk-Tab: ohne Vergleichszonen genau ein `/api/v1/series`-Request pro Sektion (kein
   Vierfach-Fetch durch die leeren Slots).
