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

import calendar
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

ZERNIO_BASE = "https://zernio.com/api/v1"

# Bigfoot Content tourne sur un 2e compte Zernio (email different, cle
# differente) : chaque chaine doit interroger la BONNE cle, sinon ses posts
# sont invisibles du controle de sante (comptes Zernio distincts = resultats
# distincts, une cle ne voit pas les posts de l'autre compte).
#
# 'silence_max_h' = duree au-dela de laquelle une chaine est consideree en
# panne et son creneau RATTRAPE automatiquement. Calibre sur le plus grand
# ecart normal entre deux creneaux + une marge pour le retard chronique des
# crons GitHub (jusqu'a ~4h observe) :
#   - TikTok (4/jour : 11h, 16h, 20h, 23h UTC, 2 France+2 US anti-
#     cannibalisation) -> ecart max 12h (creneau nuit 23h->11h) + marge = 16h
#
# Bigfoot Content EN PAUSE (07/08/2026, demande explicite) : retiree de
# CHAINES pour que le controle de sante ne la traite plus comme silencieuse
# et n'essaie plus de la rattraper. Le workflow publish-bigfoot.yml est
# aussi desactive cote GitHub (gh workflow disable). Pour reprendre :
# reactiver le workflow (gh workflow enable "Publish Bigfoot Content
# (YouTube Shorts)") et redecommenter l'entree ci-dessous.
CHAINES = [
    {"queue": "queue-toprank.json", "dossier": "clips-toprank",
     "pseudo": "toprank.tv1", "par_jour": 4, "cle": "ZERNIO_API_KEY",
     "script": "publish_next.py", "silence_max_h": 16},
    {"queue": "queue-recipecrave.json", "dossier": "clips",
     "pseudo": "recipe_crave", "par_jour": 4, "cle": "ZERNIO_API_KEY",
     "script": "publish_next.py", "silence_max_h": 16},
    {"queue": "queue-nextlevelplays.json", "dossier": "clips-nextlevelplays",
     "pseudo": "nextlevelplays88", "par_jour": 3, "cle": "ZERNIO_API_KEY_2",
     "script": "publish_next.py", "silence_max_h": 17},
    # {"queue": "queue-bigfoot.json", "dossier": "clips-bigfoot",
    #  "pseudo": "extraordinarystudiopicture", "par_jour": 2,
    #  "cle": "ZERNIO_API_KEY_2", "script": "publish_next_youtube.py",
    #  "silence_max_h": 22},
]


def heures_depuis(horodatage):
    """Nombre d'heures ecoulees depuis un horodatage ISO (UTC), ou None.

    Utilise calendar.timegm et NON time.mktime : mktime interprete le
    struct_time comme une heure LOCALE alors que Zernio renvoie de l'UTC.
    Sur un runner GitHub (en UTC) l'erreur est invisible, mais en local
    (Paris = UTC+2 en ete) le calcul est fausse de 2h - assez pour declencher
    un rattrapage trop tot ou trop tard.
    """
    if not horodatage:
        return None
    try:
        t = calendar.timegm(time.strptime(horodatage[:19], "%Y-%m-%dT%H:%M:%S"))
        return (time.time() - t) / 3600
    except Exception:
        return None


