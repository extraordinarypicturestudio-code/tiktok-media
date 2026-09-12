#!/usr/bin/env python3
"""Recopie vers "Project 1 TIKTOK" les fichiers dont le DEPOT est la reference.

POURQUOI
--------
Depuis le 2026-09-12, le montage love_kitchen et la barriere de mise en file
tournent sur GitHub Actions : ils ont donc du monter dans le depot. Les memes
fichiers existent toujours dans "Project 1 TIKTOK", d'ou partent les commandes
locales - et deux copies du meme code, c'est exactement ce qui a produit onze
echecs de jobs en septembre (un bloc git recopie dans quatorze workflows).

La regle est donc : **le depot est la reference**. Une correction se fait ici,
se pousse, puis se redescend en local avec ce script. Modifier la copie locale
ne change rien a ce que fera le runner, et la divergence ne se voit pas - le
montage local marche, le montage distant fait autre chose.

Ce script ne fait QUE descendre. Il refuse de remonter quoi que ce soit :
si la copie locale est plus recente, il le DIT et ne touche a rien, parce que
cela veut dire qu'on a edite du mauvais cote et qu'il y a une decision a
prendre, pas un fichier a ecraser.

Usage :
    python3 scripts/synchroniser_local.py          # montre ce qui differe
    python3 scripts/synchroniser_local.py --ecrire # recopie
"""

import argparse
import hashlib
import pathlib
import shutil
import sys

DEPOT = pathlib.Path(__file__).resolve().parent.parent
LOCAL = DEPOT.parent / "Project 1 TIKTOK"

# Chemin dans le depot -> chemin dans "Project 1 TIKTOK".
SUIVIS = {
    "pipeline/gemini_check.py": "gemini_check.py",
    "pipeline/controle_publication.py": "controle_publication.py",
    "pipeline/verification_publication.py": "verification_publication.py",
    "pipeline/mettre_en_file.py": "mettre_en_file.py",
    "pipeline/empreintes_video.py": "empreintes_video.py",
    "pipeline/generer_outros.py": "generer_outros.py",
    "channels/love_kitchen/montage_lovekitchen.py":
        "channels/love_kitchen/montage_lovekitchen.py",
}

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")


def empreinte(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12] if p.exists() else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ecrire", action="store_true",
                    help="recopier pour de vrai (sinon, montre seulement)")
    a = ap.parse_args()

    if not LOCAL.is_dir():
        print("dossier local introuvable : %s" % LOCAL)
        return 1

    differents, en_avance, ecrits = 0, 0, 0
    for rel_depot, rel_local in SUIVIS.items():
        src, dst = DEPOT / rel_depot, LOCAL / rel_local
        if not src.exists():
            print("  MANQUE dans le depot : %s" % rel_depot)
            continue
        if empreinte(src) == empreinte(dst):
            continue
        differents += 1
        if dst.exists() and dst.stat().st_mtime > src.stat().st_mtime:
            # La copie locale est plus recente : on a edite du mauvais cote.
            print("  LOCAL PLUS RECENT  %s" % rel_local)
            print("      -> edite en local alors que le depot fait reference."
                  " Reporter la correction dans le depot AVANT de synchroniser.")
            en_avance += 1
            continue
        print("  %s  %s -> %s" % ("recopie" if a.ecrire else "a recopier",
                                  rel_depot, rel_local))
        if a.ecrire:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            ecrits += 1

    if not differents:
        print("tout est aligne (%d fichiers suivis)." % len(SUIVIS))
    else:
        print("\n%d fichier(s) different(s), %d recopie(s), %d a arbitrer."
              % (differents, ecrits, en_avance))
        if not a.ecrire and differents > en_avance:
            print("Relancer avec --ecrire pour appliquer.")
    return 1 if en_avance else 0


if __name__ == "__main__":
    sys.exit(main())
