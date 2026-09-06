#!/usr/bin/env python3
"""Retire les depots qui depassent le quota journalier d'une chaine.

Le premier depot du 2026-09-06 a ete fait avant que `programmer_avance.py`
n'applique un quota par chaine et par jour. Des que les autres chaines ont ete
a sec, la derniere a pris tous les creneaux restants : 14 recipe_crave sur la
seule journee du 09/09, une publication toutes les 80 minutes pendant 19
heures sur un meme compte. Un rythme que TikTok lit comme du spam, et cinq
jours de stock brules en deux.

Ce script garde les `quota` premiers depots de chaque chaine pour chaque jour,
supprime les posts Zernio des autres et remet les videos en `pending`. Le
depot suivant les replacera sur des creneaux libres, quota respecte.

Il ne touche a rien d'autre : ni aux videos publiees, ni a celles qui tiennent
dans leur quota, ni aux posts d'une autre chaine.
"""

import argparse
import collections
import json
import os
import pathlib
import sys
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import publish_next as pn
import programmer_avance as pa

ICI = pathlib.Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--essai", action="store_true", help="n'efface rien")
    a = ap.parse_args()

    pa.precharger_cles()
    retires = 0

    for nom, fichier, pseudo, numero, quota in pa.CHAINES:
        chemin = ICI / fichier
        queue = json.loads(chemin.read_text(encoding="utf-8"))
        deposes = [v for v in queue
                   if v.get("status") == "scheduled" and v.get("scheduledFor")]
        deposes.sort(key=lambda v: v["scheduledFor"])

        pris = collections.defaultdict(int)
        change = False
        for v in deposes:
            jour = datetime.strptime(v["scheduledFor"], pa.FORMAT).date()
            pris[jour] += 1
            if pris[jour] <= quota:
                continue

            print("  %-12s %-32s %s  (%d e du jour, quota %d)"
                  % (nom, v["id"], v["scheduledFor"][:16], pris[jour], quota))
            retires += 1
            if a.essai:
                continue

            os.environ["ZERNIO_API_KEY"] = pa.cle_zernio(numero)
            try:
                pn.zernio_call("DELETE", "/posts/%s" % v["postId"])
            except Exception as e:
                # Un post deja parti ou deja supprime n'est pas un probleme :
                # le realignement sur Zernio tranchera au prochain passage.
                print("     (suppression impossible : %s)" % str(e)[:90])
                continue
            v["status"] = "pending"
            v.pop("postId", None)
            v.pop("scheduledFor", None)
            v.pop("scheduledForUtc", None)
            change = True

        if change and not a.essai:
            chemin.write_text(json.dumps(queue, indent=2, ensure_ascii=False),
                              encoding="utf-8")

    print("\n%d depot(s) %s" % (retires, "a retirer" if a.essai else "retire(s)"))


if __name__ == "__main__":
    main()
