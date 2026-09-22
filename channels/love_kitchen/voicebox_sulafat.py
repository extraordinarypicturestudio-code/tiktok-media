#!/usr/bin/env python3
"""Installe la voix de love_kitchen dans Voicebox, et genere sans quota.

Pourquoi
--------
Le TTS Gemini plafonne a 10 requetes par jour et par modele : c'est la seule
contrainte qui limite la production de love_kitchen, et elle a deja coute des
journees entieres. Voicebox est un serveur LOCAL (127.0.0.1:17493, Qwen3-TTS)
sans quota et sans reseau.

Ce qui est clone ici est **Sulafat, une voix de synthese** - pas une personne
reelle. La regle du projet interdit le clonage vocal d'une personne
identifiable ; elle ne s'oppose pas a reproduire une voix de synthese que la
chaine utilise deja. C'est meme le seul moyen de changer de moteur SANS changer
l'identite sonore de la chaine, qui est celle de la video a 252 432 vues.

Usage :
    python voicebox_sulafat.py --installer          # cree le profil une fois
    python voicebox_sulafat.py --dire script.txt -o voix.wav
    python voicebox_sulafat.py --etat
"""

import argparse
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request
import uuid

B = "http://127.0.0.1:17493"
ICI = pathlib.Path(__file__).resolve().parent
MARQUEUR = ICI / ".voicebox_profil.json"
NOM_PROFIL = "love_kitchen — Sulafat"

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")


def appel(methode, chemin, corps=None, timeout=600):
    data = json.dumps(corps).encode() if corps is not None else None
    r = urllib.request.Request(
        B + chemin, data=data, method=methode,
        headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(r, timeout=timeout) as rep:
        brut = rep.read()
        return json.loads(brut) if brut else {}


def envoyer_fichier(chemin, fichier, champs=None, timeout=900):
    """POST multipart : Voicebox veut le fichier ET son texte de reference."""
    lim = "----" + uuid.uuid4().hex
    corps = b""
    for k, v in (champs or {}).items():
        corps += ('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                  % (lim, k, v)).encode("utf-8")
    corps += ('--%s\r\nContent-Disposition: form-data; name="file"; '
              'filename="%s"\r\nContent-Type: audio/wav\r\n\r\n'
              % (lim, pathlib.Path(fichier).name)).encode("utf-8")
    corps += pathlib.Path(fichier).read_bytes() + ("\r\n--%s--\r\n" % lim).encode()
    r = urllib.request.Request(
        B + chemin, data=corps, method="POST",
        headers={"Content-Type": "multipart/form-data; boundary=" + lim})
    with urllib.request.urlopen(r, timeout=timeout) as rep:
        brut = rep.read()
        return json.loads(brut) if brut else {}


def transcrire(wav):
    return envoyer_fichier("/transcribe", wav)


def installer(echantillon):
    """Cree le profil et lui donne l'echantillon Sulafat + sa transcription."""
    t = transcrire(echantillon)
    texte = (t.get("text") or t.get("transcription") or "").strip()
    if not texte:
        sys.exit("transcription vide : %s" % json.dumps(t, ensure_ascii=False)[:200])
    print("texte de reference (%d mots) : %s" % (len(texte.split()), texte[:120]))

    p = appel("POST", "/profiles", {
        "name": NOM_PROFIL,
        "description": "Voix de synthese Sulafat, reprise du moteur Gemini pour "
                       "garder l'identite sonore de la chaine sans quota.",
        "language": "fr",
        "voice_type": "cloned",
    })
    pid = p.get("id") or p.get("profile_id")
    print("profil cree :", pid)

    s = envoyer_fichier("/profiles/%s/samples" % pid, echantillon,
                        {"reference_text": texte})
    print("echantillon ajoute :", json.dumps(s, ensure_ascii=False)[:160])
    MARQUEUR.write_text(json.dumps({"profile_id": pid, "reference": texte},
                                   ensure_ascii=False), encoding="utf-8")
    return pid


def profil():
    if MARQUEUR.exists():
        return json.loads(MARQUEUR.read_text(encoding="utf-8"))["profile_id"]
    for p in appel("GET", "/profiles"):
        if p.get("name") == NOM_PROFIL:
            return p.get("id") or p.get("profile_id")
    sys.exit("profil absent : lancer --installer")


def dire(texte, sortie, taille="0.6B", instruct=None, pid=None):
    """Genere la voix et ecrit le wav. Renvoie la duree en secondes."""
    debut = time.time()
    corps = {"profile_id": pid or profil(), "text": texte, "language": "fr",
             "model_size": taille}
    # `instruct` est la consigne de jeu. Sans elle le clone rend la reference
    # telle quelle : l'utilisateur a juge la voix "blasee", "elle prolonge un
    # peu trop sa voix". C'est le seul levier qui agit sur le jeu sans toucher
    # a l'echantillon.
    if instruct:
        corps["instruct"] = instruct
    g = appel("POST", "/generate", corps)
    gid = g.get("generation_id") or g.get("id")
    # On interroge /history plutot que /generate/{id}/status : c'est le seul
    # des deux qui renvoie toujours du JSON exploitable.
    for _ in range(240):
        st = appel("GET", "/history/%s" % gid)
        e = st.get("status")
        if e in ("completed", "done", "success"):
            print("   %.1fs d'audio" % (st.get("duration") or 0))
            break
        if e in ("failed", "error", "cancelled"):
            sys.exit("generation en echec : %s" % json.dumps(st, ensure_ascii=False)[:250])
        time.sleep(5)
    else:
        sys.exit("generation trop longue")
    with urllib.request.urlopen(B + "/audio/%s" % gid, timeout=300) as r:
        pathlib.Path(sortie).write_bytes(r.read())
    return time.time() - debut


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--installer", metavar="ECHANTILLON.wav")
    a.add_argument("--dire", metavar="SCRIPT.txt")
    a.add_argument("-o", "--sortie", default="voix.wav")
    a.add_argument("--taille", default="0.6B")
    a.add_argument("--etat", action="store_true")
    o = a.parse_args()

    if o.etat:
        print(json.dumps(appel("GET", "/health"), ensure_ascii=False, indent=1))
        print("profils :", json.dumps(appel("GET", "/profiles"), ensure_ascii=False)[:400])
        return
    if o.installer:
        installer(o.installer)
        return
    if o.dire:
        texte = pathlib.Path(o.dire).read_text(encoding="utf-8").strip()
        d = dire(texte, o.sortie, o.taille)
        print("ecrit %s en %.0f s" % (o.sortie, d))
        return
    a.print_help()


if __name__ == "__main__":
    main()
