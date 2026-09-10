#!/usr/bin/env python3
"""Releve de TOUT ce qui est sorti sur @love_kitchen97, et des repetitions.

POURQUOI
--------
Le 2026-09-09, j'ai propose a l'utilisateur trois videos a publier a la main.
Les trois rejouaient un plat deja sorti - il l'a vu, pas moi. Le pipeline
compare un nouveau script aux AUTRES SCRIPTS ; personne n'avait jamais mis
face a face les 49 scripts ecrits et les videos reellement en ligne.

CE QUE FAIT CE SCRIPT
---------------------
1. Lit les 49 scripts de `channels/love_kitchen/scripts/`.
2. Lit chez Zernio ce que la chaine porte VRAIMENT (endpoint /analytics :
   il voit aussi les videos publiees a la main, la file peut mentir).
3. Rapproche chaque publication de son script, par le TITRE du fichier et le
   TEXTE du script - pas par la legende seule, qui reprend la meme formule
   partout.
4. Range les scripts par FAMILLE DE PLAT et sort les repetitions.

Usage :
    python scripts/inventaire_lovekitchen.py
    python scripts/inventaire_lovekitchen.py --familles   # detail par famille
"""

import argparse
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
CLE_NUMERO = 2

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")


# Familles de plat. L'ordre compte : le premier motif trouve dans le nom du
# script OU dans son texte donne la famille. Les preparations sucrees passent
# avant les ingredients, sinon "gateau aux fraises" tombe dans "fraise".
FAMILLES = [
    ("cheesecake",      ["cheesecake"]),
    ("brioche/roules",  ["roule", "brioche", "cannelle", "viennoiserie"]),
    ("gateau",          ["gateau", "cookie", "muffin", "napoleon", "feuillet",
                         "jiggly", "sanscuisson"]),
    ("glace",           ["glace", "oreo"]),
    ("tarte",           ["tarte"]),
    ("pizza",           ["pizza"]),
    ("tortillas",       ["tortilla"]),
    ("ailes de poulet", ["ailespoulet", "ailes de poulet", "ailes"]),
    ("poulet",          ["poulet", "tenders"]),
    ("thon",            ["thon"]),
    ("saumon",          ["saumon"]),
    ("boeuf/viande",    ["boeuf", "viande", "bacon", "patatesboeuf"]),
    ("courgette",       ["courgette"]),
    ("aubergine",       ["aubergine"]),
    ("pomme de terre",  ["pommesdeterre", "pommes de terre", "patates",
                         "dauphinois", "gratinpommes"]),
    ("pates",           ["pates", "pate ", "onepot", "orzotto"]),
    ("salade",          ["salade", "avocat", "poischiche"]),
    ("tomate",          ["tomate"]),
    ("chocolat/nutella", ["nutella", "chocolat", "choco"]),
    ("fraise",          ["fraise"]),
    ("citron",          ["citron"]),
]


def sans_accents(t):
    return "".join(c for c in unicodedata.normalize("NFD", t.lower())
                   if unicodedata.category(c) != "Mn")


def famille(nom_script, texte):
    """Famille de plat, deduite du nom de fichier puis du texte."""
    n = sans_accents(nom_script)
    for nom, motifs in FAMILLES:
        if any(m in n for m in motifs):
            return nom
    t = sans_accents(texte)[:400]
    for nom, motifs in FAMILLES:
        if any(m in t for m in motifs):
            return nom
    return "autre"


def mots(t):
    t = sans_accents(t)
    t = re.sub(r"[^a-z ]", " ", t)
    return {m for m in t.split() if len(m) > 3}


VIDES = mots("""mari belle mere soeur avait disait croyait jure jamais plus rien "
etait sont une des les leur nous vous cetait cest quil quelle mavait quon fait
faire cuit cuire mange mangeait toujours bien tres trop deja encore comme tout
tous toute toutes donc mais alors quand parce dans avec pour sans cette cela
recette recettefacile cuisinemaison storytime couplegoals pourtoi minutes
""")


