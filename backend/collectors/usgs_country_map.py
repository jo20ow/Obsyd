"""USGS country-name → ISO-3166-1 alpha-3 map for the Mineral Commodity Summaries.

USGS uses its own country spellings (e.g. "Burma", "Congo (Kinshasa)") and includes
non-country aggregates ("Other Countries", "World total (rounded)") that must be dropped.
This covers the producing countries that appear in the strategic-mineral set; any name not
in the map is skipped + logged by the collector (so the map can be extended over time).
"""

# Aggregates / non-country rows to exclude outright.
USGS_AGGREGATES = {"Other Countries", "World total (rounded)", "World total", "United States and Canada"}

USGS_NAME_TO_ISO3 = {
    "Argentina": "ARG", "Australia": "AUS", "Belarus": "BLR", "Bolivia": "BOL",
    "Brazil": "BRA", "Burkina Faso": "BFA", "Burma": "MMR", "Canada": "CAN",
    "Chile": "CHL", "China": "CHN", "Colombia": "COL", "Congo (Kinshasa)": "COD",
    "Cuba": "CUB", "Germany": "DEU", "Ghana": "GHA", "Greece": "GRC", "Guinea": "GIN",
    "India": "IND", "Indonesia": "IDN", "Iran": "IRN", "Israel": "ISR", "Jamaica": "JAM",
    "Jordan": "JOR", "Kazakhstan": "KAZ", "Laos": "LAO", "Madagascar": "MDG",
    "Malaysia": "MYS", "Mali": "MLI", "Mauritania": "MRT", "Mexico": "MEX",
    "Namibia": "NAM", "New Caledonia": "NCL", "Nigeria": "NGA", "Papua New Guinea": "PNG",
    "Peru": "PER", "Philippines": "PHL", "Poland": "POL", "Portugal": "PRT", "Russia": "RUS",
    "Saudi Arabia": "SAU", "South Africa": "ZAF", "Spain": "ESP", "Sweden": "SWE",
    "Tanzania": "TZA", "Thailand": "THA", "Turkey": "TUR", "Ukraine": "UKR",
    "United States": "USA", "Uzbekistan": "UZB", "Vietnam": "VNM", "Zambia": "ZMB",
    "Zimbabwe": "ZWE",
    # extra common USGS spellings for future commodity additions:
    "Congo (Brazzaville)": "COG", "Korea, Republic of": "KOR", "Korea, North": "PRK",
    "Bahrain": "BHR", "United Arab Emirates": "ARE", "Egypt": "EGY", "Morocco": "MAR",
    "Norway": "NOR", "Finland": "FIN", "Japan": "JPN", "Botswana": "BWA",
    "Dominican Republic": "DOM", "Eritrea": "ERI", "Sierra Leone": "SLE", "Senegal": "SEN",
    "Togo": "TGO", "Gabon": "GAB", "Mozambique": "MOZ", "Angola": "AGO", "Venezuela": "VEN",
}