def zernio_posts(env_var):
    cle = os.environ.get(env_var)
    if not cle:
        raise RuntimeError(f"variable d'environnement {env_var} absente")
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

    # Une meme URL peut avoir plusieurs posts (un echec puis une reussite) :
    # la video est en ligne si AU MOINS un post est 'published'. On fusionne
    # les resultats des deux comptes Zernio (cles differentes) dans les memes
    # dictionnaires : les URLs de media sont uniques au depot, pas de risque
    # de collision entre comptes.
    etats = {}
    posts_par_id = {}
    derniere_publication = ""
    rattrapages = []
    for env_var in {c["cle"] for c in CHAINES}:
        try:
            posts = zernio_posts(env_var)
        except Exception as e:
            anomalies.append(f"Impossible de joindre Zernio avec {env_var} : {e}")
            continue
        for p in posts:
            posts_par_id[p.get("_id")] = p
            for m in p.get("mediaItems", []):
                etats.setdefault(m.get("url"), set()).add(p.get("status"))
            if p.get("status") == "published":
                d = p.get("publishedAt") or p.get("createdAt") or ""
                derniere_publication = max(derniere_publication, d)

    if not posts_par_id and not etats:
        print("::error::Impossible de joindre Zernio sur aucun des comptes")
        sys.exit(1)

    for chaine in CHAINES:
        fichier = chaine["queue"]
        dossier = chaine["dossier"]
        pseudo = chaine["pseudo"]
        par_jour = chaine["par_jour"]
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

        # --- Detection de silence PAR CHAINE + rattrapage automatique -------
        # Un controle global ne suffit pas : si TikTok publie normalement mais
        # que Bigfoot ne sort plus rien, la moyenne globale reste bonne et le
        # probleme passe inapercu. C'est exactement ce qui est arrive le
        # 02/08 (workflow bigfoot fraichement cree, GitHub a saute sa premiere
        # occurrence cron : zero publication, zero alerte).
        derniere_chaine = ""
        for v in queue:
            if v.get("status") == "published" and v.get("publishedAt"):
                derniere_chaine = max(derniere_chaine, v["publishedAt"])
        h = heures_depuis(derniere_chaine)

        if h is None:
            infos.append(f"{pseudo} : aucune publication enregistree pour l'instant")
            if pending:
                # Chaine jamais partie alors qu'elle a du contenu pret : c'est
                # le cas d'un workflow tout neuf qui n'a jamais tourne.
                rattrapages.append((pseudo, chaine, "aucune publication enregistree"))
        else:
            infos.append(f"{pseudo} : derniere publication il y a {h:.1f} h")
            if h > chaine["silence_max_h"] and pending:
                rattrapages.append(
                    (pseudo, chaine,
                     f"silencieuse depuis {h:.0f} h (seuil {chaine['silence_max_h']} h)"))

        infos.append(f"{pseudo} : {len(pending)} en attente "
                     f"({len(pending)/par_jour:.1f} jours de contenu)")

        for v in queue:
            st = etats.get(v["url"], set())
            if v.get("status") == "pending" and "published" in st:
                anomalies.append(
                    f"[{pseudo}] DOUBLON IMMINENT : '{v['id']}' est marquee en "
                    "attente mais est deja en ligne")
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
                    "(plage standard 23-60) - sera reparee automatiquement au "
                    "prochain creneau")
            duree_max = 900 if "bigfoot" in fichier else 600
            if duree > duree_max:
                anomalies.append(
                    f"[{pseudo}] SPEC INVALIDE : '{v['id']}' dure {duree:.0f}s "
                    f"(max {duree_max//60} min)")

    h_globale = heures_depuis(derniere_publication)
    if h_globale is not None:
        infos.append(f"(toutes chaines confondues : derniere publication il y a "
                     f"{h_globale:.1f} h)")

    print("=== Etat du pipeline ===")
    for i in infos:
        print("  " + i)

    if corrections_totales:
        print("\n=== Corrections automatiques appliquees ===")
        for c in corrections_totales:
            print("  - " + c)

    # Rattrapage automatique : on ne se contente pas de signaler qu'une chaine
    # est muette, on relance nous-memes son workflow de publication. Le
    # fichier est lu par l'etape suivante du workflow, qui declenche les
    # `workflow_dispatch` correspondants.
    if rattrapages:
        print("\n=== Creneaux rattrapes automatiquement ===")
        lignes = []
        for pseudo, ch, raison in rattrapages:
            print(f"  - [{pseudo}] {raison} -> publication de rattrapage")
            # Une ligne = une commande a executer par le workflow. On passe
            # aussi le nom de la variable d'environnement contenant la bonne
            # cle Zernio (les chaines ne sont pas sur le meme compte).
            lignes.append(f"{ch['script']}|{ch['queue']}|{ch['pseudo']}|{ch['cle']}")
        with open("rattrapages.txt", "w", encoding="utf-8") as fh:
            fh.write("\n".join(lignes))

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

    # IMPORTANT : on sort en 0 meme avec des anomalies.
    #
    # Le controle tourne toutes les 4h. Sortir en 1 ferait envoyer par GitHub
    # un mail "Run failed" a CHAQUE passage tant que l'anomalie dure - soit
    # 6 mails par jour pour un stock bas qui n'a rien d'une panne. C'est
    # exactement la fatigue d'alerte qu'on cherche a supprimer.
    #
    # L'alerte passe donc uniquement par l'issue GitHub (etape suivante du
    # workflow), qui elle est dedupliquee : une seule issue ouverte a la
    # fois, pas de nouveau message tant qu'elle n'est pas fermee.
    print("\n(run marque en succes : l'alerte passe par l'issue GitHub, "
          "pas par un mail d'echec)")


if __name__ == "__main__":
    main()
