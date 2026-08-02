#!/usr/bin/env python3
"""
Publie la prochaine video "pending" d'une file d'attente sur un compte TikTok
via l'API Zernio, puis marque l'entree comme publiee dans le meme fichier.

Concu pour tourner dans GitHub Actions : aucune dependance hors bibliotheque
standard, la cle API est lue depuis une variable d'environnement (secret
GitHub), jamais commitee dans le depot.

Usage : python publish_next.py <queue.json> <pseudo_tiktok>
"""

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error

ZERNIO_BASE = "https://zernio.com/api/v1"
REGISTRE = "used-clips-hashes.json"

# Nombre de videos differentes tentees dans un meme creneau avant d'abandonner.
MAX_CANDIDATS_PAR_RUN = 3


def ecrire_rapport_echec(pseudo, echecs, rattrape):
    """Ecrit un resume lisible destine a l'alerte GitHub (issue + email).

    Sans ca, un echec de publication passe totalement inapercu jusqu'a ce que
    l'utilisateur remarque lui-meme qu'une chaine ne publie plus - ce qui est
    exactement le probleme a eliminer.
    """
    if not echecs:
        return
    titre = (f"[{pseudo}] Publication rattrapee mais {len(echecs)} clip(s) ecarte(s)"
             if rattrape else
             f"[{pseudo}] AUCUNE publication sur ce creneau")
    corps = [titre, ""]
    corps.append("Details :")
    for e in echecs:
        corps.append(f"  - {e}")
    corps.append("")
    corps.append("Que faire : les clips en statut 'failed' ou 'unconfirmed' dans")
    corps.append("la file necessitent une verification. Les clips 'pending' seront")
    corps.append("automatiquement retentes au prochain creneau.")
    texte = "\n".join(corps)

    chemin = os.environ.get("RAPPORT_ECHEC_FICHIER", "echec-publication.txt")
    with open(chemin, "w", encoding="utf-8") as fh:
        fh.write(texte)
    print("\n" + texte)


def chemin_local(url):
    """Extrait le chemin relatif dans le depot depuis une URL raw.githubusercontent."""
    marqueur = "/main/"
    i = url.find(marqueur)
    return url[i + len(marqueur):] if i != -1 else None


