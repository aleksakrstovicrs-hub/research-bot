"""Šabloni mejlova po kategoriji: ugao, ponuda i fiksni delovi (potpis, odjava)."""
from . import config

OPT_OUT = "Ako vam ovo nije od interesa, samo odgovorite „ne“ i neću vam više pisati."

# kategorija -> (ugao za AI, šta nudimo)
ANGLES: dict[str, tuple[str, str]] = {
    "sajt_ne_radi": (
        "Sajt firme trenutno ne radi (greška, istekao domen ili prazna stranica). Ljubazno javi da si to "
        "primetio, jer ljudi koji ih traže na internetu sada vide grešku. Ne okrivljuj ih.",
        "Brzo vraćanje sajta u rad, a po potrebi i novi sajt.",
    ),
    "bez_sajta": (
        "Nisi uspeo da nađeš sajt firme. Postavi to kao PITANJE, ne kao tvrdnju (možda ga imaju, a ti ga nisi "
        "našao). Objasni u jednoj rečenici da ih ljudi danas prvo traže na Google-u.",
        "Jednostavan, pregledan sajt koji ih dovodi preko Google pretrage, sa kontaktom i lokacijom.",
    ),
    "samo_drustvene": (
        "Firma ima samo stranicu na društvenim mrežama (Facebook/Instagram), sajt nisi našao. Pohvali to što su "
        "aktivni na mrežama, pa objasni razliku: sopstveni sajt ih dovodi preko Google pretrage.",
        "Sopstveni sajt povezan sa njihovim mrežama, sa kontaktom i zakazivanjem.",
    ),
    "nebezbedan": (
        "Sajt nema ispravan SSL, pa Chrome posetiocima prikazuje „Nije bezbedno“. Objasni to jednostavno, "
        "bez tehničkog žargona i bez zastrašivanja.",
        "Brza popravka: SSL sertifikat i podešavanje, uz održavanje i hosting ako žele.",
    ),
    "nije_za_telefon": (
        "Sajt nije prilagođen telefonu (sadržaj se sitni ili izlazi van ekrana), a većina posetilaca dolazi "
        "sa telefona.",
        "Brza popravka za telefon ili, ako je sajt star, redizajn.",
    ),
    "zastareo": (
        "Sajt deluje zastarelo (stari dizajn, stara godina u footeru ili zastarela tehnologija). Budi taktičan: "
        "neko je taj sajt pravio i možda ga voli.",
        "Redizajn: moderan sajt prilagođen telefonu i brz.",
    ),
    "spor": (
        "Sajt se sporo učitava na telefonu, a ljudi odustanu ako stranica ne krene za par sekundi.",
        "Brza popravka: ubrzanje sajta bez celog redizajna.",
    ),
    "dobar_sajt": (
        "Sajt je dobar i savremen: iskreno ga pohvali u jednoj rečenici. Ne kritikuj sajt.",
        "AI automatizacija: online zakazivanje ili chat asistent koji odgovara na pitanja klijenata 24/7.",
    ),
}
DEFAULT_ANGLE = ANGLES["zastareo"]

RULES = """Pravila za tekst:
- Srpski, latinica, ekavica. Persiranje (Vi/Vaš), ali prirodno i ljudski, bez birokratskog jezika.
- Ti si Aleksa, pišeš lično jednoj firmi. Bez marketinških fraza ("revolucionarno", "AKCIJA", uzvičnici).
- Najviše 120 reči. Kratki pasusi. Običan tekst, bez nabrajanja sa zvezdicama, bez emoji.
- Pomeni NAJVIŠE 2 konkretna nedostatka, i to SAMO iz dostavljenog spiska. Ništa ne izmišljaj.
- Bez cena. Bez linkova osim aleksakrstovic.rs (i njega najviše jednom, može i bez).
- Poziv na akciju bez obaveze: ponudi da pošalješ kratku besplatnu analizu sa predlogom šta popraviti.
- NE piši potpis, pozdrav na kraju ("Srdačan pozdrav") ni rečenicu o odjavi: to se dodaje automatski.
- Naslov: kratak (do 60 znakova), konkretan, bez velikih slova i bez mamaca. Na primer: "Kratko zapažanje o sajtu {firma}"."""

