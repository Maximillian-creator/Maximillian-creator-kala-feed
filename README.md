# Kala Health — leveranciersfeed

Twee feeds uit **kalahealth.nl** (publieke WooCommerce-winkel, geen login):

| Feed | Bestand | Wat erin zit | Hoe vaak |
|---|---|---|---|
| **Update** | `kala_feed.xml` | SKU, prijs, beschikbaarheid van bestaande producten | 2× per dag (07:00 + 19:00 UTC) |
| **Add** | `kala_add_feed.xml` | álle productinfo om nieuwe producten aan te maken | wekelijks (ma 04:00 UTC) |

Feed-URL's voor Stock Sync (na het pushen naar GitHub):

```
https://raw.githubusercontent.com/Maximillian-creator/Maximillian-creator-kala-feed/main/kala_feed.xml
https://raw.githubusercontent.com/Maximillian-creator/Maximillian-creator-kala-feed/main/kala_add_feed.xml
```

**De repo moet publiek zijn**, anders kan Stock Sync de bestanden niet ophalen.

## De catalogus in cijfers (31-08-2026)

| | |
|---|---|
| productpagina's bij Kala | 81 |
| verkoopbare varianten | 163 |
| met Kala's eigen artikelnummer | 129 |
| met een door ons toegekende SKU | 34 |
| op voorraad / nabestelling / uitverkocht | 159 / 3 / 1 |
| producten met een beschrijving | 81 van 81 |
| overgeslagen | 0 |

Die getallen worden bij elke run opnieuw geteld en door `test_feed.py` vastgepind.

## Vier dingen die je moet weten

### 1. Een vijfde van de varianten heeft geen SKU bij Kala

33 van de 163 varianten hebben een leeg SKU-veld — niet alleen bundels, ook
Magnesium Bisglycinaat Capsules, Chaga Bio, Liposomale Curcumine, Rhodiola en
Creatine. Eén variant draagt letterlijk de tekst `SKU Voorraad` als artikelnummer.

Stock Sync matcht op SKU. Zonder SKU kun je een product wel aanmaken maar nooit
meer bijwerken. Daarom krijgt zo'n variant een SKU van ons: **`KALA-<variant-id>`**,
met het WooCommerce-variant-id van Kala als sleutel.

`kala_sku_map.json` is het geheugen: **een SKU die eenmaal in de feed heeft
gestaan verandert nooit meer.** Vult Kala later alsnog een artikelnummer in, dan
meldt de run dat, maar de feed blijft de oude SKU gebruiken — hernummeren is
handwerk in Shopify, geen bijwerking van een feed. Dat bestand hoort dus in de
repo en mag niet worden weggegooid.

Het veld `sku_bron` in beide feeds zegt per regel `leverancier` of `toegekend`.

### 2. De handles krijgen `kala-health-` ervoor

Kala verkoopt producten met doodgewone namen: Probiotica, Astaxanthine,
Magnesium Complex, Vegan Omega 3. Die handles **bestaan al** in de Good For You
winkel, van andere merken (gecontroleerd op 31-08-2026). Met de Stock
Sync-instelling *"varianten samenvoegen in bestaande producten"* aan zouden
Kala-varianten in andermans product schuiven. Het voorvoegsel maakt dat
onmogelijk. Kala's eigen slug staat als `leverancier_handle` in de add-feed.

### 3. Geen EAN, en geen voorraadaantallen

Kala voert nergens een EAN/GTIN — niet in de API, niet in schema.org, niet in de
HTML. `barcode` blijft dus leeg: **Stock Sync moet op SKU matchen, niet op
barcode.** Voor Merchant Center betekent dat later `identifier_exists: no`, of
zelf EAN's aanvullen.

Voorraad is bij Kala alleen in/uit/nabestelling, geen aantal. De feed verzint er
dus geen: hij levert `available` (true/false) plus `voorraad`
(`op-voorraad` / `nabestelling` / `uitverkocht`). **"Beschikbaar via
nabestelling" telt als niet beschikbaar** — Kala kan dan zelf niet direct
leveren.

### 4. De teksten zijn van Kala en moeten nog langs Themis

`published` staat in de add-feed **hard op false**. De beschrijvingen zijn
letterlijk die van kalahealth.nl en zitten vol claims: `themis_check.py` gaf op
31-08-2026 **0× ok, 13× let op, 68× afkeuren** (`kala_themis.md`). Er gaat niets
zichtbaar naar Google of de klant voordat die teksten zijn herschreven.

Wat er wél automatisch uit gaat — geteld en per fragment vastgelegd in
`kala_geschrapt.csv`, want stil wegfilteren mag niet:

| reden | wat | aantal |
|---|---|---|
| shortcode | `[/vc_column_text]` van WPBakery, zichtbaar als tekst | 540 |
| contact | "Bel ons op (+31) 070-345-0290 of mail naar info@kalahealth.nl" | 116 |
| eigen winkel | "te bekijken in de productgalerij op deze pagina" | 36 |
| kruisverkoop | "Bekijk onze eigen Probiotica", "→ Lees meer over CM Crème" | 21 |
| auteur | de byline van Kala's eigen wetenschapsredacteur | 14 |
| sectie | "Over Kala Health", "Waarom koop je bij Kala Health?" | 13 |

Wat blijft staan: zinnen als "geproduceerd in onze eigen FSSC 22000-faciliteit".
Dat is productinformatie in Kala's stem; wat daarmee moet, beslissen Themis en
Max — niet deze zeef.

## Prijs

`price` = de consumentenprijs van kalahealth.nl, **incl. BTW, 1-op-1** (besluit
Max, 31-08-2026) — hetzelfde model als Goldea en Energetica Natura. Er wordt
nooit een `compare_at_price` verzonnen.

