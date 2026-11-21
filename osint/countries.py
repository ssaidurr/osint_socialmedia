"""ISO 3166-1 codes and the names people read.

Generated once from pycountry, then kept here: a chart axis needs a name, and a runtime
dependency for a lookup table that never changes would be silly.
"""

import re
from functools import lru_cache

NAMES: dict[str, str] = {
    "AD": "Andorra",
    "AE": "United Arab Emirates",
    "AF": "Afghanistan",
    "AG": "Antigua and Barbuda",
    "AI": "Anguilla",
    "AL": "Albania",
    "AM": "Armenia",
    "AO": "Angola",
    "AQ": "Antarctica",
    "AR": "Argentina",
    "AS": "American Samoa",
    "AT": "Austria",
    "AU": "Australia",
    "AW": "Aruba",
    "AX": "Åland Islands",
    "AZ": "Azerbaijan",
    "BA": "Bosnia and Herzegovina",
    "BB": "Barbados",
    "BD": "Bangladesh",
    "BE": "Belgium",
    "BF": "Burkina Faso",
    "BG": "Bulgaria",
    "BH": "Bahrain",
    "BI": "Burundi",
    "BJ": "Benin",
    "BL": "Saint Barthélemy",
    "BM": "Bermuda",
    "BN": "Brunei",
    "BO": "Bolivia",
    "BQ": "Bonaire, Sint Eustatius and Saba",
    "BR": "Brazil",
    "BS": "Bahamas",
    "BT": "Bhutan",
    "BV": "Bouvet Island",
    "BW": "Botswana",
    "BY": "Belarus",
    "BZ": "Belize",
    "CA": "Canada",
    "CC": "Cocos (Keeling) Islands",
    "CD": "DR Congo",
    "CF": "Central African Republic",
    "CG": "Congo",
    "CH": "Switzerland",
    "CI": "Côte d'Ivoire",
    "CK": "Cook Islands",
    "CL": "Chile",
    "CM": "Cameroon",
    "CN": "China",
    "CO": "Colombia",
    "CR": "Costa Rica",
    "CU": "Cuba",
    "CV": "Cabo Verde",
    "CW": "Curaçao",
    "CX": "Christmas Island",
    "CY": "Cyprus",
    "CZ": "Czechia",
    "DE": "Germany",
    "DJ": "Djibouti",
    "DK": "Denmark",
    "DM": "Dominica",
    "DO": "Dominican Republic",
    "DZ": "Algeria",
    "EC": "Ecuador",
    "EE": "Estonia",
    "EG": "Egypt",
    "EH": "Western Sahara",
    "ER": "Eritrea",
    "ES": "Spain",
    "ET": "Ethiopia",
    "FI": "Finland",
    "FJ": "Fiji",
    "FK": "Falkland Islands (Malvinas)",
    "FM": "Micronesia",
    "FO": "Faroe Islands",
    "FR": "France",
    "GA": "Gabon",
    "GB": "United Kingdom",
    "GD": "Grenada",
    "GE": "Georgia",
    "GF": "French Guiana",
    "GG": "Guernsey",
    "GH": "Ghana",
    "GI": "Gibraltar",
    "GL": "Greenland",
    "GM": "Gambia",
    "GN": "Guinea",
    "GP": "Guadeloupe",
    "GQ": "Equatorial Guinea",
    "GR": "Greece",
    "GS": "South Georgia",
    "GT": "Guatemala",
    "GU": "Guam",
    "GW": "Guinea-Bissau",
    "GY": "Guyana",
    "HK": "Hong Kong",
    "HM": "Heard Island and McDonald Islands",
    "HN": "Honduras",
    "HR": "Croatia",
    "HT": "Haiti",
    "HU": "Hungary",
    "ID": "Indonesia",
    "IE": "Ireland",
    "IL": "Israel",
    "IM": "Isle of Man",
    "IN": "India",
    "IO": "British Indian Ocean Territory",
    "IQ": "Iraq",
    "IR": "Iran",
    "IS": "Iceland",
    "IT": "Italy",
    "JE": "Jersey",
    "JM": "Jamaica",
    "JO": "Jordan",
    "JP": "Japan",
    "KE": "Kenya",
    "KG": "Kyrgyzstan",
    "KH": "Cambodia",
    "KI": "Kiribati",
    "KM": "Comoros",
    "KN": "Saint Kitts and Nevis",
    "KP": "North Korea",
    "KR": "South Korea",
    "KW": "Kuwait",
    "KY": "Cayman Islands",
    "KZ": "Kazakhstan",
    "LA": "Laos",
    "LB": "Lebanon",
    "LC": "Saint Lucia",
    "LI": "Liechtenstein",
    "LK": "Sri Lanka",
    "LR": "Liberia",
    "LS": "Lesotho",
    "LT": "Lithuania",
    "LU": "Luxembourg",
    "LV": "Latvia",
    "LY": "Libya",
    "MA": "Morocco",
    "MC": "Monaco",
    "MD": "Moldova",
    "ME": "Montenegro",
    "MF": "Saint Martin (French part)",
    "MG": "Madagascar",
    "MH": "Marshall Islands",
    "MK": "North Macedonia",
    "ML": "Mali",
    "MM": "Myanmar",
    "MN": "Mongolia",
    "MO": "Macau",
    "MP": "Northern Mariana Islands",
    "MQ": "Martinique",
    "MR": "Mauritania",
    "MS": "Montserrat",
    "MT": "Malta",
    "MU": "Mauritius",
    "MV": "Maldives",
    "MW": "Malawi",
    "MX": "Mexico",
    "MY": "Malaysia",
    "MZ": "Mozambique",
    "NA": "Namibia",
    "NC": "New Caledonia",
    "NE": "Niger",
    "NF": "Norfolk Island",
    "NG": "Nigeria",
    "NI": "Nicaragua",
    "NL": "Netherlands",
    "NO": "Norway",
    "NP": "Nepal",
    "NR": "Nauru",
    "NU": "Niue",
    "NZ": "New Zealand",
    "OM": "Oman",
    "PA": "Panama",
    "PE": "Peru",
    "PF": "French Polynesia",
    "PG": "Papua New Guinea",
    "PH": "Philippines",
    "PK": "Pakistan",
    "PL": "Poland",
    "PM": "Saint Pierre and Miquelon",
    "PN": "Pitcairn",
    "PR": "Puerto Rico",
    "PS": "Palestine",
    "PT": "Portugal",
    "PW": "Palau",
    "PY": "Paraguay",
    "QA": "Qatar",
    "RE": "Réunion",
    "RO": "Romania",
    "RS": "Serbia",
    "RU": "Russia",
    "RW": "Rwanda",
    "SA": "Saudi Arabia",
    "SB": "Solomon Islands",
    "SC": "Seychelles",
    "SD": "Sudan",
    "SE": "Sweden",
    "SG": "Singapore",
    "SH": "Saint Helena",
    "SI": "Slovenia",
    "SJ": "Svalbard and Jan Mayen",
    "SK": "Slovakia",
    "SL": "Sierra Leone",
    "SM": "San Marino",
    "SN": "Senegal",
    "SO": "Somalia",
    "SR": "Suriname",
    "SS": "South Sudan",
    "ST": "Sao Tome and Principe",
    "SV": "El Salvador",
    "SX": "Sint Maarten (Dutch part)",
    "SY": "Syria",
    "SZ": "Eswatini",
    "TC": "Turks and Caicos Islands",
    "TD": "Chad",
    "TF": "French Southern Territories",
    "TG": "Togo",
    "TH": "Thailand",
    "TJ": "Tajikistan",
    "TK": "Tokelau",
    "TL": "Timor-Leste",
    "TM": "Turkmenistan",
    "TN": "Tunisia",
    "TO": "Tonga",
    "TR": "Turkey",
    "TT": "Trinidad and Tobago",
    "TV": "Tuvalu",
    "TW": "Taiwan",
    "TZ": "Tanzania",
    "UA": "Ukraine",
    "UG": "Uganda",
    "UM": "US Minor Outlying Islands",
    "US": "United States",
    "UY": "Uruguay",
    "UZ": "Uzbekistan",
    "VA": "Holy See (Vatican City State)",
    "VC": "Saint Vincent and the Grenadines",
    "VE": "Venezuela",
    "VG": "Virgin Islands, British",
    "VI": "Virgin Islands, U.S.",
    "VN": "Vietnam",
    "VU": "Vanuatu",
    "WF": "Wallis and Futuna",
    "WS": "Samoa",
    "YE": "Yemen",
    "YT": "Mayotte",
    "ZA": "South Africa",
    "ZM": "Zambia",
    "ZW": "Zimbabwe",
}


