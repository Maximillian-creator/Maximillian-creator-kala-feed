"""
Kala Health UPDATE-feed
=======================
Lichte feed om BESTAANDE producten bij te werken: verkoopprijs + beschikbaarheid.
Matcht in Stock Sync op SKU (Kala voert nergens een EAN).

  price     = consumentenprijs van kalahealth.nl (incl. BTW), 1-op-1
  cost      = GESCHATTE inkoopprijs excl. BTW: prijs zonder BTW x (1 - marge).
              De marge is hoorzeggen van de vertegenwoordiger, geen factuur --
              elke regel draagt dat voorbehoud mee in `kostprijs_bron`.
  available = op voorraad bij Kala; "nabestelling" telt als NIET beschikbaar
  GEEN beschrijving — die staat alleen in de add-feed, zodat een update nooit
  de eigen productteksten van Good For You overschrijft.

Bron: kalahealth.nl (publieke WooCommerce Store API). Zie kala_common.py.
Lokaal: INSECURE_SSL=1, TEST_SLUG=<slug>.
"""

import time
import xml.etree.ElementTree as ET
from datetime import date
from xml.dom import minidom

import kala_common as kc

OUTPUT_FILE = "kala_feed.xml"
FEED_URL = ("https://raw.githubusercontent.com/Maximillian-creator/Maximillian-creator-kala-feed/"
            "main/kala_feed.xml")


def add(parent, tag, waarde):
    el = ET.SubElement(parent, tag)
    el.text = "" if waarde is None else str(waarde)
    return el


def build_xml(producten):
    root = ET.Element("products")
    for p in producten:
        for v in p["varianten"]:
            item = ET.SubElement(root, "product")
            add(item, "sku", v["sku"])
            add(item, "sku_bron", v["sku_bron"])
            add(item, "barcode", v["barcode"])
            add(item, "title", p["titel"])
            add(item, "variant_title", v["variant_titel"])
            add(item, "handle", p["handle"])
            add(item, "option1", v["optie1"])
            add(item, "price", f"{v['prijs']:.2f}")
            add(item, "cost", f"{v['kostprijs']:.2f}" if v["kostprijs"] else "")
            add(item, "kostprijs_bron", v["kostprijs_bron"])
            add(item, "btw", v["btw"])
            add(item, "compare_at_price", "")     # nooit verzonnen
            add(item, "available", "true" if v["available"] else "false")
            add(item, "voorraad", v["voorraad"])
            add(item, "in_actie", "true" if v["in_actie"] else "false")
            add(item, "url", v["url"])
    return root


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
    print("Kala Health UPDATE-feed gestart\n")
    start = time.time()
    producten = kc.fetch_products(met_teksten=False, datum=date.today().isoformat())
    regels = sum(len(p["varianten"]) for p in producten)
    kc.controleer_omvang(regels, OUTPUT_FILE)
    save_xml(build_xml(producten), OUTPUT_FILE)
    print(f"Klaar in {time.time() - start:.0f}s — {len(producten)} producten, "
          f"{regels} varianten")
    print(f"\nFeed-URL voor Stock Sync (Update):\n{FEED_URL}")


if __name__ == "__main__":
    main()
