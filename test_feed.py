"""
Invarianten van de twee Kala-feeds
==================================
Geen gedrukt getal zonder een test die zijn betekenis vastpint. Draai deze test
na elke scraper-run:

    python test_feed.py                # beide feeds
    python test_feed.py --alleen-update

Hij kijkt niet of de scraper "werkt", maar of de XML betekent wat het etiket
zegt: elke regel een SKU die nooit verschuift, een prijs boven nul, geen
dubbelen, geen restanten van Kala's winkelpraat in de teksten, en de optelsom
feed + overgeslagen = de hele catalogus.
"""

import csv
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HIER = Path(__file__).parent
UPDATE = HIER / "kala_feed.xml"
ADD = HIER / "kala_add_feed.xml"
PLAT = HIER / "kala_add_feed_plat.xml"
OVERGESLAGEN = HIER / "kala_overgeslagen.csv"
GESCHRAPT = HIER / "kala_geschrapt.csv"
BRON = HIER / "kala_tekstbron.csv"
SKU_MAP = HIER / "kala_sku_map.json"

# De catalogus van kalahealth.nl op de dag van bouwen (2026-08-31): 81
# productpagina's, 163 varianten. Wijkt een run hier ver vanaf, dan is er iets
# veranderd aan de winkel of aan de scraper — en dat wil je weten vóórdat Stock
# Sync ermee aan de haal gaat.
VERWACHT_MINIMAAL = 120
VERWACHT_MAXIMAAL = 240

# Wat na het schoonmaken nooit meer in een beschrijving mag staan. Dit is de
# test onder `weer_winkelpraat`: valt hij om, dan staat het telefoonnummer van
# de leverancier op onze productpagina.
VERBODEN_IN_TEKST = [
    # Het nummer op de staart toetsen: Kala schrijft het ook als
    # "(+31) (0)70 345-0290", en daar liep het oude patroon "070-345" langs.
    ("contactgegevens van Kala",
     r"info@kalahealth|345[\s\-–]*0290|070[\s-]?345|\(0\)\s?70|bel ons|"
     r"bereikbaar op|bellen naar"),
    ("verwijzing naar Kala's site", r"kalahealth\.nl"),
    ("WPBakery-shortcode", r"\[/?vc_"),
    ("kruisverkoop", r"lees meer over|ook interessant"),
    ("winkelpraat over Kala zelf", r"over kala health|waarom koop je bij kala"),
    ("script of stijl", r"<script|<style|style=|class=|<iframe|<form"),
    ("link of afbeelding", r"<a\s|href=|<img"),
]

fouten = []


def eis(voorwaarde, boodschap):
    if not voorwaarde:
        fouten.append(boodschap)


def toets_kostprijs(el, sku, prijs):
    """De kostprijs is een aanname, en moet dus precies de aanname zijn.

    Zonder deze test is `cost` een getal waarvan niemand meer weet hoe het is
    ontstaan. Hij pint de formule vast: consumentenprijs zonder BTW, maal
    (1 - marge). Klopt de uitkomst niet, dan is er iets veranderd aan het
    prijsbeleid zonder dat de betekenis van het veld is bijgewerkt.
    """
    import kala_common as kc
    kosten = (el.find("cost").text or "").strip() if el.find("cost") is not None else ""
    btw = (el.find("btw").text or "").strip() if el.find("btw") is not None else ""
    bron = ((el.find("kostprijs_bron").text or "").strip()
            if el.find("kostprijs_bron") is not None else "")
    eis(btw in ("9", "21"), f"{sku}: btw is '{btw}' (moet 9 of 21 zijn)")
    if not kc.MARGE:
        eis(kosten == "", f"{sku}: cost gevuld terwijl KALA_MARGE 0 is")
        return
    eis(bool(kosten), f"{sku}: geen kostprijs")
    if not kosten or btw not in ("9", "21"):
        return
    verwacht = round(prijs / (1 + int(btw) / 100) * (1 - kc.MARGE), 2)
    eis(abs(float(kosten) - verwacht) < 0.005,
        f"{sku}: cost {kosten} is niet {verwacht} "
        f"(prijs {prijs} zonder {btw}% BTW, marge {kc.MARGE})")
    eis(0 < float(kosten) < prijs,
        f"{sku}: cost {kosten} ligt niet tussen 0 en de verkoopprijs {prijs}")
    eis("aanname" in bron.lower(),
        f"{sku}: kostprijs_bron zegt niet dat het een aanname is ('{bron}')")


