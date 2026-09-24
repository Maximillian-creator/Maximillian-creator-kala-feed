"""
Kala Health ADD-feed
====================
Volledige productinfo om met Stock Sync NIEUWE producten aan te maken.
Bron: kalahealth.nl (publieke WooCommerce Store API + de productpagina's).

  price       = consumentenprijs van kalahealth.nl (incl. BTW), 1-op-1
  cost        = GESCHATTE inkoopprijs excl. BTW (50% marge volgens de
                vertegenwoordiger, niet op factuur gecontroleerd; KALA_MARGE=0
                laat het veld leeg)
  barcode     = leeg — Kala voert nergens een EAN, Stock Sync matcht op SKU
  handle      = kala-health-<titel>, nooit een kale naam (anders schuiven
                Kala-varianten in bestaande producten van andere merken)
  description = korte beschrijving + het Beschrijving-tabblad + Samenstelling &
                ingredienten + Gebruik & dosering + Certificaten +
                Achtergrondinformatie + FAQ, letterlijk van Kala, ontdaan van
                scripts, stijlen, links en afbeeldingen
  published   = ALTIJD false. Bouwen mag, publiceren verdien je: elke tekst moet
                eerst langs Themis en langs Max. Zie themis_check.py.

Zet in Stock Sync de ADD-koppeling op "alleen nieuwe producten aanmaken".
Lokaal: INSECURE_SSL=1, TEST_SLUG=<slug>, KALA_CACHE_DIR=.cache.
"""

import csv
import time
import xml.etree.ElementTree as ET
from datetime import date
from xml.dom import minidom

import kala_common as kc

OUTPUT_FILE = "kala_add_feed.xml"
PLAT_FILE = "kala_add_feed_plat.xml"
BRON_FILE = "kala_tekstbron.csv"
FEED_URL = ("https://raw.githubusercontent.com/Maximillian-creator/Maximillian-creator-kala-feed/"
            "main/kala_add_feed.xml")
PLAT_URL = ("https://raw.githubusercontent.com/Maximillian-creator/Maximillian-creator-kala-feed/"
            "main/kala_add_feed_plat.xml")


def add(parent, tag, waarde):
    el = ET.SubElement(parent, tag)
    el.text = "" if waarde is None else str(waarde)
    return el


def build_plat_xml(producten):
    """Dezelfde inhoud, maar één <product>-regel per variant.

    Waarom deze er naast staat (24-09-2026): Stock Sync leest een `<variants>`
    met precies ÉÉN `<variant>` niet als lijst maar als los object, en slaat die
    rij dan over. Van de eerste import kwamen daardoor 75 van de 81 producten
    binnen — de zes die ontbraken waren exact de zes met één variant (CM Crème,
    Ashwagandha Extract, Plantaardige Voedingsvezels, Akkermansia Muciniphila
    Capsules en de twee van The Akkermansia Company). Er kwam geen foutmelding:
    die rijen werden nooit aangeboden.

    Plat heeft dat probleem niet, want elke variant is zijn eigen regel. Het is
    ook de vorm die de andere leveranciersfeeds gebruiken (Vitakruid: 284 regels
    over 204 handles), dus Stock Sync-instellingen zijn overal hetzelfde:
    variant-node leeg laten, variantgroep = `handle`, optie 1 = `option1`.

    De producttekst wordt per variant herhaald. Dat maakt het bestand groter,
    maar Stock Sync leest per rij en Shopify krijgt de tekst één keer per
    product binnen.
    """
    root = ET.Element("products")
    for p in producten:
        eerste = p["afbeeldingen"][0] if p["afbeeldingen"] else ""
        for v in p["varianten"]:
            item = ET.SubElement(root, "product")
            add(item, "handle", p["handle"])
            add(item, "title", p["titel"])
            add(item, "vendor", p["vendor"])
            add(item, "brand", p["vendor"])
            add(item, "product_type", p["product_type"])
            add(item, "tags", p["tags"])
            add(item, "published", "false")      # concept-only, altijd
            add(item, "description", p["beschrijving"])
            add(item, "image_links", ",".join(p["afbeeldingen"]))
            add(item, "option1_name", p["optie1_naam"])
            add(item, "leverancier_url", p["url"])
            add(item, "leverancier_handle", p["leverancier_handle"])
            add(item, "sku", v["sku"])
            add(item, "sku_bron", v["sku_bron"])
            add(item, "barcode", v["barcode"])
            add(item, "price", f"{v['prijs']:.2f}")
            add(item, "cost", f"{v['kostprijs']:.2f}" if v["kostprijs"] else "")
            add(item, "kostprijs_bron", v["kostprijs_bron"])
            add(item, "btw", v["btw"])
            add(item, "compare_at_price", "")    # nooit verzonnen
            add(item, "available", "true" if v["available"] else "false")
            add(item, "voorraad", v["voorraad"])
            add(item, "variant_title", v["variant_titel"])
            add(item, "option1", v["optie1"])
            add(item, "weight", v["gewicht"])
            add(item, "weight_unit", "g")
            add(item, "image", v["afbeelding"] or eerste)
    return root


