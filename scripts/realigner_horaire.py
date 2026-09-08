#!/usr/bin/env python3
"""Retire les depots qui ne tombent pas sur un creneau de HORAIRE.

Les depots faits avant le passage a l'horaire fixe suivent l'ancienne grille
anonyme : 00h20, 01h40, 03h00, 04h20... Sur les douze en cours le 2026-09-08,
dix tombaient hors de tout horaire voulu, dont deux en pleine nuit. Les
laisser reviendrait a garder deux calendriers en parallele.

Le script supprime le post Zernio et remet la video en `pending` ; le depot
suivant la replacera sur SON creneau. Un post deja publie n'est jamais touche,
et un post dont la suppression echoue est laisse tel quel plutot que d'etre
perdu des deux cotes.

A lancer avant `programmer_avance.py` chaque fois que HORAIRE change.
"""

import argparse
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import publish_next as pn
import programmer_avance as pa

ICI = pathlib.Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--essai", action="store_true", help="n'efface rien")
    a = ap.parse_args()

    pa.precharger_cles()
    pa.verifier_horaire()
    # Un creneau n'est valable que s'il porte l'heure ET la chaine attendues :
    # une video love_kitchen posee sur l'heure de toprank est aussi fausse
    # qu'une video posee a 3 h du matin.
    valides = set(pa.HORAIRE)
    retires = 0

    for nom, fichier, pseudo, numero, _ in pa.CHAINES:
        chemin = ICI / fichier
        queue = json.loads(chemin.read_text(encoding="utf-8"))
        change = False
        for v in queue:
            if v.get("status") != "scheduled" or not v.get("scheduledFor"):
                continue
            if (v["scheduledFor"][11:16], nom) in valides:
                continue

            print("  %-12s %-32s %s  hors horaire"
                  % (nom, v["id"][:32], v["scheduledFor"][:16].replace("T", " ")))
            retires += 1
            if a.essai:
                continue

            os.environ["ZERNIO_API_KEY"] = pa.cle_zernio(numero)
            try:
                pn.zernio_call("DELETE", "/posts/%s" % v["postId"])
            except Exception as e:
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