def lees(pad):
    if not pad.exists():
        fouten.append(f"{pad.name} bestaat niet — draai eerst de scraper")
        return None
    return ET.parse(pad).getroot()


def tekst(el, tag):
    kind = el.find(tag)
    return (kind.text or "").strip() if kind is not None else ""


def toets_sku(sku, waar):
    """Een SKU is Kala's artikelnummer of een van ons — nooit iets anders.

    Deze test is de reden dat "SKU Voorraad" (letterlijk de inhoud van een
    SKU-veld bij Kala) nooit als artikelnummer in Shopify belandt.
    """
    eis(bool(sku), f"{waar}: regel zonder SKU")
    if not sku:
        return
    eis(bool(re.fullmatch(r"\d{6,}(?:-[0-9A-Za-z]+)*|KALA-\d+", sku)),
        f"{waar}: '{sku}' is geen artikelnummer van Kala en geen KALA-<id>")


def toets_update():
    root = lees(UPDATE)
    if root is None:
        return []
    regels = root.findall("product")
    eis(regels, "update-feed is leeg")
    skus, handles = [], set()
    for r in regels:
        sku = tekst(r, "sku")
        toets_sku(sku, "update-feed")
        prijs = tekst(r, "price")
        eis(prijs and float(prijs) > 0, f"{sku}: prijs is '{prijs}' (moet > 0)")
        eis(tekst(r, "available") in ("true", "false"),
            f"{sku}: available is '{tekst(r, 'available')}'")
        eis(tekst(r, "voorraad") in ("op-voorraad", "nabestelling", "uitverkocht"),
            f"{sku}: voorraad is '{tekst(r, 'voorraad')}'")
        eis(tekst(r, "sku_bron") in ("leverancier", "toegekend"),
            f"{sku}: sku_bron is '{tekst(r, 'sku_bron')}'")
        eis(tekst(r, "handle").startswith("kala-health-"),
            f"{sku}: handle '{tekst(r, 'handle')}' mist het voorvoegsel "
            f"kala-health- (dan kan Stock Sync hem in andermans product schuiven)")
        eis(tekst(r, "option1"), f"{sku}: geen option1 — varianten botsen dan")
        toets_kostprijs(r, sku, float(prijs or 0))
        eis(r.find("description") is None,
            f"{sku}: de update-feed hoort geen beschrijving te bevatten")
        skus.append(sku)
        handles.add(tekst(r, "handle"))
    eis(len(skus) == len(set(skus)),
        f"dubbele SKU in de update-feed: "
        f"{sorted({s for s in skus if skus.count(s) > 1})}")
    eis(VERWACHT_MINIMAAL <= len(skus) <= VERWACHT_MAXIMAAL,
        f"update-feed heeft {len(skus)} varianten, verwacht tussen "
        f"{VERWACHT_MINIMAAL} en {VERWACHT_MAXIMAAL}")
    print(f"   update-feed: {len(skus)} varianten over {len(handles)} producten")
    return skus


