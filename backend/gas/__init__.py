"""EU gas balance model (Phase 1: data foundation).

Daily EU27 supply-demand balance whose product is a residual signal
(implied ΔStorage from flows vs actual ΔStorage from AGSI). Phase 1 ships
the data layer: ENTSOG flow ingestion, AGSI/ALSI ingestion, a strict
unit-conversion layer, and validation of the supply sum against Bruegel.

Internal canonical unit everywhere: GWh/day.
See the plan and the 8-table schema in backend/models/gas.py.
"""