def hash_fichier(chemin):
    h = hashlib.md5()
    with open(chemin, "rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


def enregistrer_et_supprimer(video, channel):
    """Apres une publication reussie : garde le hash a jamais, supprime le
    fichier local. La video est sur TikTok, elle n'a plus besoin d'etre
    hebergee, et sa suppression evite de la reutiliser par erreur - mais
    c'est le hash conserve dans le registre qui empeche de re-telecharger
    la meme source sous un autre nom (le vrai bug du 01/08)."""
    chemin = chemin_local(video["url"])
    if chemin is None or not os.path.exists(chemin):
        print(f"  (fichier introuvable pour {video['id']}, hash non enregistre)")
        return

    entree = {
        "hash": hash_fichier(chemin),
        "id": video["id"],
        "channel": channel,
        "file": chemin,
        "status": "published",
        "publishedAt": video.get("publishedAt"),
    }

    registre = []
    if os.path.exists(REGISTRE):
        with open(REGISTRE, encoding="utf-8") as fh:
            registre = json.load(fh)
    registre.append(entree)
    with open(REGISTRE, "w", encoding="utf-8") as fh:
        json.dump(registre, fh, indent=2, ensure_ascii=False)

    os.remove(chemin)
    print(f"  fichier local supprime, hash enregistre dans {REGISTRE}")


class EnvoiIncertain(Exception):
    """L'envoi n'a pas pu etre confirme : le post existe peut-etre deja.

    Levee quand la connexion echoue APRES l'envoi de la requete (timeout de
    lecture, coupure reseau). Republier a l'aveugle dans ce cas creerait un
    doublon : il faut d'abord aller lire l'etat reel cote Zernio.
    """


def zernio_call(methode, chemin, payload=None, timeout=120):
    cle = os.environ["ZERNIO_API_KEY"]
    req = urllib.request.Request(
        ZERNIO_BASE + chemin,
        data=json.dumps(payload).encode() if payload is not None else None,
        method=methode,
        headers={"Authorization": f"Bearer {cle}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        # Reponse recue : le serveur a rejete la requete, rien n'a ete cree.
        detail = e.read().decode(errors="replace")
        raise RuntimeError(f"Zernio {e.code} : {detail[:500]}")
    except (TimeoutError, socket.timeout, urllib.error.URLError, OSError) as e:
        raise EnvoiIncertain(f"{type(e).__name__} : {e}")


def chercher_post_recent(url_video, depuis):
    """Retrouve un post creee pour cette video apres l'instant 'depuis'.

    Sert de filet apres un envoi incertain : si Zernio a bien enregistre le
    post malgre le timeout, on le retrouve ici au lieu de republier.
    """
    try:
        data = zernio_call("GET", "/posts")
    except (RuntimeError, EnvoiIncertain) as e:
        print(f"  (impossible de relire les posts : {e})")
        return None

    posts = data.get("posts", data) if isinstance(data, dict) else data
    for p in posts or []:
        cree = p.get("createdAt", "")
        if cree < depuis:
            continue
        for m in p.get("mediaItems", []):
            if m.get("url") == url_video:
                return p
    return None


def compte_id(pseudo_tiktok):
    data = zernio_call("GET", "/accounts")
    items = data.get("accounts", data) if isinstance(data, dict) else data
    for a in items:
        if a.get("platform") == "tiktok" and a.get("username") == pseudo_tiktok:
            return a.get("_id") or a.get("id")
    raise RuntimeError(f"Compte TikTok '{pseudo_tiktok}' introuvable dans Zernio")


def publier(account_id, url_video, legende):
    payload = {
        "content": legende,
        "publishNow": True,
        "platforms": [{"platform": "tiktok", "accountId": account_id}],
        "mediaItems": [{"type": "video", "url": url_video}],
        "tiktokSettings": {
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "allow_comment": True,
            "allow_duet": True,
            "allow_stitch": True,
            "video_cover_timestamp_ms": 2000,
            "content_preview_confirmed": True,
            "express_consent_given": True,
        },
    }
    return zernio_call("POST", "/posts", payload)


def attendre_statut(post_id, delai_max=420, intervalle=20):
    """Interroge Zernio jusqu'a ce que le post soit confirme publie ou echoue.

    La publication TikTok est asynchrone : l'API accepte la requete tout de
    suite, mais TikTok peut refuser la video plusieurs minutes plus tard
    (Zernio "reconcilie" alors le post en 'failed'). Sans cette attente, une
    video en echec serait notee comme publiee et ne sortirait jamais.

    Renvoie (statut, message_erreur). Statut 'inconnu' si le delai expire :
    l'appelant ne doit alors PAS remettre la video en file, au risque de la
    publier deux fois.
    """
    limite = time.time() + delai_max
    dernier = "inconnu"
    while time.time() < limite:
        time.sleep(intervalle)
        try:
            post = zernio_call("GET", f"/posts/{post_id}").get("post", {})
        except RuntimeError as e:
            print(f"  (lecture du statut impossible, nouvel essai : {e})")
            continue

        dernier = post.get("status", "inconnu")
        if dernier == "published":
            return "published", None
        if dernier == "failed":
            msg = "cause inconnue"
            for p in post.get("platforms", []):
                if p.get("errorMessage"):
                    msg = p["errorMessage"]
                    break
            return "failed", msg
        print(f"  statut '{dernier}', attente de la confirmation TikTok...")

    return "inconnu", f"delai depasse (dernier statut connu : {dernier})"


def specs_video(chemin):
    """Lit les caracteristiques techniques d'un fichier video via ffprobe."""
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
    dims = probe(["-select_streams", "v:0", "-show_entries", "stream=width,height",
                  "-of", "csv=p=0:s=x"])
    return {
        "fps": fps,
        "duree": float(duree) if duree else 0.0,
        "dimensions": dims,
        "taille_mo": os.path.getsize(chemin) / (1024 * 1024),
    }


def verifier_et_reparer(video):
    """Valide un clip contre les contraintes TikTok AVANT de l'envoyer, et
    repare automatiquement ce qui est reparable.

    TikTok rejette silencieusement (via Zernio, plusieurs minutes plus tard)
    les videos hors specs : c'est ce qui a fait sauter le creneau du 02/08
    avec chicken-sandwich-07 en 21 FPS. Verifier ici coute 2 secondes et
    evite de perdre une publication entiere.

    Renvoie (ok, message, repare). Si repare=True, le fichier local a ete
    reecrit et doit etre committe.
    """
    chemin = chemin_local(video["url"])
    if chemin is None or not os.path.exists(chemin):
        return True, "fichier local absent, verification impossible", False

    s = specs_video(chemin)
    print(f"  specs : {s['fps']:.1f} fps, {s['duree']:.1f}s, {s['dimensions']}, {s['taille_mo']:.1f} Mo")

    if s["duree"] > 600:
        return False, f"duree {s['duree']:.0f}s > 10 min (limite TikTok)", False
    if s["taille_mo"] > 500:
        return False, f"fichier {s['taille_mo']:.0f} Mo trop lourd", False

    # FPS hors plage : reparable par re-encodage.
    if s["fps"] < 23 or s["fps"] > 60:
        print(f"  FPS {s['fps']:.1f} hors plage TikTok (23-60) -> re-encodage en 30 fps")
        tmp = chemin + ".fix.mp4"
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", chemin, "-r", "30",
             "-c:v", "libx264", "-preset", "medium", "-crf", "20",
             "-c:a", "aac", "-b:a", "192k", "-loglevel", "error", tmp],
            capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(tmp):
            return False, f"FPS {s['fps']:.1f} hors plage et re-encodage impossible", False
        os.replace(tmp, chemin)
        print(f"  repare : {chemin} est maintenant en 30 fps")
        return True, "re-encode en 30 fps", True

    return True, "conforme", False


def main():
    if len(sys.argv) != 3:
        print("Usage: publish_next.py <queue.json> <pseudo_tiktok>")
        sys.exit(1)

    chemin_queue, pseudo = sys.argv[1], sys.argv[2]

    with open(chemin_queue, encoding="utf-8") as fh:
        queue = json.load(fh)

    def ecrire_queue():
        with open(chemin_queue, "w", encoding="utf-8") as fh:
            json.dump(queue, fh, indent=2, ensure_ascii=False)

    en_attente = [v for v in queue if v.get("status") == "pending"]
    if not en_attente:
        print("File d'attente vide : aucune video en attente.")
        return

    cid = compte_id(pseudo)
    echecs = []

    # On ne se contente pas de la premiere video : si elle echoue, on tente la
    # suivante dans le MEME run. Sans ca, un seul clip defectueux fait perdre
    # tout le creneau de publication (probleme reel du 02/08 : une video en
    # 21 FPS a fait sauter la publication de la journee, et il fallait une
    # intervention manuelle pour s'en apercevoir).
    for a_publier in en_attente[:MAX_CANDIDATS_PAR_RUN]:
        print(f"\n--- Candidat : {a_publier['id']}")

        ok, detail, repare = verifier_et_reparer(a_publier)
        if repare:
            a_publier["note"] = f"Fichier repare automatiquement ({detail})."
            ecrire_queue()
        if not ok:
            print(f"  ecarte avant envoi : {detail}")
            a_publier["status"] = "failed"
            a_publier["error"] = f"Non conforme aux specs TikTok : {detail}"
            echecs.append(f"{a_publier['id']} : {detail}")
            ecrire_queue()
            continue

        a_publier["attempts"] = a_publier.get("attempts", 0) + 1
        avant_envoi = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 120))

        try:
            reponse = publier(cid, a_publier["url"], a_publier["caption"])
        except RuntimeError as e:
            # Rejet franc du serveur : rien n'a ete cree, on peut reessayer.
            statut_suivant = "pending" if a_publier["attempts"] < 3 else "failed"
            a_publier["status"] = statut_suivant
            a_publier["error"] = f"ECHEC : {e}"
            echecs.append(f"{a_publier['id']} : {e}")
            ecrire_queue()
            print(f"  {a_publier['error']} -> candidat suivant")
            continue
        except EnvoiIncertain as e:
            # Le post a peut-etre ete cree malgre la coupure : on verifie avant
            # toute chose, sinon un nouvel essai publierait la video deux fois.
            print(f"  envoi non confirme ({e}) - verification cote Zernio...")
            time.sleep(20)
            trouve = chercher_post_recent(a_publier["url"], avant_envoi)
            if trouve is None:
                a_publier["status"] = "unconfirmed"
                a_publier["error"] = (
                    f"Envoi non confirme et post introuvable ({e}) - a verifier"
                    " a la main, PAS remise en file pour eviter un doublon"
                )
                echecs.append(f"{a_publier['id']} : envoi non confirme")
                ecrire_queue()
                print(f"  {a_publier['error']}")
                # Cas ambigu : on s'arrete la, publier autre chose pourrait
                # faire deux posts dans le meme creneau si celui-ci sort.
                break
            print(f"  post retrouve : {trouve.get('_id')} - suivi de son statut")
            reponse = {"post": trouve}

        post_id = reponse.get("post", {}).get("_id") or reponse.get("_id")
        a_publier["postId"] = post_id
        ecrire_queue()

        statut, erreur = attendre_statut(post_id)

        if statut == "published":
            a_publier["status"] = "published"
            a_publier["publishedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            a_publier.pop("error", None)
            enregistrer_et_supprimer(a_publier, pseudo)
            ecrire_queue()
            print(f"\nPublie avec succes : {a_publier['id']}")
            if echecs:
                # Le creneau est sauve : on trace l'incident en avertissement,
                # SANS faire echouer le run (sinon GitHub envoie un mail
                # "Run failed" alors que la publication a bien eu lieu).
                print("::warning::Candidats ecartes avant succes : " + " | ".join(echecs))
                ecrire_rapport_echec(pseudo, echecs, rattrape=True)
            sys.exit(0)

        if statut == "failed":
            if a_publier["attempts"] >= 3:
                a_publier["status"] = "failed"
                a_publier["error"] = f"ECHEC definitif apres {a_publier['attempts']} tentatives : {erreur}"
            else:
                a_publier["status"] = "pending"
                a_publier["error"] = f"Echec TikTok ({erreur}), tentative {a_publier['attempts']}/3"
            echecs.append(f"{a_publier['id']} : {erreur}")
            ecrire_queue()
            print(f"  {a_publier['error']} -> candidat suivant")
            continue

        # Statut indetermine : la video est peut-etre en ligne. On ne la remet
        # surtout pas en file, et on n'en publie pas une autre (risque de
        # doublon dans le creneau).
        a_publier["status"] = "unconfirmed"
        a_publier["error"] = f"Statut non confirme ({erreur}) - a verifier a la main"
        echecs.append(f"{a_publier['id']} : statut non confirme")
        ecrire_queue()
        print(f"  {a_publier['error']}")
        break

    ecrire_queue()
    print("\nAucune publication n'a abouti sur ce creneau.")
    ecrire_rapport_echec(pseudo, echecs, rattrape=False)
    sys.exit(1)


if __name__ == "__main__":
    main()