def build_xml(producten):
    root = ET.Element("products")
    for p in producten:
        item = ET.SubElement(root, "product")
        add(item, "handle", p["handle"])
        add(item, "title", p["titel"])
        add(item, "vendor", p["vendor"])
        add(item, "brand", p["vendor"])
        add(item, "product_type", p["product_type"])
        add(item, "tags", p["tags"])
        add(item, "published", "false")          # concept-only, altijd
        add(item, "description", p["beschrijving"])
        add(item, "option1_name", p["optie1_naam"])
        add(item, "leverancier_url", p["url"])
        add(item, "leverancier_handle", p["leverancier_handle"])

        images_el = ET.SubElement(item, "images")
        for src in p["afbeeldingen"]:
            add(ET.SubElement(images_el, "image"), "src", src)
        add(item, "image_links", ",".join(p["afbeeldingen"]))
        eerste = p["afbeeldingen"][0] if p["afbeeldingen"] else ""

        variants_el = ET.SubElement(item, "variants")
        for v in p["varianten"]:
            v_el = ET.SubElement(variants_el, "variant")
            add(v_el, "sku", v["sku"])
            add(v_el, "sku_bron", v["sku_bron"])
            add(v_el, "barcode", v["barcode"])
            add(v_el, "price", f"{v['prijs']:.2f}")
            add(v_el, "cost", f"{v['kostprijs']:.2f}" if v["kostprijs"] else "")
            add(v_el, "kostprijs_bron", v["kostprijs_bron"])
            add(v_el, "btw", v["btw"])
            add(v_el, "compare_at_price", "")    # nooit verzonnen
            add(v_el, "available", "true" if v["available"] else "false")
            add(v_el, "voorraad", v["voorraad"])
            add(v_el, "variant_title", v["variant_titel"])
            add(v_el, "option1", v["optie1"])
            add(v_el, "weight", v["gewicht"])
            add(v_el, "weight_unit", "g")
            add(v_el, "image", v["afbeelding"] or eerste)
    return root


def schrijf_tekstbron(producten, pad=BRON_FILE):
    """Per product: waar de tekst vandaan komt en hoe lang hij is.

    Zodat "163 producten met beschrijving" niet iets anders betekent dan het
    zegt: een product met alleen een kop en geen inhoud staat hier met 0 tekens.
    """
    with open(pad, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["handle", "titel", "tabbladen", "tekens_tekst", "afbeeldingen"])
        for p in producten:
            w.writerow([p["handle"], p["titel"], "|".join(p["tabbladen"]),
                        len(kc.plat(p["beschrijving"])), len(p["afbeeldingen"])])
    print(f"   Tekstherkomst vastgelegd in {pad}")


def save_xml(root, filepath):
    xml_str = ET.tostring(root, encoding="unicode")
    pretty = minidom.parseString(xml_str).toprettyxml(indent="  ")
    regels = pretty.split("\n")
    if regels[0].startswith("<?xml"):
        regels[0] = '<?xml version="1.0" encoding="UTF-8"?>'
    with open(filepath, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(regels))
    print(f"\nXML opgeslagen: {filepath}")


def main():
    print("Kala Health ADD-feed gestart\n")
    start = time.time()
    producten = kc.fetch_products(met_teksten=True, datum=date.today().isoformat())
    regels = sum(len(p["varianten"]) for p in producten)
    kc.controleer_omvang(regels, OUTPUT_FILE)
    schrijf_tekstbron(producten)
    save_xml(build_xml(producten), OUTPUT_FILE)
    save_xml(build_plat_xml(producten), PLAT_FILE)
    print(f"Klaar in {time.time() - start:.0f}s — {len(producten)} producten, "
          f"{regels} varianten")
    print("\nFeed-URL voor Stock Sync (Add products):")
    print(f"  plat, 1 regel per variant (AANBEVOLEN): {PLAT_URL}")
    print(f"  genest, 1 regel per product:            {FEED_URL}")
    print("\nLet op: published staat op false. Draai `python themis_check.py` "
          "voordat er iets in Shopify op zichtbaar gaat.")


if __name__ == "__main__":
    main()
