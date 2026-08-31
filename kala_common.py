"""
Kala Health — gedeelde kern
===========================
kalahealth.nl is een publieke **WooCommerce**-winkel (WordPress, geen login).
Alles komt uit twee bronnen:

  1. /wp-json/wc/store/v1/products                -> de 81 productpagina's
     /wp-json/wc/store/v1/products?type=variation -> de 159 losse varianten
     (samen 163 verkoopbare regels: sku, prijs regulier + actie, voorraadstatus,
     afbeeldingen, categorieen, opties, korte + lange beschrijving)
  2. de productpagina zelf -> de WooCommerce-tabbladen die NIET in de API zitten:
     Samenstelling & ingredienten, Gebruik & dosering, Certificaten,
     Achtergrondinformatie en de FAQ.

Bijzonderheden van deze winkel — waarom de code doet wat hij doet:

- **33 van de 163 varianten hebben geen SKU** bij Kala (o.a. Magnesium
  Bisglycinaat Capsules, Chaga Bio, Liposomale Curcumine, Creatine, en vier
  bundels), en een variant draagt de tekst "SKU Voorraad" als SKU. Stock Sync
  matcht op SKU: zonder SKU is een product na het aanmaken nooit meer bij te
  werken. Daarom krijgt zo'n variant een **toegekende SKU** `KALA-<variant-id>`,
  vastgelegd in `kala_sku_map.json` zodat hij nooit meer verschuift. Het veld
  `sku_bron` in de feed zegt altijd of de SKU van Kala komt of van ons.
- **Nergens een EAN/GTIN** — niet in de API, niet in schema.org, niet in de HTML.
  `barcode` blijft dus leeg; Stock Sync matcht op SKU.
- **Handles krijgen het voorvoegsel `kala-health-`.** Kala verkoopt producten met
  doodgewone namen (Probiotica, Astaxanthine, Magnesium Complex, Vegan Omega 3)
  die bij Good For You al bestaan van andere merken. Met de Stock Sync-instelling
  "varianten samenvoegen in bestaande producten" AAN zouden Kala-varianten in
  andermans product schuiven. Het voorvoegsel maakt dat onmogelijk.
- Voorraad is alleen in/uit/nabestelling, **geen aantal**. Er wordt dus ook geen
  aantal verzonnen: de feed levert `available` (true/false) en
  `voorraad_indicatie` (op-voorraad / nabestelling / uitverkocht).
- De teksten van Kala bevatten een tracking-script, inline stijlen en links terug
  naar kalahealth.nl. `schoon_html()` haalt die eruit; er wordt niets bijgeschreven.

Prijsbeleid (env `KALA_PRIJS_BASIS`):
  "advies"  = de reguliere prijs zonder actie  (STANDAARD)
  "actueel" = de vandaag getoonde prijs, inclusief lopende actie
Optioneel `KALA_PRIJS_FACTOR` (bv. 1.05) vermenigvuldigt de prijs. Standaard 1.0.
Er wordt nooit een compare_at_price verzonnen: die blijft leeg.

Lokaal testen achter een SSL-onderscheppende proxy: INSECURE_SSL=1.
Een product testen: TEST_SLUG=<slug>.  Pagina's cachen: KALA_CACHE_DIR=...
"""

import csv
import json
import os
import re
import time
from html import unescape

import requests

BASE_URL = "https://www.kalahealth.nl"
STORE_API = f"{BASE_URL}/wp-json/wc/store/v1/products"
BRAND = "Kala Health"
HANDLE_PREFIX = "kala-health-"
REQUEST_DELAY = 0.4

OVERGESLAGEN_FILE = "kala_overgeslagen.csv"
GESCHRAPT_FILE = "kala_geschrapt.csv"
SKU_MAP_FILE = "kala_sku_map.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; GFY-KalaFeed/1.0)",
    "Accept-Language": "nl-NL,nl;q=0.9",
}

VERIFY_SSL = os.environ.get("INSECURE_SSL") != "1"
if not VERIFY_SSL:
    import urllib3
    urllib3.disable_warnings()

PRIJS_BASIS = os.environ.get("KALA_PRIJS_BASIS", "advies").lower()
PRIJS_FACTOR = float(os.environ.get("KALA_PRIJS_FACTOR", "1.0"))

# Kala verkoopt ook twee producten van een ander merk onder eigen categorie.
# Alleen die twee krijgen een andere vendor; ButyraGen/OptiMSM/Ester-C/Aspitol/
# NaturGlux zijn grondstof-merken, geen producent van het product.
VREEMDE_MERKEN = {"The Akkermansia Company"}

# Categorieen die zeggen WAT iets is (producttype) tegenover categorieen die
# zeggen WAARVOOR je het neemt (doelen) of welk bestanddeel erin zit. Alleen de
# eerste soort mag het Shopify-producttype worden; de rest wordt een tag.
PRODUCTTYPES = [
    "Bundels",
    "MSM producten",
    "Paddenstoelen",
    "Pre-, Pro- en Postbiotica",
    "Omega 3-vetzuren",
    "Vezels en Enzymen",
    "Liposomale producten",
    "Vitaminen",
    "Mineralen",
    "Fytonutrienten",
    "Specialiteiten",
]

