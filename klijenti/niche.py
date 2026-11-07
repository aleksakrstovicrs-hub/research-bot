"""Prevod niše („stomatolozi“) u OpenStreetMap tagove i pomoćne funkcije za tekst."""
import unicodedata

# niša -> (OSM tagovi, druga imena koja korisnik može da otkuca)
NICHES: dict[str, tuple[list[tuple[str, str]], list[str]]] = {
    "stomatolozi": ([("amenity", "dentist")], ["stomatolog", "zubari", "zubar", "stomatoloske ordinacije"]),
    "lekari": ([("amenity", "doctors"), ("amenity", "clinic")], ["ordinacije", "doktori", "klinike", "poliklinike"]),
    "fizioterapeuti": ([("healthcare", "physiotherapist")], ["fizioterapija", "fizijatri"]),
    "veterinari": ([("amenity", "veterinary")], ["veterinarske ambulante", "veterinar"]),
    "apoteke": ([("amenity", "pharmacy")], ["apoteka"]),
    "frizeri": ([("shop", "hairdresser")], ["frizer", "frizerski saloni", "berberi"]),
    "kozmeticari": ([("shop", "beauty")], ["kozmeticki saloni", "saloni lepote", "kozmeticar"]),
    "restorani": ([("amenity", "restaurant")], ["restoran"]),
    "kafici": ([("amenity", "cafe")], ["kafic"]),
    "pekare": ([("shop", "bakery")], ["pekara"]),
    "hoteli": ([("tourism", "hotel")], ["hotel"]),
    "apartmani": ([("tourism", "apartment"), ("tourism", "guest_house")], ["smestaj", "prenociste"]),
    "turisticke agencije": ([("shop", "travel_agency")], ["agencije", "putovanja"]),
    "advokati": ([("office", "lawyer")], ["advokat", "advokatske kancelarije"]),
    "racunovodje": ([("office", "accountant")], ["knjigovodje", "knjigovodstvo", "racunovodstvo"]),
    "nekretnine": ([("office", "estate_agent")], ["agencije za nekretnine", "agenti za nekretnine"]),
    "arhitekte": ([("office", "architect")], ["arhitekta", "arhitektonski biroi"]),
    "auto servisi": ([("shop", "car_repair")], ["autoservisi", "auto servis", "mehanicari"]),
    "teretane": ([("leisure", "fitness_centre")], ["teretana", "fitnes", "fitness"]),
    "optike": ([("shop", "optician")], ["optika", "opticari"]),
    "cvecare": ([("shop", "florist")], ["cvecara"]),
    "skole jezika": ([("amenity", "language_school")], ["skola jezika", "kursevi jezika"]),
    "vrtici": ([("amenity", "kindergarten")], ["vrtic", "privatni vrtici"]),
    "fotografi": ([("craft", "photographer"), ("shop", "photo")], ["fotograf", "foto studiji"]),
    "stolari": ([("craft", "carpenter")], ["stolar"]),
    "elektricari": ([("craft", "electrician")], ["elektricar"]),
    "vodoinstalateri": ([("craft", "plumber")], ["vodoinstalater"]),
    "it firme": ([("office", "it"), ("office", "telecommunication")],
                 ["it kompanije", "it", "softverske firme", "softverske kompanije", "programeri"]),
    "marketinske agencije": ([("office", "advertising_agency")], ["marketing agencije", "reklamne agencije"]),
    "osiguranja": ([("office", "insurance")], ["osiguravajuce kuce", "osiguranje"]),
    "stamparije": ([("shop", "copyshop"), ("craft", "printer")], ["stamparija", "fotokopirnice"]),
    "gradjevinske firme": ([("office", "construction_company"), ("craft", "builder"), ("office", "construction")],
                           ["gradjevinske kompanije", "gradjevina", "gradjevinari", "izvodjaci radova",
                            "izvodjaci gradjevinskih radova", "gradjevinske firme i izvodjaci"]),
    "skole voznje": ([("amenity", "driving_school")], ["auto skole", "auto skola", "skola voznje"]),
}

# Dodatna pretraga po IMENU firme, za delatnosti koje su na mapi slabo označene.
# Traži se samo među objektima koji su firme (office/craft/shop), latinicom i ćirilicom.
NAME_HINTS: dict[str, str] = {
    "gradjevinske firme": ("građevin|gradjevin|gradnj|izgradnj|građenj|konstrukt|construct|inženjering|"
                           "inzenjering|engineering|beton|fasad|krovopokriv|hidroizolac|"
                           "грађевин|градњ|изградњ|конструкт|инжењеринг|бетон|фасад"),
    "it firme": "software|softver|digital|tech|tehnolog|informati|it solutions|софтвер|информати|технолог",
    "marketinske agencije": "marketing|advertis|reklam|media|creative|kreativ|маркетинг|реклам",
}


_CYR = {
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Ђ": "Đ", "Е": "E", "Ж": "Ž", "З": "Z",
    "И": "I", "Ј": "J", "К": "K", "Л": "L", "Љ": "Lj", "М": "M", "Н": "N", "Њ": "Nj", "О": "O",
    "П": "P", "Р": "R", "С": "S", "Т": "T", "Ћ": "Ć", "У": "U", "Ф": "F", "Х": "H", "Ц": "C",
    "Ч": "Č", "Џ": "Dž", "Ш": "Š",
}
_CYR.update({k.lower(): v.lower() for k, v in _CYR.items()})


def to_latin(text: str | None) -> str | None:
    """Ćirilica -> latinica (srpska pravila). Latinični tekst ostaje isti."""
    if text is None:
        return None
    return "".join(_CYR.get(ch, ch) for ch in text)


def simplify(text: str) -> str:
    """Za poređenje: latinica, mala slova, bez kvačica (š->s, đ->dj)."""
    text = to_latin(text).lower().strip().replace("đ", "dj")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.split())


def resolve_niche(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Vraća (ime niše, OSM tagovi). Prihvata i sirov tag, npr. 'shop=bicycle'."""
    if "=" in text:
        key, value = (part.strip() for part in text.split("=", 1))
        return text.strip(), [(key, value)]
    wanted = simplify(text)
    for name, (tags, aliases) in NICHES.items():
        if wanted == simplify(name) or wanted in {simplify(a) for a in aliases}:
            return name, tags
    known = ", ".join(sorted(NICHES))
    raise ValueError(
        f"Ne znam nišu '{text}'. Poznate niše: {known}. "
        "Možeš da zadaš i OSM tag direktno, npr. 'shop=bicycle'."
    )