def label(code) -> str:
    """'BD — Bangladesh'. Anything else — a blank, a NaN from a dataframe column, a code we do
    not know — comes back readable rather than crashing the page."""
    if not isinstance(code, str) or not code.strip():
        return "Unknown"
    code = code.strip().upper()
    name = NAMES.get(code)
    return f"{code} — {name}" if name else code


# The long and diplomatic names of countries, as they turn up in official headlines. WHO writes
# "the Russian Federation" and "Kingdom of Saudi Arabia"; UNHCR and the UN agencies write much
# the same way. Only the forms that differ from NAMES need to be here.
ALIASES: dict[str, str] = {
    "russian federation": "RU",
    "democratic republic of the congo": "CD",
    "democratic republic of congo": "CD",
    "republic of the congo": "CG",
    "united republic of tanzania": "TZ",
    "kingdom of saudi arabia": "SA",
    "islamic republic of iran": "IR",
    "syrian arab republic": "SY",
    "bolivarian republic of venezuela": "VE",
    "plurinational state of bolivia": "BO",
    "lao people's democratic republic": "LA",
    "republic of moldova": "MD",
    "czech republic": "CZ",
    "republic of korea": "KR",
    "democratic people's republic of korea": "KP",
    "viet nam": "VN",
    "united states of america": "US",
    "united kingdom of great britain and northern ireland": "GB",
    "côte d'ivoire": "CI",
    "cote d'ivoire": "CI",
    "ivory coast": "CI",
    "türkiye": "TR",
    "turkey": "TR",
    "myanmar (burma)": "MM",
    "burma": "MM",
    "cabo verde": "CV",
    "eswatini": "SZ",
    "timor-leste": "TL",
}