# Producten die geen voedingssupplement zijn maar cosmetica (huid). Ze mogen mee
# in de feed, maar Max moet weten dat er een ander BTW-tarief op zit en dat
# Themis er anders naar kijkt.
# Op hele woorden, niet op letterreeksen: "Nagel" en "Dagelijkse" bevatten ook
# "gel" en zijn geen huidverzorging.
COSMETICA_RE = re.compile(r"\b(gel|creme|cr[eè]me|zalf|lotion|balsem)\b", re.I)

# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


def _cache_pad(url):
    """Alleen voor lokaal ontwikkelen: KALA_CACHE_DIR=... zet opgehaalde pagina's
    op schijf, zodat je de tekstverwerking kunt bijschaven zonder de winkel van
    Kala Health 81 keer opnieuw te bevragen."""
    map_ = os.environ.get("KALA_CACHE_DIR")
    if not map_ or "wp-json" in url:      # de API nooit uit cache: die moet vers
        return None
    os.makedirs(map_, exist_ok=True)
    naam = re.sub(r"[^a-zA-Z0-9]+", "_", url)[-120:] + ".html"
    return os.path.join(map_, naam)


def _get(url, retries=3):
    pad = _cache_pad(url)
    if pad and os.path.exists(pad):
        class _Cached:
            text = open(pad, encoding="utf-8", errors="replace").read()
            headers = {}

            def json(self):
                return json.loads(self.text)
        return _Cached()

    for poging in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30, verify=VERIFY_SSL)
            resp.raise_for_status()
            if pad:
                with open(pad, "w", encoding="utf-8") as f:
                    f.write(resp.text)
            return resp
        except Exception as e:
            if poging < retries - 1:
                wacht = (poging + 1) * 15
                print(f"    !  Fout ({e}), opnieuw in {wacht}s...")
                time.sleep(wacht)
            else:
                raise


def _api(query):
    """Alle pagina's van een Store API-vraag, met de paginateller van WordPress."""
    uit, pagina = [], 1
    while True:
        vraag = f"{query}&" if query else ""
        resp = _get(f"{STORE_API}?{vraag}per_page=100&page={pagina}")
        batch = resp.json()
        if not batch:
            break
        uit.extend(batch)
        totaal = int(resp.headers.get("x-wp-totalpages", pagina) or pagina)
        if pagina >= totaal:
            break
        pagina += 1
        time.sleep(REQUEST_DELAY)
    return uit


def fetch_catalogus():
    """(ouders, varianten) uit de Store API — ongefilterd, zoals Kala ze geeft."""
    # Zonder type-filter geeft de Store API alleen de productpagina's terug,
    # niet de losse varianten. Een lijstje types meegeven ("simple,variable")
    # accepteert WooCommerce niet — dan komt er niets terug.
    ouders = _api("")
    varianten = _api("type=variation")
    return ouders, varianten


# --------------------------------------------------------------------------- #
# SKU — Kala's nummer als het er is, anders een van ons die nooit meer verschuift
# --------------------------------------------------------------------------- #
def geldige_sku(ruw):
    """Een echt artikelnummer van Kala, of "".

    Kala's nummers zijn 7-cijferig, soms met een achtervoegsel (1004060-V,
    1701060-1-1). Wat geen cijferreeks is, is geen artikelnummer: een variant
    draagt letterlijk de tekst "SKU Voorraad".
    """
    s = (ruw or "").strip()
    if not s or " " in s:
        return ""
    return s if re.fullmatch(r"\d{6,}(?:-[0-9A-Za-z]+)*", s) else ""


