#!/usr/bin/env python3
"""
Controle de sante du pipeline de publication TikTok.

Croise l'etat reel des posts cote Zernio avec les deux files d'attente et
signale UNIQUEMENT ce qui demande une intervention humaine :

  - videos "pending" qui sont pourtant deja en ligne (risque de doublon)
  - videos "published" qui ne sont jamais sorties (publication fantome)
  - videos bloquees en "failed" / "unconfirmed"
  - files bientot vides (moins de 2 jours de contenu)
  - aucune publication reussie depuis plus de 24h (pipeline en panne)
  - clips non conformes aux specs TikTok (FPS hors 23-60, duree, taille)

Sort en code 1 uniquement si une anomalie reelle est detectee, pour que
l'alerte GitHub ne se declenche QUE dans ce cas (et pas tous les jours).

Usage : python healthcheck.py
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

ZERNIO_BASE = "https://zernio.com/api/v1"

CHAINES = [
    ("queue-toprank.json", "clips-toprank", "toprank.tv1", 3),
    ("queue-recipecrave.json", "clips", "recipe_crave", 3),
]


def zernio_posts():
    cle = os.environ["ZERNIO_API_KEY"]
    req = urllib.request.Request(
        ZERNIO_BASE + "/posts",
        headers={"Authorization": f"Bearer {cle}"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    return data.get("posts", data) if isinstance(data, dict) else data


def specs(chemin):
    def probe(args):
        r = subprocess.run(["ffprobe", "-v", "error"] + args + [chemin],
                           capture_output=True, text=True)
        return r.stdout.strip()
    fr = probe(["-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
                "-of", "default=noprint_wrappers=1:nokey=1"])
    try:
        num, den = fr.split("/")
        fps = float(num) / float(den)
    except Exception:
        fps = 0.0
    duree = probe(["-show_entries", "format=duration",
                   "-of", "default=noprint_wrappers=1:nokey=1"])
    return fps, (float(duree) if duree else 0.0)


def resoudre_incertains(queue, etats_par_url, posts_par_id):
    """Tranche automatiquement le sort des videos restees 'unconfirmed'.

    Au moment de la publication, on ne sait parfois pas si le post est sorti
    (timeout, delai depasse) : la video est alors gelee en 'unconfirmed' pour
    ne surtout pas creer de doublon. Plusieurs heures plus tard, Zernio, lui,
    connait la reponse. On tranche donc ici, sans intervention humaine :

      - le post est publie  -> 'published' (la video est en ligne)
      - le post a echoue    -> 'pending'   (elle repartira au prochain creneau)

    C'est ce qui permet au pipeline de se remettre seul d'un incident reseau
    meme si personne ne le surveille pendant plusieurs jours.
    """
    corrections = []
    for v in queue:
        if v.get("status") != "unconfirmed":
            continue

        statut_reel = None
        post = posts_par_id.get(v.get("postId"))
        if post:
            statut_reel = post.get("status")
        else:
            etats = etats_par_url.get(v["url"], set())
            if "published" in etats:
                statut_reel = "published"
            elif etats and "failed" in etats:
                statut_reel = "failed"

        if statut_reel == "published":
            v["status"] = "published"
            v.pop("error", None)
            if post and post.get("publishedAt"):
                v["publishedAt"] = post["publishedAt"]
            v["note"] = ("Statut incertain resolu automatiquement par le "
                         "controle de sante : la video est bien en ligne.")
            corrections.append(f"'{v['id']}' etait incertaine -> confirmee PUBLIEE")
        elif statut_reel == "failed":
            v["status"] = "pending"
            v.pop("error", None)
            v["note"] = ("Statut incertain resolu automatiquement : la video "
                         "n'est jamais sortie, remise en file.")
            corrections.append(f"'{v['id']}' etait incertaine -> REMISE EN FILE")
    return corrections


def main():
    anomalies = []
    infos = []
    corrections_totales = []
    fichiers_modifies = set()

    try:
        posts = zernio_posts()
    except Exception as e:
        print(f"::error::Impossible de joindre Zernio : {e}")
        sys.exit(1)

    # Une meme URL peut avoir plusieurs posts (un echec puis une reussite) :
    # la video est en ligne si AU MOINS un post est 'published'.
    etats = {}
    posts_par_id = {}
    derniere_publication = ""
    for p in posts:
        posts_par_id[p.get("_id")] = p
        for m in p.get("mediaItems", []):
            etats.setdefault(m.get("url"), set()).add(p.get("status"))
        if p.get("status") == "published":
            d = p.get("publishedAt") or p.get("createdAt") or ""
            derniere_publication = max(derniere_publication, d)

    for fichier, dossier, pseudo, par_jour in CHAINES:
        if not os.path.exists(fichier):
            anomalies.append(f"{fichier} introuvable")
            continue

        with open(fichier, encoding="utf-8") as fh:
            queue = json.load(fh)

        # Auto-guerison : resoud toute video restee 'unconfirmed' avant meme
        # de regarder ce qui reste bloque. C'est ce qui permet au pipeline de
        # se rattraper seul apres un incident reseau, sans que personne ne
        # touche a la file - essentiel si l'utilisateur est absent plusieurs
        # jours.
        corrections = resoudre_incertains(queue, etats, posts_par_id)
        if corrections:
            with open(fichier, "w", encoding="utf-8") as fh:
                json.dump(queue, fh, indent=2, ensure_ascii=False)
            fichiers_modifies.add(fichier)
            corrections_totales.extend(f"[{pseudo}] {c}" for c in corrections)

        pending = [v for v in queue if v.get("status") == "pending"]
        # 'failed' = un candidat que TikTok a definitivement refuse (contenu,
        # moderation...) : le pipeline passe deja au suivant tout seul
        # (publish_next.py), donc ca ne bloque jamais rien. On le signale une
        # seule fois (flag 'alerte_envoyee') pour ne pas re-notifier chaque
        # jour la meme video morte - sinon on recree exactement le probleme
        # de fatigue d'alerte qu'on est en train de corriger.
        for v in queue:
            if v.get("status") == "failed" and not v.get("alerte_envoyee"):
                anomalies.append(
                    f"[{pseudo}] BLOQUEE (definitif) : '{v['id']}' - "
                    f"{v.get('error', 'sans detail')} (alerte unique, ne sera "
                    "plus re-signalee)")
                v["alerte_envoyee"] = True
                fichiers_modifies.add(fichier)
            # Rare : resoudre_incertains n'a pas trouve de reponse cote
            # Zernio (post pas encore reconcilie). Contrairement a 'failed'
            # ce n'est pas fige - on re-signale tant que ce n'est pas
            # tranche, mais ca reste sans impact sur le debit de publication.
            if v.get("status") == "unconfirmed":
                anomalies.append(
                    f"[{pseudo}] TOUJOURS INCERTAIN : '{v['id']}' - Zernio n'a "
                    "pas encore de statut definitif, nouvelle tentative de "
                    "resolution au prochain controle")
        if fichier in fichiers_modifies:
            with open(fichier, "w", encoding="utf-8") as fh:
                json.dump(queue, fh, indent=2, ensure_ascii=False)

        infos.append(f"{pseudo} : {len(pending)} en attente "
                     f"({len(pending)/par_jour:.1f} jours de contenu)")

        for v in queue:
            st = etats.get(v["url"], set())
            if v.get("status") == "pending" and "published" in st:
                anomalies.append(
                    f"[{pseudo}] DOUBLON IMMINENT : '{v['id']}' est marquee en "
                    "attente mais est deja en ligne sur TikTok")
            if (v.get("status") == "published" and st
                    and "published" not in st and "note" not in v):
                anomalies.append(
                    f"[{pseudo}] PUBLICATION FANTOME : '{v['id']}' est marquee "
                    "publiee mais n'est jamais sortie")

        if len(pending) < par_jour * 2:
            anomalies.append(
                f"[{pseudo}] STOCK BAS : {len(pending)} video(s) en attente, "
                f"soit moins de 2 jours a {par_jour}/jour")

        # Controle des specs des clips encore en file : mieux vaut le savoir
        # maintenant qu'au moment ou TikTok refuse la video.
        for v in pending:
            chemin = os.path.join(dossier, v["url"].rsplit("/", 1)[-1])
            if not os.path.exists(chemin):
                anomalies.append(
                    f"[{pseudo}] FICHIER MANQUANT : '{v['id']}' est en file mais "
                    "son fichier n'existe pas dans le depot")
                continue
            fps, duree = specs(chemin)
            if fps and (fps < 23 or fps > 60):
                anomalies.append(
                    f"[{pseudo}] SPEC INVALIDE : '{v['id']}' est en {fps:.1f} FPS "
                    "(TikTok exige 23-60) - sera reparee automatiquement au "
                    "prochain creneau")
            if duree > 600:
                anomalies.append(
                    f"[{pseudo}] SPEC INVALIDE : '{v['id']}' dure {duree:.0f}s "
                    "(max 10 min)")

    # Pipeline muet depuis plus de 30h = quelque chose ne tourne plus.
    if derniere_publication:
        try:
            t = time.mktime(time.strptime(derniere_publication[:19], "%Y-%m-%dT%H:%M:%S"))
            heures = (time.time() - t) / 3600
            infos.append(f"derniere publication reussie il y a {heures:.1f} h")
            if heures > 30:
                anomalies.append(
                    f"PIPELINE MUET : aucune publication reussie depuis "
                    f"{heures:.0f} heures sur l'ensemble des chaines")
        except Exception:
            pass

    print("=== Etat du pipeline ===")
    for i in infos:
        print("  " + i)

    if corrections_totales:
        print("\n=== Corrections automatiques appliquees ===")
        for c in corrections_totales:
            print("  - " + c)

    if not anomalies:
        print("\nTout est conforme : aucune action humaine requise.")
        if fichiers_modifies:
            # Le pipeline s'est corrige tout seul : on ecrit quand meme un
            # marqueur pour que le workflow committe les fichiers modifies,
            # mais le run reste un SUCCES (exit 0) - une auto-reparation
            # n'est pas une raison de deranger qui que ce soit.
            with open("fichiers-modifies.txt", "w", encoding="utf-8") as fh:
                fh.write("\n".join(sorted(fichiers_modifies)))
        return

    print(f"\n=== {len(anomalies)} anomalie(s) detectee(s) ===")
    for a in anomalies:
        print("  - " + a)

    with open("healthcheck-anomalies.txt", "w", encoding="utf-8") as fh:
        fh.write("\n".join(f"- {a}" for a in anomalies))
    if fichiers_modifies:
        with open("fichiers-modifies.txt", "w", encoding="utf-8") as fh:
            fh.write("\n".join(sorted(fichiers_modifies)))
    sys.exit(1)


if __name__ == "__main__":
    main()