# ─── The level above a country ────────────────────────────────────────────────
# The UN M49 sub-regions, which is the standard everyone from the World Bank to WHO reports
# against, so a reader who knows "Western Asia" from one of those knows it here too. Written
# as region -> members because that is how it is checked and corrected; REGION_OF inverts it
# once at import.
REGIONS: dict[str, tuple[str, ...]] = {
    "Northern Africa": ("DZ", "EG", "LY", "MA", "SD", "TN", "EH"),
    "Sub-Saharan Africa": (
        "AO", "BF", "BI", "BJ", "BW", "CD", "CF", "CG", "CI", "CM", "CV", "DJ", "ER", "ET",
        "GA", "GH", "GM", "GN", "GQ", "GW", "KE", "KM", "LR", "LS", "MG", "ML", "MR", "MU",
        "MW", "MZ", "NA", "NE", "NG", "RE", "RW", "SC", "SH", "SL", "SN", "SO", "SS", "ST",
        "SZ", "TD", "TG", "TZ", "UG", "YT", "ZA", "ZM", "ZW", "IO"),
    "Northern America": ("BM", "CA", "GL", "PM", "US"),
    "Latin America & Caribbean": (
        "AG", "AI", "AR", "AW", "BB", "BL", "BO", "BQ", "BR", "BS", "BZ", "CL", "CO", "CR",
        "CU", "CW", "DM", "DO", "EC", "FK", "GD", "GF", "GP", "GT", "GY", "HN", "HT", "JM",
        "KN", "KY", "LC", "MF", "MQ", "MS", "MX", "NI", "PA", "PE", "PR", "PY", "SR", "SV",
        "SX", "TC", "TT", "UY", "VC", "VE", "VG", "VI"),
    "Central Asia": ("KG", "KZ", "TJ", "TM", "UZ"),
    "Eastern Asia": ("CN", "HK", "JP", "KP", "KR", "MN", "MO", "TW"),
    "South-Eastern Asia": ("BN", "ID", "KH", "LA", "MM", "MY", "PH", "SG", "TH", "TL", "VN"),
    "Southern Asia": ("AF", "BD", "BT", "IN", "IR", "LK", "MV", "NP", "PK"),
    "Western Asia": (
        "AE", "AM", "AZ", "BH", "CY", "GE", "IL", "IQ", "JO", "KW", "LB", "OM", "PS", "QA",
        "SA", "SY", "TR", "YE"),
    "Eastern Europe": ("BG", "BY", "CZ", "HU", "MD", "PL", "RO", "RU", "SK", "UA"),
    "Northern Europe": (
        "AX", "DK", "EE", "FI", "FO", "GB", "GG", "IE", "IM", "IS", "JE", "LT", "LV", "NO",
        "SE", "SJ"),
    "Southern Europe": (
        "AD", "AL", "BA", "ES", "GI", "GR", "HR", "IT", "ME", "MK", "MT", "PT", "RS", "SI",
        "SM", "VA", "XK"),
    "Western Europe": ("AT", "BE", "CH", "DE", "FR", "LI", "LU", "MC", "NL"),
    "Oceania": (
        "AS", "AU", "CK", "FJ", "FM", "GU", "KI", "MH", "MP", "NC", "NF", "NR", "NU", "NZ",
        "PF", "PG", "PN", "PW", "SB", "TK", "TO", "TV", "VU", "WF", "WS", "CC", "CX", "UM"),
    "Antarctica": ("AQ", "BV", "GS", "HM", "TF"),
}

REGION_OF: dict[str, str] = {code: region for region, codes in REGIONS.items() for code in codes}


def region(code: str | None) -> str:
    """The region a country sits in, or "Elsewhere" for one this table has not placed."""
    return REGION_OF.get((code or "").upper(), "Elsewhere")


@lru_cache(maxsize=1)
def _name_pattern() -> tuple:
    """One regular expression over every country name we know, longest first.

    Longest first matters: "Congo" must not win inside "Democratic Republic of the Congo", and
    "Guinea" must not win inside "Papua New Guinea".
    """
    known = {name.lower(): code for code, name in NAMES.items()} | ALIASES
    ordered = sorted(known, key=len, reverse=True)
    return re.compile(r"\b(" + "|".join(re.escape(n) for n in ordered) + r")\b", re.I), known


def named_in(text: str) -> list[str]:
    """The ISO-2 codes of every country a piece of text names, in the order they appear.

    Headlines from the official feeds say where something happened and nothing more precise, so
    this is how a WHO outbreak notice or a sanctions listing finds its place on the map.
    """
    if not text:
        return []
    pattern, known = _name_pattern()
    out: list[str] = []
    for hit in pattern.finditer(text):
        code = known[hit.group(1).lower()]
        if code not in out:
            out.append(code)
    return out