def _lees_sku_map(pad=SKU_MAP_FILE):
    if os.path.exists(pad):
        with open(pad, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _schrijf_sku_map(kaart, pad=SKU_MAP_FILE):
    with open(pad, "w", encoding="utf-8", newline="\n") as f:
        json.dump(kaart, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def wijs_skus_toe(rijen, datum, pad=SKU_MAP_FILE):
    """Zet op elke rij `sku` en `sku_bron`; werkt `kala_sku_map.json` bij.

    De regel is: **een SKU die eenmaal in de feed heeft gestaan verandert nooit
    meer.** Anders maakt Stock Sync bij de volgende run een tweede product aan en
    blijft het eerste als wees achter. Krijgt een variant later alsnog een
    artikelnummer van Kala, dan wordt dat wel vastgelegd en gemeld, maar de feed
    blijft de SKU gebruiken die er al in stond — hernummeren is handwerk in
    Shopify, geen bijwerking van een feed.
    """
    kaart = _lees_sku_map(pad)
    toegekend, nieuw_bij_kala = [], []
    for r in rijen:
        sleutel = str(r["variant_id"])
        van_kala = geldige_sku(r["sku_leverancier"])
        bekend = kaart.get(sleutel)

        if bekend:
            r["sku"] = bekend["sku"]
            r["sku_bron"] = bekend["bron"]
            if van_kala and bekend["bron"] == "toegekend":
                nieuw_bij_kala.append((r, van_kala))
            bekend["sku_leverancier"] = van_kala
            bekend["laatst_gezien"] = datum
        else:
            r["sku"] = van_kala or f"KALA-{r['variant_id']}"
            r["sku_bron"] = "leverancier" if van_kala else "toegekend"
            if not van_kala:
                toegekend.append(r)
            kaart[sleutel] = {
                "sku": r["sku"],
                "bron": r["sku_bron"],
                "sku_leverancier": van_kala,
                "titel": r["titel_vol"],
                "sinds": datum,
                "laatst_gezien": datum,
            }
    _schrijf_sku_map(kaart, pad)

    if toegekend:
        print(f"   SKU      {len(toegekend)} varianten zonder artikelnummer bij "
              f"Kala kregen er een van ons (KALA-<id>):")
        for r in toegekend[:40]:
            print(f"            - {r['sku']:<12} {r['titel_vol'][:60]}")
    for r, van_kala in nieuw_bij_kala:
        print(f"   LET OP   Kala geeft nu wel een artikelnummer voor "
              f"{r['titel_vol'][:45]}: {van_kala} (feed houdt {r['sku']}). "
              f"Hernummeren kan alleen met de hand in Shopify.")
    return kaart


# --------------------------------------------------------------------------- #
# De productpagina: de tabbladen die niet in de API zitten
# --------------------------------------------------------------------------- #
_PANEEL = re.compile(
    r'<div class="woocommerce-Tabs-panel woocommerce-Tabs-panel--([a-z_]+)[^"]*"[^>]*>',
    re.I)
_DIV_TAG = re.compile(r"</?div\b", re.I)

# Welk tabblad wordt welke kop in onze beschrijving. Wat hier niet in staat gaat
# niet mee: `reviews_tab` is bij Kala een berg scripts en reviews van hun eigen
# klanten, en die zijn niet van ons.
TABBLADEN = {
    "samenstelling_tab": "Samenstelling & ingrediënten",
    "gebruikdosering_tab": "Gebruik & dosering",
    "certificaten_tab_content": "Certificaten",
    "achtergrond_informatie_tab": "Achtergrondinformatie",
    "add_comments_tab": "Veelgestelde vragen",
}

# Kala's eigen winkelpraat hoort niet in onze productteksten. Elk stuk dat
# hierop wegvalt wordt geteld en in kala_geschrapt.csv vastgelegd: stil
# wegfilteren mag niet.
#
# Wat er weg moet en waarom:
#   contact       — "Bel ons op (+31) 070-345-0290 of mail naar info@kalahealth.nl"
#                   staat onder 64 beschrijvingen. Op onze pagina stuurt dat de
#                   klant naar de leverancier.
#   kruisverkoop  — "Bekijk onze eigen Probiotica", "-> Lees meer over CM Creme":
#                   verwijzingen naar Kala's andere producten. De link zelf is er
#                   al uit, de zin zou als losse tekst achterblijven.
#   eigen winkel  — "te bekijken in de productgalerij op deze pagina" klopt bij
#                   ons niet: die certificaten staan niet in onze galerij.
#
# WEREN_BLOK haalt het hele blok weg, WEREN_ZIN alleen de zin. Dat onderscheid is
# nodig: bij "Elke inkomende batch wordt onafhankelijk geverifieerd. Het
# analysecertificaat vind je in de productgalerij." is de eerste zin echte
# productinformatie en alleen de tweede winkelpraat.
#
# Wat hier NIET in staat en dus blijft: "geproduceerd in onze eigen FSSC
# 22000-faciliteit" en soortgelijke zinnen. Dat is productinformatie in Kala's
# stem; wat daarmee moet, beslissen Themis en Max, niet deze zeef.
WEREN_BLOK = [
    ("contact", re.compile(
        r"bel ons op|denke?[nt] graag met je mee", re.I)),
    ("kruisverkoop", re.compile(
        r"lees meer over|bekijk (?:onze|ons|de|dan ook)|ook interessant", re.I)),
    # De byline van Kala's eigen wetenschapsredacteur onder een artikel.
    ("auteur", re.compile(
        r"geschreven door|wetenschapscommunicatie", re.I)),
]

WEREN_ZIN = [
    ("contact", re.compile(
        r"info@kalahealth|070[\s-]?345|bel ons|telefonisch bereikbaar|"
        r"contact (?:met ons op|op te nemen met ons|opnemen met ons)|"
        r"voor inhoudelijke vragen|staat ons team", re.I)),
    ("eigen winkel", re.compile(
        r"productgalerij|op deze pagina|onze webshop|gratis verzending|"
        r"achteraf betalen|volg je bestelling|klantenservice|kalahealth\.nl|"
        r"kala health b\.v\.", re.I)),
]

# Koppen waarvan het hele blok tot de volgende kop wegvalt.
SECTIES_WEREN = re.compile(
    r"waarom koop je bij kala|waarom kala|over kala health|ook interessant|"
    r"onze belofte", re.I)

# WPBakery laat zijn eigen shortcodes in de API-tekst staan; op de pagina worden
# ze weggerenderd, in de feed zouden ze als "[/vc_column_text]" zichtbaar worden.
SHORTCODE = re.compile(r"\[/?[a-z][a-z0-9_]*(?:\s[^\]]*)?\]", re.I)

# Blok-elementen die in Kala's teksten nooit genest voorkomen, en dus in hun
# geheel te verwijderen zijn als er winkelpraat in staat. `i`/`em`/`td` staan
# erbij omdat Kala's FAQ-antwoorden niet in een <p> staan maar in
# <div><div><div><i>...</i>, en de vergelijkingstabellen hun tekst in <td>.
_BLOK = re.compile(
    r"<(p|li|h2|h3|h4|blockquote|i|em|td)\b[^>]*>(.*?)</\1\s*>", re.I | re.S)
_KOP = re.compile(r"<h([2-4])\b[^>]*>(.*?)</h\1\s*>", re.I | re.S)


def _balanced_div(src, start):
    """Inhoud van het <div> dat op positie `start` opent, met genest tellen."""
    diepte = 0
    open_eind = src.index(">", start) + 1
    for m in _DIV_TAG.finditer(src, start):
        if m.group(0).lower().startswith("</"):
            diepte -= 1
            if diepte == 0:
                return src[open_eind:m.start()]
        else:
            diepte += 1
    return ""


def parse_tabbladen(html):
    """{tabblad-naam: schone html} van een productpagina."""
    uit = {}
    for m in _PANEEL.finditer(html):
        naam = m.group(1)
        if naam not in TABBLADEN:
            continue
        inhoud = schoon_html(_balanced_div(html, m.start()))
        if plat(inhoud):
            uit[naam] = inhoud
    return uit


# --------------------------------------------------------------------------- #
# HTML schoonmaken
# --------------------------------------------------------------------------- #
_WEG_MET_INHOUD = re.compile(
    r"<(script|style|noscript|form|iframe|button|svg|select)\b.*?</\1\s*>",
    re.I | re.S)
_LOSSE_TAGS = re.compile(r"<(script|style|noscript|form|iframe|button|svg|img|input)\b[^>]*/?>",
                         re.I)
_ANKER = re.compile(r"</?a\b[^>]*>", re.I)
_ATTRIBUUT = re.compile(
    r'\s+(?:style|class|id|lang|dir|title|on\w+|data-[\w-]+|srcset|sizes|width|'
    r'height|loading|decoding|aria-[\w-]+|role|tabindex)\s*=\s*'
    r'(?:"[^"]*"|\'[^\']*\'|[^\s>]+)',
    re.I)
_LEGE_BLOKKEN = re.compile(r"<(p|div|span|h[1-6])>\s*</\1>", re.I)
_TOEGESTAAN = re.compile(
    r"</?(p|br|ul|ol|li|strong|b|em|i|u|h2|h3|h4|h5|h6|table|thead|tbody|tfoot|"
    r"tr|th|td|sup|sub|blockquote|div|span)\b[^>]*>", re.I)


def schoon_html(ruw):
    """Kala's HTML zonder scripts, stijlen, formulieren, links en afbeeldingen.

    Er wordt niets herschreven of samengevat — alleen weggehaald wat niet in een
    productbeschrijving van Good For You hoort: het tracking-script van
    revealid.xyz dat in de beschrijving zit, inline stijlen die naar Kala's eigen
    CSS-variabelen wijzen, en links terug naar kalahealth.nl.
    """
    h = ruw or ""
    vorig = None
    while vorig != h:                      # geneste script-in-div-in-script
        vorig = h
        h = _WEG_MET_INHOUD.sub(" ", h)
    h = _LOSSE_TAGS.sub(" ", h)
    h = _ANKER.sub("", h)                  # link weg, tekst blijft
    h = _ATTRIBUUT.sub("", h)
    # Wat overblijft aan onbekende tags eruit; de rest van de opmaak blijft staan.
    h = re.sub(r"<(?!/?(?:p|br|ul|ol|li|strong|b|em|i|u|h[2-6]|table|thead|tbody|"
               r"tfoot|tr|th|td|sup|sub|blockquote|div|span)\b)[^>]*>", " ", h)
    for _ in range(3):
        h = _LEGE_BLOKKEN.sub("", h)
    h = re.sub(r"(\s*<br\s*/?>\s*){3,}", "<br><br>", h, flags=re.I)
    h = re.sub(r"[ \t]{2,}", " ", h)
    h = re.sub(r"(\n\s*){3,}", "\n\n", h)
    return h.strip()


def plat(html):
    """Alleen de tekst — om te tellen en te toetsen, niet om te publiceren."""
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html or ""))).strip()