def toets_add(update_skus):
    root = lees(ADD)
    if root is None:
        return
    producten = root.findall("product")
    eis(producten, "add-feed is leeg")
    skus, handles = [], []
    for p in producten:
        handle = tekst(p, "handle")
        handles.append(handle)
        eis(handle.startswith("kala-health-"),
            f"{handle}: handle mist het voorvoegsel kala-health-")
        eis(tekst(p, "title"), f"{handle}: geen titel")
        eis(tekst(p, "vendor") in ("Kala Health", "The Akkermansia Company"),
            f"{handle}: vendor is '{tekst(p, 'vendor')}'")
        eis(tekst(p, "product_type"), f"{handle}: geen producttype")
        eis(tekst(p, "published") == "false",
            f"{handle}: published moet 'false' zijn (concept-only)")
        eis(p.findall("images/image"), f"{handle}: geen afbeelding")
        eis(tekst(p, "option1_name"), f"{handle}: geen naam voor optie 1")

        beschrijving = tekst(p, "description")
        eis(len(re.sub(r"<[^>]+>", " ", beschrijving)) > 500,
            f"{handle}: beschrijving is maar "
            f"{len(re.sub(r'<[^>]+>', ' ', beschrijving))} tekens")
        for naam, patroon in VERBODEN_IN_TEKST:
            m = re.search(patroon, beschrijving, re.I)
            eis(not m, f"{handle}: {naam} staat nog in de beschrijving "
                       f"({m.group(0) if m else ''})")

        varianten = p.findall("variants/variant")
        eis(varianten, f"{handle}: geen varianten")
        opties = []
        for v in varianten:
            sku = tekst(v, "sku")
            toets_sku(sku, f"add-feed {handle}")
            prijs = tekst(v, "price")
            eis(prijs and float(prijs) > 0, f"{sku}: prijs is '{prijs}' (moet > 0)")
            eis(tekst(v, "barcode") == "",
                f"{sku}: barcode gevuld — Kala voert geen EAN, dus dat kan niet")
            toets_kostprijs(v, sku, float(prijs or 0))
            skus.append(sku)
            opties.append(tekst(v, "option1"))
        eis(len(opties) == len(set(opties)),
            f"{handle}: twee varianten met dezelfde option1 {opties}")

    eis(len(handles) == len(set(handles)),
        f"dubbele handle in de add-feed: "
        f"{sorted({h for h in handles if handles.count(h) > 1})}")
    eis(len(skus) == len(set(skus)),
        f"dubbele SKU in de add-feed: "
        f"{sorted({s for s in skus if skus.count(s) > 1})}")
    if update_skus:
        eis(set(skus) == set(update_skus),
            "add-feed en update-feed bevatten niet dezelfde SKU's: "
            f"alleen in add {sorted(set(skus) - set(update_skus))[:5]}, "
            f"alleen in update {sorted(set(update_skus) - set(skus))[:5]}")
    print(f"   add-feed: {len(handles)} producten, {len(skus)} varianten")
    return skus


def toets_plat(add_skus):
    """De platte add-feed moet exact dezelfde inhoud dragen als de geneste.

    Deze test bestaat om één reden: Stock Sync leest een `<variants>` met één
    enkele `<variant>` niet als lijst, en sloeg daardoor bij de eerste import
    zes producten stil over. In de platte vorm is elke variant een eigen regel.
    Raakt die vorm ooit uit de pas met de geneste, dan mist er weer iets zonder
    dat er een foutmelding komt.
    """
    if not PLAT.exists():
        fouten.append("kala_add_feed_plat.xml ontbreekt — draai add_scraper.py")
        return
    root = ET.parse(PLAT).getroot()
    regels = root.findall("product")
    skus = [tekst(r, "sku") for r in regels]
    handles = {tekst(r, "handle") for r in regels}
    eis(len(skus) == len(set(skus)),
        f"dubbele SKU in de platte feed: "
        f"{sorted({s for s in skus if skus.count(s) > 1})}")
    for r in regels:
        eis(tekst(r, "option1"), f"{tekst(r, 'sku')}: platte regel zonder option1")
        eis(tekst(r, "handle").startswith("kala-health-"),
            f"{tekst(r, 'sku')}: handle mist het voorvoegsel")
        eis(tekst(r, "published") == "false",
            f"{tekst(r, 'sku')}: published moet 'false' zijn")
    if add_skus:
        eis(set(skus) == set(add_skus),
            "platte en geneste add-feed dragen niet dezelfde SKU's: "
            f"alleen plat {sorted(set(skus) - set(add_skus))[:5]}, "
            f"alleen genest {sorted(set(add_skus) - set(skus))[:5]}")
    print(f"   platte add-feed: {len(regels)} regels over {len(handles)} producten")


