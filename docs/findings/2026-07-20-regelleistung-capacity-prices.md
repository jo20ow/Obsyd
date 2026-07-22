# Feasibility spike: German FCR/aFRR/mFRR capacity prices — 2026-07-20

**Verdict: GO — via ENTSO-E documentType A15 (12.3.F), not regelleistung.net.**
regelleistung.net has no data licence (Impressum reserves reproduction to the four TSOs);
the identical data is live on the ENTSO-E Transparency Platform under the
attribution-based reuse terms Obsyd already operates under for its 77 ENTSO-E series.

## regelleistung.net: no licence, permission would be needed

- Operator: the four German TSOs (50Hertz, Amprion, TenneT, TransnetBW). There is **no
  Nutzungsbedingungen page and no data-licence statement** anywhere on the site (Impressum,
  Datenschutz, SPA footers, robots.txt all checked). The only governing clause (Impressum,
  "Use of this website"): *"Reproduction of the website or parts thereof … requires prior
  written permission from the German TSOs unless reproduction is authorised by law."*
- Systematic daily harvesting + public re-export plausibly falls under the sui-generis
  database right (§§ 87a ff. UrhG). Public scrapers exist and enforcement risk against a
  free attributed tool is low — but "probably tolerated" is not "licensed".
- If its cleaner ready-made aggregates are ever wanted directly: the Datacenter SPA uses an
  **unauthenticated JSON API** at `https://www.regelleistung.net/apps/crds/api/v2`
  (`/tenders?…`, `/tenders/{id}/aggregated-product-results` with min/mean/max + full
  anonymised bid ladder, `/tenders/{id}/local-marginal-prices` for FCR; tenderIds
  `PRL|SRL|MRL_YYYYMMDD_D1`; history to 2018/2019). The documented xlsx download endpoint
  (`…/cpp-publisher/api/v1/download/tenders/files/RESULT_OVERVIEW_CAPACITY_MARKET_…`) is
  xlsx-only, mid-2022→today, empty-200 on missing dates. Ask the TSOs in writing first.

## ENTSO-E path (chosen)

- **A89/A81 are dead on web-api.tp.entsoe.eu** — live probe returns "combination … not
  valid, or the requested data is not allowed to be fetched via this service" (token
  sanity-checked against A44). Any library code path using them (e.g. entsoe-py
  `query_contracted_reserve_prices`) fails against today's API.
- **The live replacement is documentType A15 "Procured Balancing Capacity [GL EB 12.3.F]"**:
  `area_Domain=10Y1001A1001A82H` (DE-LU LFC **block** — individual German control areas
  return nothing), processType **A52=FCR / A51=aFRR / A47=mFRR**. Verified 2026-07-14:
  aFRR 6,458 instances, mFRR 1,802, FCR non-empty. Responses are ZIPs of
  `Balancing_MarketDocument` XMLs with per-bid `businessType B95`, `flowDirection`
  (A01=up/A02=down), `quantity` [MW], `procurement_Price.amount`. Pagination: 100
  documents per request via explicit `offset` (max 4900).
- Aggregate client-side to min/avg/marginal per 4-h product block (daily tenders, six
  blocks 00_04…20_24; FCR symmetric, aFRR/mFRR split pos/neg; FCR pay-as-cleared
  EUR/MW per 4 h, aFRR/mFRR pay-as-bid EUR/MW/h).
- **Fidelity caveat:** FCR per-country settlement prices under cross-border clearing (the
  regelleistung.net local-marginal-prices view) are not exactly reconstructible from DE-LU
  bid data alone; DE settlement price can differ from the cross-border price on
  export-limit congestion days. Acceptable for a German-desk view; note it in the UI.
- Publication timing (D-1, Europe/Berlin): FCR results ~08:30, aFRR ~09:30, mFRR ~11:00.
- Unverified lead: SMARD (Bundesnetzagentur, CC BY 4.0) has a Regelenergie section that
  may carry the same series.