# Alles wat `weer_winkelpraat` weghaalt, komt hier terecht: (handle, reden,
# fragment). Zonder dit logboek zou de opschoning een stille zeef zijn.
GESCHRAPT = []


def _schrap_secties(h, handle):
    """Een kop als "Waarom koop je bij Kala Health?" plus alles eronder tot de
    volgende kop van hetzelfde of een hoger niveau."""
    while True:
        for m in _KOP.finditer(h):
            if not SECTIES_WEREN.search(plat(m.group(2))):
                continue
            niveau = int(m.group(1))
            eind = len(h)
            for volgende in _KOP.finditer(h, m.end()):
                if int(volgende.group(1)) <= niveau:
                    eind = volgende.start()
                    break
            GESCHRAPT.append([handle, "sectie", plat(h[m.start():eind])[:200]])
            h = h[:m.start()] + h[eind:]
            break
        else:
            return h


# Zinsgrenzen alleen op een punt die door witruimte of het eind wordt gevolgd:
# anders knipt "info@kalahealth.nl." middenin en blijft er "nl." achter, en
# breekt "ChondroPure < 10.000 daltons" in tweeen.
_ZIN = re.compile(r".*?[.!?]+(?=\s|$)\s*|.+$", re.S)


def _schrap_zinnen(binnen, handle):
    """Alleen de zin met winkelpraat eruit, de rest van het blok blijft staan.

    Een zin waar opmaak (<strong>, <em>) doorheen loopt wordt niet half
    weggeknipt — dan valt het hele blok weg, met vermelding in het logboek,
    zodat er nooit een kapotte tag achterblijft.
    """
    if not any(p.search(plat(binnen)) for _, p in WEREN_ZIN):
        return binnen
    stukken, houden = _ZIN.findall(binnen), []
    for stuk in stukken:
        reden = next((r for r, p in WEREN_ZIN if p.search(plat(stuk))), None)
        if not reden:
            houden.append(stuk)
            continue
        if "<" in stuk or ">" in stuk:      # opmaak in de zin: te riskant
            GESCHRAPT.append([handle, reden + " (heel blok)", plat(binnen)[:200]])
            return ""
        GESCHRAPT.append([handle, reden, plat(stuk)[:200]])
    return "".join(houden)


