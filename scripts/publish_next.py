#!/usr/bin/env python3
"""
Publie la prochaine video "pending" d'une file d'attente sur un compte TikTok
via l'API Zernio, puis marque l'entree comme publiee dans le meme fichier.

Concu pour tourner dans GitHub Actions : aucune dependance hors bibliotheque
standard, la cle API est lue depuis une variable d'environnement (secret
GitHub), jamais commitee dans le depot.

Usage : python publish_next.py <queue.json> <pseudo_tiktok>
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error

ZERNIO_BASE = "https://zernio.com/api/v1"


def zernio_call(methode, chemin, payload=None):
    cle = os.environ["ZERNIO_API_KEY"]
    req = urllib.request.Request(
        ZERNIO_BASE + chemin,
        data=json.dumps(payload).encode() if payload is not None else None,
        method=methode,
        headers={"Authorization": f"Bearer {cle}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError(f"Zernio {e.code} : {detail[:500]}")


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


def main():
    if len(sys.argv) != 3:
        print("Usage: publish_next.py <queue.json> <pseudo_tiktok>")
        sys.exit(1)

    chemin_queue, pseudo = sys.argv[1], sys.argv[2]

    with open(chemin_queue, encoding="utf-8") as fh:
        queue = json.load(fh)

    a_publier = next((v for v in queue if v.get("status") == "pending"), None)
    if a_publier is None:
        print("File d'attente vide : aucune video en attente.")
        return

    print(f"Publication de : {a_publier['id']}")
    cid = compte_id(pseudo)

    try:
        reponse = publier(cid, a_publier["url"], a_publier["caption"])
    except RuntimeError as e:
        a_publier["status"] = "error"
        a_publier["error"] = str(e)
        with open(chemin_queue, "w", encoding="utf-8") as fh:
            json.dump(queue, fh, indent=2, ensure_ascii=False)
        print(f"ECHEC : {e}")
        sys.exit(1)

    post_id = reponse.get("post", {}).get("_id") or reponse.get("_id")
    a_publier["postId"] = post_id
    a_publier["attempts"] = a_publier.get("attempts", 0) + 1

    statut, erreur = attendre_statut(post_id)

    if statut == "published":
        a_publier["status"] = "published"
        a_publier["publishedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        a_publier.pop("error", None)
        sortie, code = f"Publie avec succes : {a_publier['id']}", 0

    elif statut == "failed":
        # La video n'est jamais sortie : on peut la remettre en file sans
        # risque de doublon. Au-dela de 3 tentatives on abandonne pour ne pas
        # bloquer la file sur une video systematiquement refusee.
        a_publier["error"] = erreur
        if a_publier["attempts"] >= 3:
            a_publier["status"] = "failed"
            sortie = f"ECHEC definitif apres {a_publier['attempts']} tentatives : {erreur}"
        else:
            a_publier["status"] = "pending"
            sortie = f"Echec TikTok ({erreur}) - remise en file, tentative {a_publier['attempts']}/3"
        code = 1

    else:
        # Statut indetermine : la video est peut-etre en ligne. On ne la remet
        # surtout pas en file (risque de doublon), on la signale pour controle.
        a_publier["status"] = "unconfirmed"
        a_publier["error"] = erreur
        sortie = f"Statut non confirme pour {a_publier['id']} ({erreur}) - a verifier a la main, PAS remise en file"
        code = 1

    with open(chemin_queue, "w", encoding="utf-8") as fh:
        json.dump(queue, fh, indent=2, ensure_ascii=False)

    print(sortie)
    sys.exit(code)


if __name__ == "__main__":
    main()
