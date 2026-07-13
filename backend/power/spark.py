"""The spark spread on this desk is DIRTY, and it was labelled "CCGT margin".

WHAT WAS WRONG
--------------
The situation hero showed, in green, for Germany:

    Spark spread   +€25   ·   CCGT margin

A gas plant does not keep that. It has to buy carbon. At an EUA price of €78/t — where the EU
ETS traded on 2026-07-10 — a CCGT at this desk's own heat rate emits ~0.40 tCO2 per MWh of
electricity, which costs about €32/MWh. The actual day-ahead margin was NEGATIVE.

Measured across the desk on the same day:

    zone        dirty spark      break-even EUA        margin at ~EUR 78/t
    DE-LU          +25.5             63 EUR/t              negative
    FR             +21.6             53                    negative
    ES             +19.0             47                    negative
    IT-NORD        +50.9            126                    still positive

So the desk was telling a power analyst that gas generation is profitable in Germany, France and
Spain, when it is not. This is the class of error that gets a tab closed: it takes ten seconds to
check and it is wrong in the direction that flatters us.

WHAT THIS MODULE DOES INSTEAD, WITHOUT NEEDING A CARBON PRICE
------------------------------------------------------------
It cannot compute the clean spark: there is no confirmed free, redistributable daily EUA series
(see docs/findings/2026-06-24-eua-coal-data-source.md — the EEX auction reports parse, but their
licence is unverified, and nothing goes into the free core until it is).

But it does not need one to stop lying. Two things fix the claim:

1. Call it what it is: a DIRTY spark spread. That is the industry's own word for the spread
   before carbon, and it is not a hedge — it is the correct name.

2. Publish the BREAK-EVEN CARBON PRICE: `dirty_spark / co2_intensity`. The EUA price at which
   this margin reaches zero. It is arithmetic on our own numbers plus a published technical
   constant — no carbon price, no licence, no model — and it is more useful than the clean spread
   would be, because it says exactly how much carbon this zone's gas fleet can absorb before it
   is under water.

THE GAS LEG, HONESTLY
---------------------
TTF via yfinance is Yahoo's copy of the ICE Endex front-month — licensed exchange data, which
this project does not redistribute (CLAUDE.md). The raw close is therefore no longer served in
the API response; only the derived spread is.

That is a MITIGATION, not a cure, and saying so is the point: the heat rate is published and the
power price is public, so anyone can invert the spread back to the gas leg. The real fix is a
free redistributable European gas benchmark, and one has not been found (EEX EGSI is gated). The
provenance is labelled so nobody mistakes the mitigation for a solution.
"""

from __future__ import annotations

#: Emission factor of natural gas, per MWh of THERMAL input. 56.1 kg CO2/GJ (IPCC 2006 default
#: for natural gas, used by the EEA and in EU ETS monitoring) = 0.202 t/MWh_th.
NATURAL_GAS_CO2_T_PER_MWH_TH = 0.202

#: Wording, so the desk never says "margin" about a number that is not one.
DIRTY_SPARK_LABEL = "Dirty spark"


def co2_intensity(heat_rate: float) -> float:
    """tCO2 per MWh of ELECTRICITY for a gas plant at this heat rate.

    heat_rate = MWh_gas per MWh_el = 1 / efficiency. So a 50%-efficient CCGT burns 2 MWh of gas
    per MWh of power and emits 2 × 0.202 = 0.404 tCO2/MWh_el.
    """
    return NATURAL_GAS_CO2_T_PER_MWH_TH * heat_rate


def breakeven_eua(dirty_spark: float, heat_rate: float) -> float | None:
    """The carbon price at which this dirty spread becomes a zero margin. EUR/tCO2.

    Above it, the plant loses money on the day-ahead; below it, it earns. Pure arithmetic on the
    published record and a published emission factor — no EUA price is needed to state it, which
    is exactly why it can be published while the licence question is open.

    None when the spread is already negative: a plant that is under water before carbon has no
    carbon price that saves it, and quoting a negative break-even would invite the reader to read
    it as one.
    """
    intensity = co2_intensity(heat_rate)
    if intensity <= 0 or dirty_spark <= 0:
        return None
    return round(dirty_spark / intensity, 1)


def clean_spark(dirty_spark: float, heat_rate: float, eua_price: float | None) -> float | None:
    """dirty − CO2 cost. None without a carbon price — and there is not one yet.

    Kept here, unused by the API, so that the day the EUA licence clears the arithmetic is
    already written down and already tested, rather than reinvented under time pressure.
    """
    if eua_price is None:
        return None
    return round(dirty_spark - co2_intensity(heat_rate) * eua_price, 4)