def weer_winkelpraat(h, handle=""):
    """Kala's shortcodes, contactgegevens, kruisverkoop en winkelpraat eruit.

    Werkt per blok-element (<p>, <li>, <h2..4>) zodat er nooit een halve zin
    blijft staan, en legt elk geschrapt fragment vast in GESCHRAPT.
    """
    for m in SHORTCODE.finditer(h):
        GESCHRAPT.append([handle, "shortcode", m.group(0)[:200]])
    h = SHORTCODE.sub("", h)
    h = _schrap_secties(h, handle)

    def _blok(m):
        binnen = m.group(2)
        for reden, patroon in WEREN_BLOK:
            if patroon.search(plat(binnen)):
                GESCHRAPT.append([handle, reden, plat(binnen)[:200]])
                return ""
        nieuw = _schrap_zinnen(binnen, handle)
        if not plat(nieuw):
            return ""
        return m.group(0).replace(binnen, nieuw, 1)

    h = _BLOK.sub(_blok, h)
    # Blokjes van een of twee tekens: wat overblijft van een weggehaalde
    # LinkedIn-knop is een losse "in" midden in de tekst.
    h = re.sub(r"<(p|div|span|li)>\s*\w{1,2}\s*</\1>", "", h, flags=re.I)
    for _ in range(4):
        h = _LEGE_BLOKKEN.sub("", h)
    return re.sub(r"(\n\s*){3,}", "\n\n", h).strip()


def _heeft_eigen_kop(inhoud, kop):
    """Begint dit tabblad al met zijn eigen kop? Dan geen tweede erboven.

    Het tabblad "Samenstelling & ingredienten" opent zelf met <h2>Samenstelling</h2>;
    zonder deze controle staat die kop er twee keer.
    """
    m = _KOP.search(inhoud)
    if not m or plat(inhoud[:m.start()]):
        return False
    a = re.sub(r"[^a-z]", "", plat(m.group(2)).lower())
    b = re.sub(r"[^a-z]", "", kop.lower())
    return bool(a) and (a.startswith(b[:12]) or b.startswith(a[:12]))


def bouw_beschrijving(ouder, tabbladen, handle=""):
    """Korte beschrijving + het Beschrijving-tabblad + de losse tabbladen.

    Alles staat letterlijk zo op kalahealth.nl — er wordt niets bijgeschreven of
    samengevat, alleen weggehaald (zie weer_winkelpraat). De volgorde is die van
    de productpagina zelf.
    """
    delen = []
    for veld in ("short_description", "description"):
        stuk = weer_winkelpraat(schoon_html(ouder.get(veld, "")), handle)
        if plat(stuk):
            delen.append(stuk)
    for sleutel, kop in TABBLADEN.items():
        inhoud = weer_winkelpraat(tabbladen.get(sleutel, ""), handle)
        if not plat(inhoud):
            continue
        delen.append(inhoud if _heeft_eigen_kop(inhoud, kop)
                     else f"<h3>{kop}</h3>\n{inhoud}")
    return "\n".join(delen)


