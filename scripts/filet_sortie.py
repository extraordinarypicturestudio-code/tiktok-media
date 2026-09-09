#!/usr/bin/env python3
"""FILET DE SECURITE : verifie qu'un creneau passe a bien produit une sortie.

Pourquoi ce script existe
-------------------------
Une video deposee chez Zernio peut ne jamais sortir de trois facons, et deux
d'entre elles ne laissent AUCUNE trace :

  1. le post echoue au declenchement (saturation TikTok, refus de contenu) ;
  2. le post disparait purement et simplement - c'est arrive le 2026-09-08 a
     la sortie love_kitchen de 23h30, effacee sans que rien ne le signale ;
  3. le post reste bloque en `pending` cote plateforme.

Jusqu'ici personne ne regardait APRES COUP si le creneau avait tenu sa
promesse. Le realignement corrigeait la file en silence, et l'utilisateur
decouvrait le lendemain qu'il ne s'etait rien passe.

Ce que fait ce script
---------------------
Il regarde les creneaux ECHUS (entre MARGE_MIN et MARGE_MAX minutes dans le
passe) et tranche, video par video :

  publiee                  -> la file est mise a jour, rien a signaler
  echec de saturation      -> laisse au rattrapage, signale sans alarmer
  echec pour un autre motif-> remise en file, ANOMALIE (le motif est affiche)
  post introuvable         -> remise en file, ANOMALIE
  toujours en attente      -> signale, on repassera au prochain tour

Les videos remises en file sont ensuite redeposees par `programmer_avance.py`,
que le workflow appelle juste apres : elles reprennent le prochain creneau
libre de leur chaine au lieu d'etre perdues.

Sortie : `creneaux_manques.txt` (une ligne par sortie manquee) que le workflow
lit pour ouvrir une alerte. Le script sort TOUJOURS en 0 : une sortie manquee
n'est pas une panne du script, et un run rouge de plus n'aide personne - c'est
justement le bruit qui a masque le vrai defaut pendant deux semaines.
"""

import json
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import publish_next as pn
import programmer_avance as pa

ICI = pathlib.Path(__file__).resolve().parent.parent

# On laisse le temps a Zernio de declencher et a TikTok de confirmer : sous un
# quart d'heure, un post encore `pending` est normal, pas suspect.
MARGE_MIN = 15
# Au-dela, c'est le tour precedent qui s'en est deja occupe.
MARGE_MAX = 240

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")


def horodatage(v):
    """Instant prevu de la sortie, en UTC, ou None."""
    q = v.get("scheduledForUtc")
    if q:
        try:
            return datetime.fromisoformat(q.replace("Z", "+00:00"))
        except ValueError:
            pass
    q = v.get("scheduledFor")
    if q:
        try:
            return (datetime.strptime(q, pa.FORMAT)
                    .replace(tzinfo=pa.FUSEAU).astimezone(timezone.utc))
        except ValueError:
            pass
    return None


def main():
    pa.precharger_cles()
    now = datetime.now(timezone.utc)
    manques, publiees, en_cours = [], 0, 0

    for nom, fichier, pseudo, numero, actif in pa.CHAINES:
        chemin = ICI / fichier
        queue = json.loads(chemin.read_text(encoding="utf-8"))
        change = False
        os.environ["ZERNIO_API_KEY"] = pa.cle_zernio(numero)

        for v in queue:
            if v.get("status") != "scheduled":
                continue
            t = horodatage(v)
            if t is None:
                continue
            age = (now - t).total_seconds() / 60
            if not (MARGE_MIN <= age <= MARGE_MAX):
                continue

            quand = v.get("scheduledFor", t.isoformat())[:16].replace("T", " ")
            try:
                post = pn.zernio_call("GET", "/posts/%s" % v.get("postId"))["post"]
            except Exception as e:
                if "404" not in str(e):
                    print("  %-12s %-28s lecture impossible : %s"
                          % (nom, v["id"], str(e)[:70]))
                    continue
                # Cas le plus grave : le post a disparu. La video ne sortira
                # jamais et rien d'autre ne le dit.
                print("  %-12s %-28s %s  POST DISPARU -> remise en file"
                      % (nom, v["id"], quand))
                manques.append("%s|%s|%s|post disparu" % (nom, v["id"], quand))
                v["status"] = "pending"
                for c in ("postId", "scheduledFor", "scheduledForUtc"):
                    v.pop(c, None)
                change = True
                continue

            pf = (post.get("platforms") or [{}])[0]
            etat = pf.get("status") or post.get("status")
            motif = pf.get("errorMessage") or ""

            if etat == "published" or post.get("status") == "published":
                publiees += 1
                v["status"] = "published"
                v["publishedAt"] = (post.get("publishedAt") or pf.get("publishedAt")
                                    or now.strftime("%Y-%m-%dT%H:%M:%SZ"))
                for c in ("scheduledFor", "scheduledForUtc", "error"):
                    v.pop(c, None)
                pn.enregistrer_et_supprimer(v, pseudo)
                change = True
                print("  %-12s %-28s %s  EN LIGNE" % (nom, v["id"], quand))
                continue

            if etat in ("failed", "error"):
                if "at capacity" in motif:
                    # Le rattrapage sait le reprendre : on ne touche a rien,
                    # mais on le dit - une sortie decalee reste une sortie
                    # manquee de son creneau.
                    en_cours += 1
                    print("  %-12s %-28s %s  refus de saturation, rattrapage en cours"
                          % (nom, v["id"], quand))
                    manques.append("%s|%s|%s|saturation TikTok, rattrapage en cours"
                                   % (nom, v["id"], quand))
                else:
                    print("  %-12s %-28s %s  ECHEC : %s"
                          % (nom, v["id"], quand, motif[:60] or "motif inconnu"))
                    manques.append("%s|%s|%s|%s" % (nom, v["id"], quand,
                                                    motif[:120] or "echec sans motif"))
                    v["status"] = "pending"
                    v["error"] = "Echec au creneau du %s : %s" % (quand, motif[:200])
                    for c in ("postId", "scheduledFor", "scheduledForUtc"):
                        v.pop(c, None)
                    change = True
                continue

            en_cours += 1
            print("  %-12s %-28s %s  toujours '%s' apres %.0f min"
                  % (nom, v["id"], quand, etat, age))

        if change:
            chemin.write_text(json.dumps(queue, indent=2, ensure_ascii=False),
                              encoding="utf-8")

    marqueur = ICI / "creneaux_manques.txt"
    if manques:
        marqueur.write_text("\n".join(manques) + "\n", encoding="utf-8")
    elif marqueur.exists():
        marqueur.unlink()

    print("\n%d sortie(s) confirmee(s), %d en cours, %d creneau(x) manque(s)"
          % (publiees, en_cours, len(manques)))


if __name__ == "__main__":
    main()