FOLLOWUP_RULES = """Ovo je JEDINI podsetnik na prethodni mejl na koji firma nije odgovorila.
- Najviše 50 reči, ljubazno, bez pritiska i bez prebacivanja ("niste odgovorili").
- Ponovi ponudu besplatne kratke analize u jednoj rečenici.
- Naslov: "Re: " + naslov prvog mejla.
- NE piši potpis ni rečenicu o odjavi."""


# Primeri koje je Aleksa odobrio: AI ih dobija kao uzor tona, dužine i strukture (ne kopira doslovno).
EXAMPLES: dict[str, tuple[str, str]] = {
    "sajt_ne_radi": (
        "Vaš sajt trenutno ne radi",
        "Poštovani,\n\ntražio sam ordinacije na Vračaru i naišao na Vaš sajt primer-ordinacija.rs, ali umesto stranice "
        "dobijam poruku da domen ne postoji. Moguće je da je registracija domena istekla.\n\nPišem jer se ovo lako "
        "previdi, a pacijenti koji Vas traže na internetu sada vide grešku i verovatno odlaze kod konkurencije.\n\n"
        "Bavim se izradom i održavanjem sajtova. Ako želite, mogu da proverim šta se tačno desilo i javim Vam kako "
        "da sajt najbrže vratite u rad, bez obaveze.",
    ),
    "bez_sajta": (
        "Pitanje o sajtu Frizerskog salona Mia",
        "Poštovani,\n\ntražio sam frizerske salone u Zemunu i naišao na Vaš salon, ali nisam uspeo da pronađem Vaš "
        "sajt. Da li ga imate?\n\nPitam jer većina ljudi danas novi salon prvo traži na Google-u, a bez sajta Vas "
        "tamo teže pronalaze i lakše odaberu nekog drugog.\n\nPravim jednostavne i pregledne sajtove sa radnim "
        "vremenom, cenovnikom, lokacijom i kontaktom. Ako želite, poslaću Vam kratak predlog kako bi to izgledalo "
        "za Vaš salon, bez ikakve obaveze.",
    ),
    "samo_drustvene": (
        "Vaš Instagram i Google pretraga",
        "Poštovani,\n\npratio sam Vaš Instagram profil i vidi se da redovno objavljujete radove, što je odlično. "
        "Primetio sam, međutim, da nemate sopstveni sajt.\n\nInstagram je dobar za ljude koji Vas već prate, ali "
        "kad neko na Google-u ukuca „kozmetički salon Novi Sad“, pojavljuju se sajtovi, a ne profili na mrežama.\n\n"
        "Mogu da napravim jednostavan sajt povezan sa Vašim Instagramom, sa cenovnikom i mogućnošću zakazivanja. "
        "Ako želite, pošaljem Vam kratak predlog, bez obaveze.",
    ),
    "nebezbedan": (
        "Kratko zapažanje o sajtu auto servisa Kralj",
        "Poštovani,\n\npregledao sam Vaš sajt primer-servis.rs i primetio da Chrome pored adrese prikazuje "
        "upozorenje „Nije bezbedno“. To znači da sajt nema SSL sertifikat.\n\nMnogi posetioci se na takvo "
        "upozorenje uplaše i zatvore stranicu, a i Google takve sajtove slabije prikazuje u pretrazi.\n\n"
        "Popravka je brza i ne zahteva novi sajt. Ako želite, pošaljem Vam kratku besplatnu analizu sa predlogom "
        "šta tačno treba uraditi.",
    ),
    "nije_za_telefon": (
        "Vaš sajt na telefonu",
        "Poštovani,\n\notvorio sam sajt Advokatske kancelarije Petrović sa telefona i primetio da se tekst sitni, "
        "a deo sadržaja izlazi van ekrana, pa mora da se uvećava i pomera.\n\nDanas većina ljudi prvi put otvara "
        "sajt upravo sa telefona, i ako je teško čitljiv, brzo ga zatvore.\n\nOvo se može popraviti i bez potpuno "
        "novog sajta. Ako želite, pošaljem Vam kratku besplatnu analizu sa predlogom šta bi trebalo promeniti.",
    ),
    "zastareo": (
        "Kratko zapažanje o sajtu Pekare Zlatni klas",
        "Poštovani,\n\npregledao sam sajt Vaše pekare i vidi se da je pravljen sa dosta truda, ali deluje kao da "
        "nije osvežavan neko vreme. U dnu stranice i dalje piše © 2016, a sajt nije prilagođen telefonu.\n\n"
        "Posetiocima to ponekad šalje poruku da firma više nije aktivna, iako to naravno nije slučaj.\n\nBavim se "
        "redizajnom sajtova: moderan izgled, prilagođen telefonu, a sadržaj koji već imate ostaje. Ako želite, "
        "pošaljem Vam kratku besplatnu analizu sa predlogom.",
    ),
    "spor": (
        "Brzina Vašeg sajta na telefonu",
        "Poštovani,\n\nproverio sam sajt Hotela Sunce preko Google-ovog alata za merenje brzine i na telefonu je "
        "dobio 28 od 100 poena. Glavni sadržaj se učitava oko 6 sekundi.\n\nVeliki deo posetilaca odustane ako "
        "stranica ne krene za 2–3 sekunde, a za hotel su to propuštene rezervacije.\n\nUbrzanje se obično radi bez "
        "promene izgleda sajta. Ako želite, pošaljem Vam kratku besplatnu analizu šta konkretno usporava sajt.",
    ),
    "dobar_sajt": (
        "Ideja za Vašu ordinaciju",
        "Poštovani,\n\npregledao sam sajt Vaše ordinacije i moram da kažem da je odlično urađen: pregledan, brz i "
        "lepo izgleda na telefonu.\n\nPišem sa drugom idejom: pravim AI asistente koji na sajtu odgovaraju na česta "
        "pitanja pacijenata (cene, radno vreme, pripreme za intervencije) i primaju zahteve za termin, i van "
        "radnog vremena.\n\nAko Vam je zanimljivo, mogu da Vam pošaljem kratak primer kako bi to izgledalo na Vašem "
        "sajtu, bez obaveze.",
    ),
}

FOLLOWUP_EXAMPLE = (
    "Re: Kratko zapažanje o sajtu auto servisa Kralj",
    "Poštovani,\n\nsamo kratko podsećam na moju prethodnu poruku. Ako Vam je zanimljivo, rado Vam pošaljem "
    "besplatnu analizu sajta, a ako nije, potpuno razumem.",
)


def example_for(category: str | None, kind: str = "prvi") -> tuple[str, str]:
    if kind == "followup":
        return FOLLOWUP_EXAMPLE
    return EXAMPLES.get(category or "", EXAMPLES["zastareo"])


def signature() -> str:
    name = config.get("FROM_NAME", "Aleksa Krstović")
    title = config.get("SIGNATURE_TITLE", "Izrada sajtova i AI automatizacije")
    phone = config.get("SIGNATURE_PHONE", "")
    web = config.get("SIGNATURE_WEB", "aleksakrstovic.rs")
    contact = " · ".join(p for p in [web, phone] if p)
    return f"{name}\n{title}\n{contact}"


def compose(core: str) -> str:
    """Ceo mejl: tekst + pozdrav + potpis + rečenica za odjavu."""
    return f"{core.strip()}\n\nSrdačan pozdrav,\n{signature()}\n\n{OPT_OUT}"