# --------------------------------------------------------------------------- #
# Normaliseren
# --------------------------------------------------------------------------- #
def maak_handle(titel):
    """Een eigen handle uit de titel, met voorvoegsel.

    Niet Kala's slug: die is bij een kwart van de producten een restant van hun
    eigen CMS (`kalahealth-nl-product-magnesium-bis-caps`,
    `gebufferde-vitamine-c-capsules2`). Wel altijd `kala-health-`ervoor, zodat
    "Probiotica" van Kala nooit in het bestaande product "probiotica" van een
    ander merk schuift (Stock Sync voegt varianten samen op handle).
    """
    t = unescape(titel or "").lower()
    t = (t.replace("®", " ").replace("™", " ").replace("&", " en ")
          .replace("+", " plus ").replace("’", "").replace("'", ""))
    t = (t.replace("ë", "e").replace("é", "e").replace("ï", "i").replace("ö", "o")
          .replace("ü", "u").replace("è", "e").replace("ê", "e").replace("ç", "c"))
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return HANDLE_PREFIX + re.sub(r"-{2,}", "-", t)


def bepaal_vendor(titel, categorieen):
    for merk in VREEMDE_MERKEN:
        if unescape(titel).startswith(merk) and any(
                merk in c for c in categorieen):
            return merk
    return BRAND


def bepaal_producttype(categorieen):
    """Het eerste echte producttype; doel-categorieen worden alleen tags.

    "Liposomaal IJzer" staat in Mineralen en in Liposomale producten; "Chaga Bio"
    in Paddenstoelen, Fytonutrienten en Specialiteiten. Zonder vaste volgorde
    hangt het producttype af van de volgorde waarin Kala ze toevallig opsomt.
    """
    namen = {c.replace("ë", "e"): c for c in categorieen}
    for pt in PRODUCTTYPES:
        if pt in namen:
            return namen[pt]
    return categorieen[0] if categorieen else ""


def is_cosmetica(titel):
    return bool(COSMETICA_RE.search(unescape(titel or "")))


def bepaal_prijs(bron):
    """Prijs in euro volgens het ingestelde beleid. Nooit geraden."""
    p = bron["prices"]
    centen = p["regular_price"] if PRIJS_BASIS == "advies" else p["price"]
    return round(int(centen) / 100 * PRIJS_FACTOR, 2)


def voorraad_indicatie(bron):
    """op-voorraad / nabestelling / uitverkocht — geen verzonnen aantal.

    Kala geeft geen voorraadaantallen. "Beschikbaar via nabestelling" telt hier
    als NIET beschikbaar: Kala kan het dan zelf niet direct leveren, en dan wil
    Max het niet als voorradig in zijn winkel hebben staan.
    """
    klasse = (bron.get("stock_availability") or {}).get("class", "")
    if klasse == "available-on-backorder" or bron.get("is_on_backorder"):
        return "nabestelling"
    if not bron.get("is_in_stock"):
        return "uitverkocht"
    return "op-voorraad"


def _gewicht_gram(tekst):
    """Gewicht uit een optie als "500g" of "1kg"; "" als het er niet staat."""
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(kilo|kg|gram|gr|g)\b", tekst or "", re.I)
    if not m:
        return ""
    getal = float(m.group(1).replace(",", "."))
    return str(int(round(getal * 1000 if m.group(2).lower() in ("kilo", "kg")
                         else getal)))


def _optie_waarde(bron, ouder):
    """De onderscheidende waarde van een variant ("180 capsules").

    Bij Kala staat die in `variation` als "Aantal: 180 capsules". Een product
    zonder varianten krijgt de naam van de enige uitvoering.
    """
    v = unescape(bron.get("variation") or "")
    if ":" in v:
        return v.split(":", 1)[1].strip()
    return v.strip() or "Standaard"


def _optie_naam(ouder):
    for a in ouder.get("attributes", []):
        if a.get("has_variations"):
            return a.get("name", "") or "Uitvoering"
    return "Uitvoering"


def normaliseer_variant(bron, ouder):
    afbeeldingen = [i["src"] for i in bron.get("images", []) if i.get("src")]
    optie = _optie_waarde(bron, ouder)
    return {
        "variant_id": bron["id"],
        "sku_leverancier": (bron.get("sku") or "").strip(),
        "sku": "",                            # wordt gezet door wijs_skus_toe()
        "sku_bron": "",
        "barcode": "",                        # Kala voert nergens een EAN
        "prijs": bepaal_prijs(bron),
        "prijs_regulier": round(int(bron["prices"]["regular_price"]) / 100, 2),
        "prijs_actueel": round(int(bron["prices"]["price"]) / 100, 2),
        "in_actie": bool(bron.get("on_sale")),
        "voorraad": voorraad_indicatie(bron),
        "available": voorraad_indicatie(bron) == "op-voorraad",
        "optie1": optie,
        "variant_titel": optie,
        "gewicht": _gewicht_gram(optie),
        "afbeelding": afbeeldingen[0] if afbeeldingen else "",
        "titel_vol": f"{unescape(ouder['name'])} — {optie}",
        "url": bron.get("permalink") or ouder.get("permalink", ""),
    }


