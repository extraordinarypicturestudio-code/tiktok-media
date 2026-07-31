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

    a_publier["status"] = "published"
    a_publier["publishedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    a_publier["postId"] = reponse.get("post", {}).get("_id") or reponse.get("_id")

    with open(chemin_queue, "w", encoding="utf-8") as fh:
        json.dump(queue, fh, indent=2, ensure_ascii=False)

    print(f"Publie avec succes : {a_publier['id']}")


if __name__ == "__main__":
    main()