def toets_sku_map(feed_skus):
    """De SKU-kaart is het geheugen: elke SKU in de feed staat erin, met dezelfde
    waarde. Verschuift er een, dan maakt Stock Sync een tweede product aan."""
    if not SKU_MAP.exists():
        fouten.append("kala_sku_map.json ontbreekt — draai eerst de scraper")
        return
    kaart = json.loads(SKU_MAP.read_text(encoding="utf-8"))
    in_kaart = {r["sku"] for r in kaart.values()}
    ontbreekt = set(feed_skus) - in_kaart
    eis(not ontbreekt, f"SKU's in de feed maar niet in kala_sku_map.json: "
                       f"{sorted(ontbreekt)[:5]}")
    eis(all(r["bron"] in ("leverancier", "toegekend") for r in kaart.values()),
        "onbekende bron in kala_sku_map.json")
    toegekend = sum(1 for r in kaart.values() if r["bron"] == "toegekend")
    print(f"   sku-kaart: {len(kaart)} varianten onthouden, "
          f"{toegekend} met een toegekende SKU")


def toets_verantwoording(aantal_producten, met_teksten=True):
    """De zeef en de opschoning mogen niets stil weglaten."""
    if not OVERGESLAGEN.exists():
        fouten.append("kala_overgeslagen.csv ontbreekt")
        return
    rijen = list(csv.DictReader(OVERGESLAGEN.open(encoding="utf-8")))
    eis(all(r["reden"] for r in rijen),
        "een overgeslagen product zonder reden in de CSV")
    print(f"   {aantal_producten} producten in de feed + {len(rijen)} overgeslagen "
          f"= {aantal_producten + len(rijen)} verantwoord")

    if not met_teksten:
        return
    if not GESCHRAPT.exists():
        fouten.append("kala_geschrapt.csv ontbreekt — draai add_scraper.py")
        return
    geschrapt = list(csv.DictReader(GESCHRAPT.open(encoding="utf-8")))
    eis(all(r["reden"] and r["fragment"] for r in geschrapt),
        "een geschrapt fragment zonder reden of zonder tekst")
    print(f"   {len(geschrapt)} stukken uit de teksten geschrapt, allemaal met reden")

    if BRON.exists():
        bron = list(csv.DictReader(BRON.open(encoding="utf-8")))
        eis(len(bron) == aantal_producten,
            f"tekstbron-CSV heeft {len(bron)} regels, add-feed {aantal_producten}")
        mager = [r["titel"] for r in bron if int(r["tekens_tekst"]) < 1000]
        eis(not mager, f"producten met minder dan 1000 tekens tekst: {mager}")


