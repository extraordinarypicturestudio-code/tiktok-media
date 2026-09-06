#!/usr/bin/env python3
"""Refait la couverture des videos deposees avec la vignette par defaut.

Pourquoi
--------
Le choix de vignette passe par Gemini, dont le quota gratuit est de 500
requetes par jour. Un gros depot l'epuise : le 2026-09-06, 19 des 44 videos
deposees sont parties avec la vignette par defaut a 2 s, c'est-a-dire un plan
d'intro - exactement ce que le choix automatique existe pour eviter, et ce que
`project_love_kitchen_performance` designe comme le facteur qui decide des vues.

Comme les sorties sont deposees plusieurs jours a l'avance, rien n'oblige a
subir ce hasard : le lendemain, quota reinitialise, on refait la couverture
AVANT la date de sortie.

Comment
-------
Zernio ne renvoie pas `tiktokSettings` dans ses reponses : la vignette d'un
post depose est illisible apres coup. C'est donc la file qui porte la trace,
dans `vignette_auto`, ecrite par `programmer_avance.py`.

Zernio n'expose pas non plus de modification de post : on supprime et on
recree AU MEME CRENEAU. La suppression d'un post programme n'a aucun effet
visible cote TikTok, qui n'a pas encore ete sollicite.

Ordre d'importance : les sorties les plus proches d'abord, puisque le quota
peut s'epuiser de nouveau avant la fin de la liste.
"""

import argparse
import json
import os
import pathlib
import sys
import time
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import publish_next as pn
import programmer_avance as pa

ICI = pathlib.Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=0, help="plafond de corrections")
    ap.add_argument("--pause", type=int, default=10)
    ap.add_argument("--essai", action="store_true")
    a = ap.parse_args()

    pa.precharger_cles()
    pa.cle_gemini_locale()

    # Toutes chaines confondues, les sorties les plus proches en premier.
    travail = []
    for nom, fichier, pseudo, numero, _ in pa.CHAINES:
        chemin = ICI / fichier
        queue = json.loads(chemin.read_text(encoding="utf-8"))
        for v in queue:
            if (v.get("status") == "scheduled"
                    and v.get("vignette_auto") is False
                    and v.get("scheduledFor") and v.get("postId")):
                travail.append((v["scheduledFor"], nom, pseudo, numero, chemin, queue, v))
    travail.sort(key=lambda x: x[0])

    faites = 0
    cids = {}
    for quand, nom, pseudo, numero, chemin, queue, v in travail:
        if a.max and faites >= a.max:
            break
        chemin_clip = pn.chemin_local(v["url"])
        if not chemin_clip or not os.path.exists(chemin_clip):
            print("  %-12s %-30s clip absent du depot, laisse tel quel"
                  % (nom, v["id"]))
            continue

        os.environ["ZERNIO_API_KEY"] = pa.cle_zernio(numero)
        specs = pn.specs_video(chemin_clip)
        if not specs or not specs.get("duree"):
            continue
        cover = pn.choisir_vignette_ms(chemin_clip, specs["duree"])
        if cover == pn.VIGNETTE_DEFAUT_MS:
            print("  %-12s %-30s vignette toujours indisponible, on s'arrete"
                  % (nom, v["id"]))
            break

        print("  %-12s %-30s %s -> vignette a %.1f s"
              % (nom, v["id"], quand[:16], cover / 1000))
        faites += 1
        if a.essai:
            continue

        try:
            pn.zernio_call("DELETE", "/posts/%s" % v["postId"])
        except Exception as e:
            print("     (suppression impossible : %s)" % str(e)[:90])
            continue
        if nom not in cids:
            cids[nom] = pn.compte_id(pseudo)
        moment = datetime.strptime(v["scheduledFor"], pa.FORMAT).replace(
            tzinfo=pa.FUSEAU)
        try:
            pid, auto = pa.deposer(v, cids[nom], moment)
        except Exception as e:
            # Le post a ete supprime et la recreation a echoue : la video doit
            # revenir en file, sinon elle serait perdue des deux cotes.
            print("     (recreation impossible : %s) -> remise en attente"
                  % str(e)[:90])
            v["status"] = "pending"
            v.pop("postId", None)
            v.pop("scheduledFor", None)
            v.pop("scheduledForUtc", None)
            chemin.write_text(json.dumps(queue, indent=2, ensure_ascii=False),
                              encoding="utf-8")
            continue
        v["postId"] = pid
        v["vignette_auto"] = auto
        chemin.write_text(json.dumps(queue, indent=2, ensure_ascii=False),
                          encoding="utf-8")
        if a.pause:
            time.sleep(a.pause)

    print("\n%d vignette(s) %s" % (faites, "a refaire" if a.essai else "refaite(s)"))


if __name__ == "__main__":
    main()