def charger_scripts():
    out = []
    for f in sorted(SCRIPTS.glob("*.txt")):
        m = re.match(r"(\d+)_(.+)\.txt$", f.name)
        if not m:
            continue
        texte = f.read_text(encoding="utf-8", errors="replace")
        out.append({
            "n": int(m.group(1)),
            "plat": m.group(2),
            "fichier": f.name,
            "texte": texte,
            "famille": famille(f.name, texte),
            "mots": (mots(m.group(2)) | mots(texte[:600])) - VIDES,
        })
    return out


def publiees():
    import programmer_avance as pa
    import controle_repetition as cr
    pa.precharger_cles()
    return cr.legendes_publiees(PSEUDO, pa.cle_zernio(CLE_NUMERO)), cr


def rapprocher(pubs, scripts):
    """Associe chaque publication au script dont le plat correspond."""
    liens = []
    for date, legende in pubs:
        mp = mots(legende) - VIDES
        best, score = None, 0.0
        for s in scripts:
            if not mp:
                continue
            r = len(mp & s["mots"]) / len(mp)
            if r > score:
                best, score = s, r
        liens.append((date, legende, best if score >= 0.12 else None, score))
    return liens


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--familles", action="store_true")
    a = ap.parse_args()

    scripts = charger_scripts()
    pubs, cr = publiees()
    liens = rapprocher(pubs, scripts)

    sortis = {}
    for date, legende, s, score in liens:
        if s:
            sortis.setdefault(s["n"], []).append(date)

    print("=" * 78)
    print("RELEVE @love_kitchen97 — %d scripts ecrits, %d videos en ligne"
          % (len(scripts), len(pubs)))
    print("=" * 78)

    par_famille = {}
    for s in scripts:
        par_famille.setdefault(s["famille"], []).append(s)

    print("\n%-18s %4s  %s" % ("FAMILLE", "n", "scripts (date de sortie)"))
    print("-" * 78)
    repetitions = []
    for fam in sorted(par_famille, key=lambda f: -len(par_famille[f])):
        groupe = par_famille[fam]
        publies = [s for s in groupe if s["n"] in sortis]
        detail = []
        for s in sorted(groupe, key=lambda x: x["n"]):
            d = sortis.get(s["n"])
            detail.append("%02d %s%s" % (s["n"], s["plat"],
                                         " [%s]" % d[0][5:] if d else " [-]"))
        print("%-18s %4d  %s" % (fam, len(groupe), ", ".join(detail)[:120]))
        if len(publies) >= 2:
            repetitions.append((fam, publies, groupe))

    print("\n" + "=" * 78)
    print("REPETITIONS DEJA SORTIES — %d familles publiees plusieurs fois"
          % len(repetitions))
    print("=" * 78)
    for fam, publies, groupe in repetitions:
        print("\n%s : %d fois" % (fam.upper(), len(publies)))
        for s in sorted(publies, key=lambda x: sortis[x["n"]][0]):
            print("   %s   %02d_%s" % (sortis[s["n"]][0], s["n"], s["plat"]))
        jamais = [s for s in groupe if s["n"] not in sortis]
        if jamais:
            print("   a venir dans la meme famille : %s"
                  % ", ".join("%02d_%s" % (s["n"], s["plat"]) for s in jamais))

    print("\n" + "=" * 78)
    print("SCRIPTS JAMAIS SORTIS")
    print("=" * 78)
    for s in sorted(scripts, key=lambda x: x["n"]):
        if s["n"] in sortis:
            continue
        deja = [t for t in scripts
                if t["famille"] == s["famille"] and t["n"] in sortis]
        marque = ("  <-- famille deja sortie %s"
                  % ", ".join(sortis[t["n"]][0] for t in deja)) if deja else "  neuf"
        print("  %02d %-24s %-18s%s" % (s["n"], s["plat"], s["famille"], marque))

    non_rapprochees = [(d, l) for d, l, s, sc in liens if s is None]
    if non_rapprochees:
        print("\n%d publication(s) sans script identifie :" % len(non_rapprochees))
        for d, l in non_rapprochees:
            print("   %s  %s" % (d, l.split("\n")[0][:60]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