def normaliseer(ouder, varianten, tabbladen):
    titel = unescape(ouder["name"])
    handle = maak_handle(titel)
    categorieen = [unescape(c["name"]) for c in ouder.get("categories", [])]
    afbeeldingen = [i["src"] for i in ouder.get("images", []) if i.get("src")]
    for v in varianten:
        if v["afbeelding"] and v["afbeelding"] not in afbeeldingen:
            afbeeldingen.append(v["afbeelding"])
    tags = list(dict.fromkeys(
        categorieen
        + [unescape(t["name"]) for t in ouder.get("tags", [])]
        + (["cosmetica"] if is_cosmetica(titel) else [])))
    return {
        "product_id": ouder["id"],
        "handle": handle,
        "leverancier_handle": ouder.get("slug", ""),
        "titel": titel,
        "vendor": bepaal_vendor(ouder["name"], categorieen),
        "product_type": bepaal_producttype(categorieen),
        "tags": ", ".join(tags),
        "categorieen": categorieen,
        "beschrijving": bouw_beschrijving(ouder, tabbladen, handle),
        "tabbladen": sorted(tabbladen),
        "afbeeldingen": afbeeldingen,
        "optie1_naam": _optie_naam(ouder),
        "cosmetica": is_cosmetica(titel),
        "bundel": "Bundels" in categorieen,
        "url": ouder.get("permalink", ""),
        "varianten": varianten,
    }


# --------------------------------------------------------------------------- #
# Zeef — wat wegvalt, valt zichtbaar weg
# --------------------------------------------------------------------------- #
def _reden_overslaan(ouder, varianten):
    if ouder.get("type") not in ("simple", "variable"):
        return f"producttype '{ouder.get('type')}' — geen eigen prijs of variant"
    if not varianten:
        return "geen verkoopbare variant"
    if all(v["prijs_regulier"] == 0 for v in varianten):
        return "prijs is 0"
    if not ouder.get("images"):
        return "geen afbeelding"
    return None


def schrijf_overgeslagen(rijen, pad=OVERGESLAGEN_FILE):
    with open(pad, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "naam", "type", "reden"])
        w.writerows(rijen)
    print(f"   Overgeslagen vastgelegd in {pad} ({len(rijen)} regels)")


def schrijf_geschrapt(pad=GESCHRAPT_FILE):
    """Elk stuk tekst dat uit een beschrijving is gehaald, met reden.

    Zodat "81 producten met beschrijving" te controleren is: je kunt teruglezen
    wát er niet in staat en waarom.
    """
    with open(pad, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["handle", "reden", "fragment"])
        w.writerows(GESCHRAPT)
    tel = {}
    for _, reden, _fragment in GESCHRAPT:
        tel[reden] = tel.get(reden, 0) + 1
    print(f"   GESCHRAPT {len(GESCHRAPT)} stukken winkelpraat uit de teksten "
          f"(vastgelegd in {pad}):")
    for reden, n in sorted(tel.items(), key=lambda x: -x[1]):
        print(f"            - {n:>3}x {reden}")


# --------------------------------------------------------------------------- #
# Vangnet — een halve feed is gevaarlijker dan geen feed
# --------------------------------------------------------------------------- #
def controleer_omvang(aantal, vorig_bestand):
    """Stop de run bij een lege of gehalveerde feed.

    Stock Sync zet producten die niet in de feed staan op *gearchiveerd*, stil en
    zonder melding — dat heeft in mei, juni en juli 2026 drie keer een hele
    catalogus tot 44 dagen onvindbaar gemaakt. Een scraper die na een wijziging
    aan kalahealth.nl 0 of 40 producten vindt mag die uitkomst dus niet
    wegschrijven. Overrulen kan bewust met FORCE_FEED=1.
    """
    if os.environ.get("FORCE_FEED") == "1":
        return
    if aantal == 0:
        raise SystemExit("STOP: 0 producten gevonden — feed niet weggeschreven.")
    if not os.path.exists(vorig_bestand):
        return
    vorig = open(vorig_bestand, encoding="utf-8").read().count("<product>")
    if vorig and aantal < vorig / 2:
        raise SystemExit(
            f"STOP: {aantal} producten tegenover {vorig} in de vorige feed — "
            f"minder dan de helft. Feed niet weggeschreven (FORCE_FEED=1 "
            f"overrulet dit).")