def toets_dubbele_sku_regel():
    """Pint vast wat `vind_dubbele_skus` betekent: bij twee pagina's met
    hetzelfde Kala-artikelnummer wijkt de jongste (hoogste id), ongeacht de
    volgorde waarin de API ze geeft; lege SKU's botsen nooit; een pagina die
    wijkt krijgt een reden die met 'dubbele SKU bij Kala' begint, zodat hij in
    kala_overgeslagen.csv onder die kop telt."""
    import kala_common as kc

    def rij(sku):
        return {"sku_leverancier": sku}

    # API-volgorde: nieuwste eerst (zo kwam Pilzmix vóór Mushroom Mix binnen).
    paren = [
        ({"id": 48693, "name": "Pilzmix BIO Pulver"}, [rij("1500914")]),
        ({"id": 48687, "name": "Mushroom Mix BIO Poeder"}, [rij("1500914")]),
        ({"id": 36158, "name": "Maitake BIO Powder"}, [rij("")]),
        ({"id": 36012, "name": "Akkermansia"}, [rij("1906030")]),
        ({"id": 500, "name": "Huid Haar Nagel"}, [rij(""), rij("1700500")]),
    ]
    wijkt = kc.vind_dubbele_skus(paren)
    eis(set(wijkt) == {48693},
        f"dubbele-SKU-regel: verwacht dat alleen id 48693 wijkt, kreeg {sorted(wijkt)}")
    reden = wijkt.get(48693, "")
    eis(reden.startswith("dubbele SKU bij Kala — 1500914"),
        f"dubbele-SKU-regel: reden begint niet met de vaste kop: '{reden}'")
    eis("Mushroom Mix BIO Poeder" in reden and "48687" in reden,
        f"dubbele-SKU-regel: reden noemt niet wie de SKU houdt: '{reden}'")
    eis(kc.vind_dubbele_skus([]) == {}, "dubbele-SKU-regel: lege catalogus geeft iets terug")
    print("   dubbele-SKU-regel: de oudste pagina houdt de SKU, de jongste wijkt met reden")



