# Finding 2026-09-13 — Intraday-auction (IDA) prices on the Transparency Platform

**Question:** Since 2024-06-13 the SIDC intraday auctions (IDA1-3) clear 15-minute
products EU-wide, and their prices can be published on the ENTSO-E TP (A44 +
`contract_MarketAgreement.type=A07`, auctions distinguished by
`classificationSequence_AttributeInstanceComponent.position` 1..3). Is this the
free, redistributable intraday price for every zone?

**Answer: no — submission is voluntary and almost nobody submits.**

Probe (2026-09-12, delivery day 2026-09-10, 20 zones): DE_LU, FR, NL, BE, AT, PT,
PL, CZ, IT_NORD, SE3, FI, DK1, CH, IE_SEM, GR, HU, RO, NO2, SK all answer
"No matching data found". **Only ES publishes** — 5 TimeSeries, PT15M, sequences
1/2/3; IDA1/IDA2 span the full CET delivery day, IDA3 the afternoon from 10:00 UTC.

**Depth probe (ES):** 2024-10, 2025-01/04/06/09 all empty; data begins 2026-01.
Publication floor recorded as `IDA_HISTORY_FLOOR` in `backend/power/entsoe_ida.py`.

**Consequence:** `price.ida1/2/3.qh` ship for the publishing zones only
(`IDA_ZONES`, currently ES). Continuous intraday (EPEX/Nord Pool) stays licensed —
NO-GO unchanged. Re-probe before extending the list:

    A44 + contract_MarketAgreement.type=A07 per zone, one settled delivery day;
    a zone joins IDA_ZONES when it answers with TimeSeries instead of an
    Acknowledgement.