Op 31-08-2026 stonden 8 varianten in de actie (Magnesium Bisglycinaat 100 mg,
Liposomaal B-complex, Creatine, Gebufferde Vitamine C Poeder). Met `advies` komt
de normale prijs mee, niet de tijdelijke.

### De kostprijs is een aanname, geen gemeten getal

Kala publiceert nergens inkoopprijzen en heeft geen inkoopportaal. Max,
31-08-2026: *"ik heb geen online inkoopprijzen, maar naar zeggen van de
vertegenwoordiger heb ik 50% marge."* Dat is hoorzeggen, geen factuur. Zo staat
het ook in de feed:

```
cost = (consumentenprijs / BTW-factor) x (1 - KALA_MARGE)
```

De marge wordt genomen over de prijs **zonder** BTW, zoals een leverancier hem
noemt. Voorbeeld: Liposomaal IJzer 60 caps € 29,95 incl. 9% → € 27,48 excl. →
kostprijs **€ 13,74**. Over de hele catalogus: € 9.919,95 verkoop tegenover
€ 4.540,81 geschatte inkoop.

Elke regel draagt het voorbehoud mee in het veld `kostprijs_bron`, en
`test_feed.py` pint de formule vast — zodat `cost` nooit een getal wordt waarvan
niemand meer weet hoe het is ontstaan.

> **Controleer dit bij de eerste factuur van Kala.** Wijkt het af, dan is het
> één getal in de env en een nieuwe run: `KALA_MARGE=0.45`. Wil je helemaal geen
> kostprijs in Shopify, dan `KALA_MARGE=0` — dan blijft het veld leeg.

| env | standaard | betekenis |
|---|---|---|
| `KALA_PRIJS_BASIS` | `advies` | `advies` = reguliere prijs, `actueel` = met lopende actie |
| `KALA_PRIJS_FACTOR` | `1.0` | vermenigvuldigt de verkoopprijs |
| `KALA_MARGE` | `0.50` | inkoopmarge voor de geschatte kostprijs; `0` = geen kostprijs |

**Let op de BTW:** 3 producten zijn cosmetica, geen supplement — CM Crème (MSM),
OptiMSM® Gel en de bundel Huid, Haar & Nagel + OptiMSM® Gel (5 varianten). Daar
geldt 21% in plaats van 9%. Ze staan met de tag `cosmetica` in de feed, en het
veld `btw` zegt per regel 9 of 21. Dat veld wordt alleen gebruikt om de
kostprijs terug te rekenen; aan de verkoopprijs wordt niets op- of afgeteld.

## Stock Sync instellen

**Update-koppeling** (bestaande producten):
- Product-identificeerder: **SKU**. Barcode NIET mappen.
- Map: `price`, `available`, en desgewenst `cost` → Kostprijs per artikel (lees
  eerst het voorbehoud hierboven). **`description` staat niet in deze feed** — zo
  kan een prijs-update nooit een eigen productbeschrijving overschrijven.
- Bij "niet in de feed": **voorraad op 0 zetten, nooit archiveren of op concept.**
  Stock Sync heeft in 2026 drie keer stilletjes een hele catalogus gearchiveerd.

**Add-koppeling** (nieuwe producten):
- Parent node `products.product[*]`, variant node `variants.variant[*]`.
- Variantgroep-veld → `handle`, Variant Optie 1 → `option1`. Beide zijn
  verplicht, anders wordt elke regel een los product.
- "Varianten samenvoegen in bestaande producten" mag aan: het `kala-health-`
  voorvoegsel voorkomt botsingen.
- Zet de koppeling op **alleen nieuwe producten aanmaken**.

## Draaien en testen

```bash
pip install -r requirements.txt
python scraper.py          # update-feed  (~6 s)
python add_scraper.py      # add-feed     (~40 s, haalt 81 productpagina's op)
python test_feed.py        # invarianten  — moet groen zijn vóór een push
python themis_check.py     # claimcontrole, alleen lokaal naast gfy-themis
```

| env | waarvoor |
|---|---|
| `INSECURE_SSL=1` | lokaal achter een SSL-onderscheppende bedrijfsproxy |
| `TEST_SLUG=<slug>` | één product verwerken |
| `KALA_CACHE_DIR=.cache` | productpagina's op schijf bewaren tijdens ontwikkelen |
| `FORCE_FEED=1` | het vangnet overrulen (zie hieronder) |

**Het vangnet:** een run die 0 producten vindt, of minder dan de helft van de
vorige feed, **schrijft niets weg en stopt met een foutcode**. Dat is er omdat
een lege feed gevaarlijker is dan geen feed: Stock Sync archiveert wat er niet in
staat. Bij Vuzïmo (rebranding) stond de feed 13 dagen leeg zonder dat iemand het
zag.

## Bestanden

| | |
|---|---|
| `kala_common.py` | de gedeelde kern: ophalen, SKU-beleid, tekst schoonmaken, tellen |
| `scraper.py` | update-feed |
| `add_scraper.py` | add-feed |
| `test_feed.py` | de invarianten — draait ook in beide Actions vóór het committen |
| `themis_check.py` | claimcontrole via `gfy-themis` (alleen lokaal) |
| `kala_sku_map.json` | het SKU-geheugen — **niet weggooien** |
| `kala_geschrapt.csv` | elk stuk winkelpraat dat uit de teksten is gehaald, met reden |
| `kala_overgeslagen.csv` | elk product dat niet in de feed zit, met reden |
| `kala_tekstbron.csv` | per product: welke tabbladen, hoeveel tekst, hoeveel afbeeldingen |
| `kala_themis.md` | het claimrapport |