def toets_prijsrem():
    """30-10-2026: kalahealth.nl toonde de GitHub-runner prijzen zonder btw (÷1,09) en Stock Sync zette
    ze zo in de winkel. De prijsrem (kala_common.prijsrem) moet dat herkennen en terugrekenen, een losse
    rare prijs vasthouden, en een gewone kleine wijziging doorlaten. Eerst op de echte feeds van 25-10
    (incl. btw) en 27-10 (zonder btw), dan op kleine gevallen."""
    import subprocess
    import tempfile
    import kala_common as kc

    def producten_uit(xml_tekst):
        root = ET.fromstring(xml_tekst)
        uit = {}
        for el in root.findall("product"):
            uit.setdefault(el.findtext("handle"), {"titel": el.findtext("title"), "varianten": []})
            uit[el.findtext("handle")]["varianten"].append({
                "sku": el.findtext("sku"), "prijs": float(el.findtext("price")), "btw": int(el.findtext("btw") or 9),
                "kostprijs": 0.0, "available": el.findtext("available") == "true"})
        return list(uit.values())

    def git_xml(ref):
        return subprocess.run(["git", "show", f"{ref}:kala_feed.xml"], capture_output=True, text=True,
                              encoding="utf-8", cwd=HIER).stdout

    goed, zonder_btw = git_xml("48616a6"), git_xml("8c5b3c2")
    if goed.startswith("<?xml") and zonder_btw.startswith("<?xml"):
        with tempfile.TemporaryDirectory() as d:
            vorig = Path(d) / "vorig.xml"
            vorig.write_text(goed, encoding="utf-8")
            producten = producten_uit(zonder_btw)
            r = kc.prijsrem(producten, str(vorig))
            eis(r["vergeleken"] >= 150 and r["btw_eraf"] >= 150 and not r["vastgehouden"],
                f"prijsrem herkende de btw-loze feed van 27-10 niet: {r['btw_eraf']} van {r['vergeleken']}")
            per_sku = {v["sku"]: v["prijs"] for p in producten for v in p["varianten"]}
            eis(per_sku.get("1800605") == 49.95 and per_sku.get("1310060") == 19.95,
                f"terugrekenen klopt niet: {per_sku.get('1800605')} / {per_sku.get('1310060')}")
            # andersom (de bron toont weer incl. na een btw-loze feed): nieuwe prijzen gaan gewoon door
            vorig.write_text(zonder_btw, encoding="utf-8")
            producten = producten_uit(goed)
            r = kc.prijsrem(producten, str(vorig))
            eis(r["btw_terug"] >= 150 and not r["vastgehouden"] and not r["btw_eraf"],
                f"btw terug niet herkend: {r['btw_terug']}, vastgehouden {len(r['vastgehouden'])}")
    else:
        print("   (git-historie niet beschikbaar: alleen de kleine gevallen)")

    # kleine gevallen: 10 regels, één rare prijs, één kleine wijziging, één losse ÷1,09 zonder systeem
    with tempfile.TemporaryDirectory() as d:
        vorig = Path(d) / "vorig.xml"
        vorig.write_text('<?xml version="1.0"?><products>' + "".join(
            f"<product><sku>s{i}</sku><handle>h{i}</handle><title>T{i}</title><price>20.00</price><btw>9</btw>"
            f"<available>true</available></product>" for i in range(10)) + "</products>", encoding="utf-8")
        producten = [{"titel": f"T{i}", "varianten": [{"sku": f"s{i}", "prijs": 20.00, "btw": 9, "kostprijs": 0.0}]}
                     for i in range(10)]
        producten[0]["varianten"][0]["prijs"] = 20.40      # +2%: gewoon
        producten[1]["varianten"][0]["prijs"] = 12.00      # -40%: vasthouden
        producten[2]["varianten"][0]["prijs"] = 18.35      # ÷1,09 maar als enige: vasthouden (geen systeem)
        producten.append({"titel": "Nieuw", "varianten": [{"sku": "s99", "prijs": 5.00, "btw": 9, "kostprijs": 0.0}]})
        r = kc.prijsrem(producten, str(vorig))
        eis(r["ok"] == 8 and r["vergeleken"] == 10 and r["nieuw_zonder_vorige"] == 1, f"telling prijsrem: {r}")
        eis(sorted(r["vastgehouden"]) == ["s1", "s2"], f"vastgehouden: {r['vastgehouden']}")
        prijzen = {v["sku"]: v["prijs"] for p in producten for v in p["varianten"]}
        eis(prijzen["s0"] == 20.40 and prijzen["s1"] == 20.00 and prijzen["s2"] == 20.00 and prijzen["s99"] == 5.00,
            f"prijzen na de rem: {prijzen}")
        eis("VASTGEHOUDEN: 2" in kc.prijsrem_verslag(r), "verslag noemt de vastgehouden prijzen niet")
    print("   prijsrem: btw-loze feed teruggerekend, rare prijs vastgehouden, kleine wijziging doorgelaten")

def main():
    """`--alleen-update` slaat de add-feed over.

    De update-feed draait 2x per dag, de add-feed 1x per week. Dan lopen ze een
    week uit de pas en zou een vergelijking tussen beide de dagelijkse run laten
    struikelen op iets wat geen fout is.
    """
    alleen_update = "--alleen-update" in sys.argv
    print("Invarianten van de Kala Health-feeds\n")
    toets_dubbele_sku_regel()
    toets_prijsrem()
    skus = toets_update()
    aantal_producten = len({p.findtext("handle") for p in ET.parse(UPDATE).getroot()
                            .findall("product")}) if UPDATE.exists() else 0
    add_skus = []
    if not alleen_update:
        add_skus = toets_add(skus) or []
        toets_plat(add_skus)
        if ADD.exists():
            aantal_producten = len(ET.parse(ADD).getroot().findall("product"))
    toets_sku_map(skus)
    toets_verantwoording(aantal_producten, met_teksten=not alleen_update)

    if fouten:
        print(f"\n{len(fouten)} probleem/problemen:")
        for f in fouten:
            print(f"  - {f}")
        sys.exit(1)
    print(f"\nAlles klopt: {len(skus)} varianten, geen dubbelen, geen prijs 0, "
          f"geen winkelpraat van Kala in de teksten.")


if __name__ == "__main__":
    main()
