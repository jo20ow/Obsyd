# Feasibility spike: REMIT/UMM ingestion (Nord Pool, EEX) — 2026-07-20

**Verdict: NO-GO as-of-right. GO only with express written consent from Nord Pool.**
Storage + analysis of UMMs is permitted; public re-display on obsyd.dev is not, absent
written consent. The legally clean substitute for the largest unique slice (transmission/
interconnector unavailability) is ENTSO-E documentType **A78**, under the terms Obsyd
already operates under.

## The hard gate: Nord Pool terms fail

Governing document: **"REMIT UMM Services General Terms"** (Nord Pool AS, valid from
2021-05-15), linked from the UMM platform's own T&C dialog
(<https://www.nordpoolgroup.com/4975a5/globalassets/download-center/remit/remit-umm-services-general-terms-valid-from-15.05.21-.pdf>).

- Clause 11.1: users "may download, store and use" contents "for analysis or research
  purposes", but "may not republish, retransmit, re-distribute or otherwise make the
  contents … available … on any website … without the express written consent of Nord Pool."
- Service Schedule 1C (READ-ONLY SERVICE): "Reading of data on Nord Pool web page **for
  internal use only**."
- API General T&Cs §9.2 lists "re-transmission, re-selling or publication in any form
  whatsoever" as the first example of API misuse.

REMIT II (Reg. (EU) 2024/1106, Art. 4) obliges Nord Pool as an IIP to make inside
information freely publicly *accessible* — it does not grant third parties a licence to
republish the IIP's feed over its contract terms. Commercial aggregators (Montel,
EnAppSys) presumably hold exactly the written consent Clause 11.1 contemplates.

**EEX transparency: confirmed NO** — the disclaimer
(<https://www.eex-transparency.com/disclaimer>) forbids copying/dissemination without
prior written approval; single personal non-commercial copy only.

## Everything else scores well (for the record)

- **API**: `https://ummapi.nordpoolgroup.com` is a documented, unauthenticated, public
  REST/JSON API (OpenAPI at `/swagger/v1/swagger.json`; "Much of the API does not require
  authentication and is public"). `GET /messages` with rich filters, `messageId`+`version`+
  `eventStatus` versioning, max 2000 rows/call (HTTP 413 above), history back to 2013
  (~113k current / ~586k incl. outdated messages), RSS + websocket push channels.
- **Zone mapping**: of all 1,723 messages published 2026-06-20→2026-07-20, **86.3 %** map
  cleanly onto Obsyd's 37-zone registry (DE TSO control areas folded to DE_LU); the gap is
  almost entirely LT/LV/EE + GB. Adding the Baltic zones (config-only) lifts full
  mappability to **98.9 %**. ES/PT/IT/CH are essentially absent from the platform.
- **Message mix**: Production 47 %, Transmission 39 %, MarketInformation 9 %,
  Consumption 5 %. Production UMMs largely duplicate ENTSO-E A77 by construction
  (Nord Pool forwards the same disclosures to the Transparency Platform; est. 80–95 %
  duplication for enabled zones). Unique value: transmission/interconnector outages,
  consumption outages, richer free-text remarks, clean JSON, likely faster publication.

## Paths forward

1. **A78 transmission unavailability via ENTSO-E** (recommended, no new licence needed):
   `backend/power/entsoe_outages.py` already parameterises `doc_type` (A77/A78/A80) and
   `PowerOutage.doc_type` exists — ingesting transmission outages is an incremental change
   to the existing pipeline, covering the biggest unique UMM slice for all 37 zones
   (not just the Nordics-skewed Nord Pool population).
2. **Link out** to `umm.nordpoolgroup.com` from the outage panel (per-message deep links
   need no licence).
3. **Owner option**: request express written consent from Nord Pool
   (support@nordpoolgroup.com) for a free, AGPL, attributed public re-display —
   Clause 11.1 makes consent the explicit unlock, and a non-commercial free site
   re-displaying REMIT-mandated-public data is the strongest possible ask. Until/unless
   granted: no Nord Pool UMM content on obsyd.dev.
