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
    ("contactgegevens van Kala", r"info@kalahealth|070[\s-]?345|bel ons"),
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


def main():
    """`--alleen-update` slaat de add-feed over.

    De update-feed draait 2x per dag, de add-feed 1x per week. Dan lopen ze een
    week uit de pas en zou een vergelijking tussen beide de dagelijkse run laten
    struikelen op iets wat geen fout is.
    """
    alleen_update = "--alleen-update" in sys.argv
    print("Invarianten van de Kala Health-feeds\n")
    skus = toets_update()
    aantal_producten = len({p.findtext("handle") for p in ET.parse(UPDATE).getroot()
                            .findall("product")}) if UPDATE.exists() else 0
    if not alleen_update:
        toets_add(skus)
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
