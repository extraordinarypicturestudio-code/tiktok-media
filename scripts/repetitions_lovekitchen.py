#!/usr/bin/env python3
"""Pour chaque script JAMAIS SORTI, dit si SON PLAT est deja en ligne.

Complete `inventaire_lovekitchen.py`, qui raisonne par FAMILLE. Une famille
publiee trois fois en trois semaines n'est pas un defaut : le sucre est ce qui
marche sur cette chaine. Ce qui est un defaut, c'est le MEME PLAT.

Le signal le plus net est le hashtag specifique des videos en ligne : il nomme
le plat sans detour (#ailesdepoulet, #gratindethon, #pouletfrit). On le
compare au nom du script et a son texte.

Usage :
    python scripts/repetitions_lovekitchen.py
"""

import datetime
import json
import pathlib
import re
import sys
import unicodedata

ICI = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ICI))

PROJET = pathlib.Path(r"C:/Users/tcheb/Desktop/Projets Claude/Project 1 TIKTOK")
SCRIPTS = PROJET / "channels" / "love_kitchen" / "scripts"
PSEUDO = "love_kitchen97"

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

# Hashtags qui decrivent le format, pas le plat.
GENERIQUES = {
    "cuisinemaison", "storytime", "couplegoals", "pourtoi", "foryou", "fyp",
    "recettefacile", "recette", "cuisine", "dessertfacile", "patisseriemaison",
    "food", "asmr", "faitmaison", "gourmandise", "recettes", "tiktokfood",
    "vendredisoir", "mardisoir", "lundisoir", "samedisoir", "dimanchesoir",
    "diner", "repas", "gouter", "petitdejeuner", "facile", "rapide", "maison",
}


def plat(t):
    return "".join(c for c in unicodedata.normalize("NFD", t.lower())
                   if unicodedata.category(c) != "Mn")


def morceaux(mot):
    """Sous-mots utiles d'un hashtag colle : gratindethon -> gratin, thon."""
    base = ["gratin", "tarte", "gateau", "roule", "brioche", "cheesecake",
            "muffin", "cookie", "glace", "pizza", "tortilla", "salade",
            "poulet", "ailes", "tenders", "thon", "saumon", "boeuf", "viande",
            "bacon", "courgette", "aubergine", "pomme", "patate", "dauphinois",
            "pates", "pasta", "fraise", "citron", "chocolat", "nutella",
            "cannelle", "tomate", "avocat", "peche", "oreo", "napoleon",
            "feuillet", "frit", "sanscuisson", "onepot", "orzotto", "biscuit",
            "carotte", "champignon", "poischiche", "creme", "miel"]
    return {b for b in base if b in mot}


def signature(texte, avec_hashtags=True):
    """Mots de plat presents dans ce texte."""
    t = plat(texte)
    s = set()
    if avec_hashtags:
        for h in re.findall(r"#(\w+)", t):
            if h in GENERIQUES:
                continue
            s |= morceaux(h)
            if not morceaux(h):
                s.add(h)
    s |= morceaux(t)
    return s


def main():
    import programmer_avance as pa
    import controle_repetition as cr
    pa.precharger_cles()
    pubs = cr.legendes_publiees(PSEUDO, pa.cle_zernio(2))

    # Ce que la chaine porte deja, plat par plat.
    en_ligne = []
    for date, legende in pubs:
        en_ligne.append((date, signature(legende), legende.split("\n")[0][:46]))

    deja = set()
    for _, s, _ in en_ligne:
        deja |= s

    scripts = []
    for f in sorted(SCRIPTS.glob("*.txt")):
        m = re.match(r"(\d+)_(.+)\.txt$", f.name)
        if not m:
            continue
        texte = f.read_text(encoding="utf-8", errors="replace")
        # Date d'ecriture : une video ne peut pas etre sortie AVANT que son
        # script existe. Sans cette borne, l'appariement forcait les scripts
        # de septembre sur des publications d'aout du meme plat - et faisait
        # passer pour "deja sorties" des videos qui ne le sont pas.
        ecrit = datetime.date.fromtimestamp(f.stat().st_mtime).isoformat()
        scripts.append((int(m.group(1)), m.group(2), f.name,
                        signature(m.group(2), False) | signature(texte[:500], False),
                        ecrit))

    print("=" * 88)
    print("@love_kitchen97 : %d videos en ligne, %d scripts ecrits"
          % (len(pubs), len(scripts)))
    print("Plats deja a l'antenne : %s" % ", ".join(sorted(deja)))
    print("=" * 88)

    # APPARIEMENT, pas seuil. Il y a 30 publications et 49 scripts : chaque
    # publication vient d'UN script et d'un seul. Un simple seuil laissait
    # 03_thon et 09_cookies dans les "jamais sortis" alors qu'ils sont en
    # ligne - leur script cite des ingredients que la legende ne reprend pas.
    #
    # On classe donc toutes les paires par score et on attribue au plus offrant,
    # une publication et un script ne servant qu'une fois.
    paires = []
    for i, (n, nom, fichier, sig, ecrit) in enumerate(scripts):
        for j, (date, s, legende) in enumerate(en_ligne):
            if not sig or not s or date < ecrit:
                continue
            # Jaccard : penalise autant un script trop large qu'une legende
            # trop bavarde. Le recouvrement simple faisait gagner les scripts
            # a une seule signature.
            paires.append((len(sig & s) / len(sig | s), i, j))
    paires.sort(reverse=True)

    pris_script, pris_pub, attribue = set(), set(), {}
    for score, i, j in paires:
        if score < 0.20 or i in pris_script or j in pris_pub:
            continue
        pris_script.add(i)
        pris_pub.add(j)
        attribue[i] = (score, en_ligne[j][0], en_ligne[j][2])

    sortis, restants = [], []
    for i, (n, nom, fichier, sig, ecrit) in enumerate(scripts):
        if i in attribue:
            sortis.append((n, nom, sig, attribue[i]))
            continue
        best = (0.0, "", "")
        for date, s, legende in en_ligne:
            r = len(sig & s) / len(sig) if sig else 0.0
            if r > best[0]:
                best = (r, date, legende)
        restants.append((n, nom, sig, best))

    print("\nDEJA SORTIS (%d)" % len(sortis))
    print("-" * 88)
    for n, nom, sig, (r, date, legende) in sorted(sortis):
        print("  %02d %-24s  %s  %s" % (n, nom, date, legende))

    print("\nPAS ENCORE SORTIS (%d) — recouvrement avec le plat le plus proche"
          % len(restants))
    print("-" * 88)
    a_ecarter, a_garder = [], []
    for n, nom, sig, (r, date, legende) in sorted(restants):
        verdict = "REPETITION" if r >= 0.60 else ("proche" if r >= 0.40 else "NEUF")
        (a_ecarter if r >= 0.60 else a_garder).append((n, nom))
        print("  %02d %-24s %.2f  %-10s %s  %s"
              % (n, nom, r, verdict, date or "-", legende))
        print("     plat : %s" % ", ".join(sorted(sig)))

    print("\n" + "=" * 88)
    print("A ECARTER (%d) : %s" % (len(a_ecarter),
                                   ", ".join("%02d_%s" % x for x in a_ecarter)))
    print("A GARDER  (%d) : %s" % (len(a_garder),
                                   ", ".join("%02d_%s" % x for x in a_garder)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
