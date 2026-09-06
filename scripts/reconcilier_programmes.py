#!/usr/bin/env python3
"""Aligne les files d'attente sur ce que Zernio sait reellement de chaque video.

Pourquoi ce script existe
-------------------------
Depuis que les videos sont DEPOSEES A L'AVANCE (`programmer_avance.py`), la
publication n'a plus lieu pendant le run qui la declenche : Zernio garde le post
et sollicite TikTok des heures plus tard. Personne ne repasse ensuite ecrire le
resultat dans la file. Sans ce script, une video sortie hier resterait
`scheduled` pour toujours.

Le probleme existait deja avant : `relancer_saturation.py` republie des posts en
echec sans toucher aux files. Le 2026-09-06, `40-tenderspoulet` a ete publiee a
11h18 sur love_kitchen alors que la file la donnait toujours `pending` - au
point que toute nouvelle programmation de cette video se faisait refuser par un
409 "already posted to this account within the last 24 hours". Une file qui
ment finit par republier ce qui est deja en ligne.

Regle de decision
-----------------
La verite est du cote de Zernio, et elle se lit sur l'URL du media, pas sur
l'identifiant de post memorise dans la file : le relanceur remplace le post par
un autre, l'identifiant note dans la file devient caduc, l'URL non.

  un post `published` porte cette URL  -> la video est EN LIGNE
  un post programme / en cours         -> la video est DEPOSEE
  aucun post, mais la file dit deposee -> le post a disparu, retour en attente
  que des posts en echec               -> on ne touche a rien, le relanceur
                                          s'en occupe (et lui seul)

Une video passee `published` suit exactement le meme traitement qu'apres une
publication directe : empreinte enregistree dans le registre, fichier local
supprime. Sans quoi le depot enflerait indefiniment et la meme source pourrait
etre retelechargee sous un autre nom.
"""

import argparse
import json
import os
import pathlib
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import publish_next as pn
import programmer_avance as pa

ICI = pathlib.Path(__file__).resolve().parent.parent

# Statuts de file que ce script peut faire evoluer. `failed`, `rejected` et
# `on_hold` sont des decisions humaines ou definitives : on n'y touche pas.
MOBILES = ("pending", "scheduled", "unconfirmed")

EN_COURS = ("scheduled", "publishing", "processing", "queued", "pending")


def posts_par_url(cle):
    """Index URL de media -> posts, pour une cle Zernio."""
    os.environ["ZERNIO_API_KEY"] = cle
    data = pn.zernio_call("GET", "/posts?limit=500")
    index = {}
    for p in (data.get("posts") or []):
        for m in p.get("mediaItems", []):
            if m.get("url"):
                index.setdefault(m["url"], []).append(p)
    return index


def statut_reel(posts):
    """(statut, post) le plus avance parmi les posts portant cette video."""
    for p in posts:
        if p.get("status") == "published":
            return "published", p
        for pl in p.get("platforms", []):
            if pl.get("status") == "published":
                return "published", p
    for p in posts:
        if p.get("status") in EN_COURS:
            return "scheduled", p
    return "failed", posts[0] if posts else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--essai", action="store_true", help="n'ecrit rien")
    a = ap.parse_args()

    pa.precharger_cles()
    index_par_cle = {}
    total = {"publiee": 0, "deposee": 0, "rendue": 0}

    for nom, fichier, pseudo, numero, _ in pa.CHAINES:
        chemin = ICI / fichier
        queue = json.loads(chemin.read_text(encoding="utf-8"))
        if numero not in index_par_cle:
            try:
                index_par_cle[numero] = posts_par_url(pa.cle_zernio(numero))
            except Exception as e:
                print("%-12s lecture Zernio impossible : %s" % (nom, str(e)[:110]))
                continue
        index = index_par_cle[numero]
        change = False

        for v in queue:
            if v.get("status") not in MOBILES:
                continue
            posts = index.get(v.get("url"), [])
            if not posts:
                # Depose puis disparu : le post a ete supprime (purge d'un 409,
                # menage cote Zernio). La video doit redevenir publiable.
                if v.get("status") == "scheduled":
                    print("  %-12s %-30s post disparu -> remise en attente"
                          % (nom, v["id"]))
                    if not a.essai:
                        v["status"] = "pending"
                        v.pop("postId", None)
                        v.pop("scheduledFor", None)
                        v.pop("scheduledForUtc", None)
                        change = True
                    total["rendue"] += 1
                continue

            etat, post = statut_reel(posts)

            if etat == "published" and v.get("status") != "published":
                quand = (post.get("publishedAt")
                         or (post.get("platforms") or [{}])[0].get("publishedAt")
                         or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
                print("  %-12s %-30s EN LIGNE le %s" % (nom, v["id"], quand[:16]))
                total["publiee"] += 1
                if not a.essai:
                    v["status"] = "published"
                    v["publishedAt"] = quand
                    v["postId"] = post.get("_id")
                    v.pop("error", None)
                    v.pop("scheduledFor", None)
                    v.pop("scheduledForUtc", None)
                    pn.enregistrer_et_supprimer(v, pseudo)
                    change = True

            elif etat == "scheduled":
                prevu = post.get("scheduledFor")
                if v.get("status") != "scheduled" or v.get("postId") != post.get("_id"):
                    print("  %-12s %-30s deposee pour %s"
                          % (nom, v["id"], (prevu or "?")[:16]))
                    total["deposee"] += 1
                    if not a.essai:
                        v["status"] = "scheduled"
                        v["postId"] = post.get("_id")
                        if prevu:
                            # Les deux formes : l'UTC est ce que renvoie Zernio,
                            # l'heure de Paris est la seule lisible dans la file
                            # et celle sur laquelle `programmer_avance` reconnait
                            # un creneau deja occupe.
                            v["scheduledForUtc"] = prevu
                            v["scheduledFor"] = (
                                datetime.fromisoformat(prevu.replace("Z", "+00:00"))
                                .astimezone(pa.FUSEAU).strftime(pa.FORMAT))
                        v.pop("error", None)
                        change = True

        if change and not a.essai:
            chemin.write_text(json.dumps(queue, indent=2, ensure_ascii=False),
                              encoding="utf-8")

    print("\n%d passee(s) en ligne, %d confirmee(s) deposee(s), %d rendue(s) a la file"
          % (total["publiee"], total["deposee"], total["rendue"]))


if __name__ == "__main__":
    main()