# --------------------------------------------------------------------------- #
# De hoofdroute
# --------------------------------------------------------------------------- #
def fetch_products(met_teksten=True, datum=""):
    """De verkoopbare producten, genormaliseerd. De telling sluit altijd.

    met_teksten=False slaat de 81 productpagina's over (de update-feed heeft
    alleen SKU, prijs en voorraad nodig).
    """
    ouders, ruwe_varianten = fetch_catalogus()
    per_ouder = {}
    for v in ruwe_varianten:
        per_ouder.setdefault(v["parent"], []).append(v)
    print(f"   {len(ouders)} productpagina's + {len(ruwe_varianten)} varianten "
          f"uit de Store API")

    overgeslagen, houden = [], []
    for o in ouders:
        rijen = [normaliseer_variant(v, o) for v in per_ouder.get(o["id"], [])]
        if o["type"] == "simple":
            rijen = [normaliseer_variant(o, o)]
        reden = _reden_overslaan(o, rijen)
        if reden:
            overgeslagen.append([o["id"], unescape(o["name"]), o["type"], reden])
        else:
            houden.append((o, rijen))

    test = os.environ.get("TEST_SLUG")
    if test:
        houden = [(o, r) for o, r in houden if o.get("slug") == test]

    tabbladen = {}
    if met_teksten:
        print(f"   {len(houden)} productpagina's ophalen voor de tabbladen")
        for i, (o, _) in enumerate(houden, 1):
            try:
                tabbladen[o["id"]] = parse_tabbladen(_get(o["permalink"]).text)
            except Exception as e:
                print(f"    !  Productpagina faalt bij {o.get('slug')}: {e}")
                tabbladen[o["id"]] = {}
            print(f"  [{i}/{len(houden)}] {unescape(o['name'])[:44]:<44} "
                  f"- {len(tabbladen[o['id']])} tabbladen")
            time.sleep(REQUEST_DELAY)

    producten = [normaliseer(o, rijen, tabbladen.get(o["id"], {}))
                 for o, rijen in houden]
    wijs_skus_toe([v for p in producten for v in p["varianten"]], datum)

    alle_varianten = [v for p in producten for v in p["varianten"]]
    if not test:
        # Telling: elke productpagina uit de bron is of in de feed, of met reden
        # overgeslagen — en elke variant uit de bron hoort bij precies een product.
        assert len(producten) + len(overgeslagen) == len(ouders), (
            f"Telling klopt niet: {len(producten)} + {len(overgeslagen)} "
            f"!= {len(ouders)}")
        skus = [v["sku"] for v in alle_varianten]
        assert all(skus), "Variant zonder SKU in de feed"
        assert len(skus) == len(set(skus)), (
            f"Dubbele SKU in de feed: "
            f"{sorted({s for s in skus if skus.count(s) > 1})}")
        assert all(v["prijs"] > 0 for v in alle_varianten), (
            "Variant met prijs 0 in de feed")
        assert all(p["afbeeldingen"] for p in producten), (
            "Product zonder afbeelding in de feed")
        handles = [p["handle"] for p in producten]
        assert len(handles) == len(set(handles)), (
            f"Dubbele handle: {sorted({h for h in handles if handles.count(h) > 1})}")
        schrijf_overgeslagen(overgeslagen)
        if met_teksten:
            schrijf_geschrapt()

    redenen = {}
    for r in overgeslagen:
        redenen[r[3].split(" —")[0]] = redenen.get(r[3].split(" —")[0], 0) + 1
    print(f"\n   TELLING  {len(ouders)} productpagina's in de bron "
          f"= {len(producten)} in de feed + {len(overgeslagen)} overgeslagen")
    for kop, n in sorted(redenen.items(), key=lambda x: -x[1]):
        print(f"            - {n:>3}x {kop}")
    print(f"   REGELS   {len(alle_varianten)} varianten "
          f"({sum(1 for v in alle_varianten if v['sku_bron'] == 'leverancier')} "
          f"met Kala's artikelnummer, "
          f"{sum(1 for v in alle_varianten if v['sku_bron'] == 'toegekend')} "
          f"met een toegekende SKU)")
    if met_teksten:
        met = sum(1 for p in producten if plat(p["beschrijving"]))
        print(f"   TEKST    {met} van {len(producten)} producten met beschrijving")
    verdeling = {}
    for v in alle_varianten:
        verdeling[v["voorraad"]] = verdeling.get(v["voorraad"], 0) + 1
    print("   VOORRAAD " + ", ".join(f"{n}x {k}" for k, n in sorted(verdeling.items())))
    print(f"   PRIJS    basis '{PRIJS_BASIS}'"
          + (f", factor {PRIJS_FACTOR}" if PRIJS_FACTOR != 1.0 else "")
          + f"  ({sum(1 for v in alle_varianten if v['in_actie'])} varianten "
          f"staan vandaag in de actie)")
    cos = [p["titel"] for p in producten if p["cosmetica"]]
    if cos:
        print(f"   LET OP   {len(cos)} cosmetica (ander BTW-tarief dan een "
              f"supplement): {', '.join(cos)}")
    print()
    return producten
