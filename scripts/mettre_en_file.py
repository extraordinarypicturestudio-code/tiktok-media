#!/usr/bin/env python3
"""Seule porte d'entree autorisee vers une file de publication.

    python mettre_en_file.py --chaine recipecrave \
        --legende "Focaccia maison, la pate qui leve toute seule" \
        --source lot/RAW/focaccia.mp4  PRETS/focaccia.mp4

Il fait trois choses, dans cet ordre, et s'arrete a la premiere qui echoue :

  1. lance verification_publication.py sur le fichier ;
  2. copie le clip dans le dossier de la chaine, dans le depot ;
  3. ajoute l'entree a la file AVEC UN TAMPON contenant le sha256 du fichier
     verifie et le detail des controles passes.

publish_next.py refuse toute entree sans tampon valide, et recalcule le
sha256 avant l'envoi : un tampon recopie d'une autre entree ou un fichier
remplace apres coup ne sort pas.

Ajouter une entree a la main dans le JSON ne suffit donc plus : c'etait
possible avant le 19/08/2026, et c'est comme ca qu'un lot de clips de 16 s
avec des legendes ne correspondant pas aux videos est parti en production.
"""

import argparse
import hashlib
import json
import pathlib
import shutil
import sys
import time

RACINE = pathlib.Path(__file__).resolve().parent
DEPOT = RACINE.parent / "tiktok-media-work"
sys.path.insert(0, str(RACINE))

from verification_publication import CHAINES, verifier  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = ("https://raw.githubusercontent.com/extraordinarypicturestudio-code/"
            "tiktok-media/main/")

# Ou vivent les clips et la file de chaque chaine, dans le depot.
DESTINATIONS = {
    "recipecrave": ("clips", "queue-recipecrave.json"),
    "espritlibre": ("clips-espritlibre", "queue-espritlibre.json"),
    "mindshift": ("clips-mindshift", "queue-mindshift.json"),
    "toprank": ("clips-toprank", "queue-toprank.json"),
    "nextlevelplays": ("clips-nextlevelplays", "queue-nextlevelplays.json"),
    # Ajoutees le 2026-09-05. Ces deux chaines generatives programmaient
    # directement sur Zernio, des heures a l'avance, sans passer par une file.
    # Consequence : un refus de TikTok au declenchement (saturation) faisait
    # perdre le creneau SEC, alors que sur les chaines a file la video revient
    # en `pending` et un autre candidat prend sa place dans le meme run.
    # Deux sorties argile ont ete perdues comme ca les 31/08 et 02/09.
    "argile": ("clips-argile", "queue-argile.json"),
    "lovekitchen": ("clips-lovekitchen", "queue-lovekitchen.json"),
}


def sha256(chemin):
    h = hashlib.sha256()
    with open(chemin, "rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


def tampon(rapport, empreinte):
    return {
        "verdict": rapport["verdict"],
        "sha256": empreinte,
        "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "outil": "verification_publication.py",
        "preuves": {nom: c["preuve"] for nom, c in rapport["controles"].items()},
    }


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("video")
    p.add_argument("--chaine", required=True, choices=sorted(CHAINES))
    p.add_argument("--legende", required=True)
    p.add_argument("--source", help="fichier d'origine. FORTEMENT recommande : "
                        "sans lui, la bande son ne peut pas etre recoupee "
                        "avec l'original")
    p.add_argument("--id", help="identifiant dans la file (defaut : nom du fichier)")
    p.add_argument("--republication", metavar="MOTIF",
                   help="autorise une video DEJA PUBLIEE a repartir, sur une "
                        "autre chaine (migration). Le controle de doublon "
                        "cesse d'etre bloquant pour cette entree UNIQUEMENT, "
                        "et le motif est inscrit dans le tampon. Ne jamais "
                        "s'en servir pour reposter sur la meme chaine.")
    a = p.parse_args()

    video = pathlib.Path(a.video).resolve()
    if not video.exists():
        sys.exit(f"introuvable : {video}")
    ident = a.id or video.stem

    print(f"Verification de {video.name}...")
    rapport = verifier(str(video), a.chaine, a.legende, a.source)

    # Migration d'une chaine a l'autre : la video EST un doublon de ce qui a
    # ete publie ailleurs, et c'est voulu. On leve ce seul controle, on le
    # dit a l'ecran, et on l'ecrit dans le tampon pour que la trace reste
    # lisible dans la file six mois plus tard. Tous les autres controles
    # gardent leur pouvoir de rejet.
    if a.republication and rapport["controles"]["doublon"]["problemes"]:
        rapport["controles"]["doublon"]["bloquant"] = False
        rapport["controles"]["doublon"]["preuve"] += (
            f" | republication autorisee : {a.republication}")
        rapport["motifs_refus"] = [
            f"[{nom}] {pb}" for nom, c in rapport["controles"].items()
            if c["bloquant"] for pb in c["problemes"]]
        rapport["verdict"] = "OK" if not rapport["motifs_refus"] else "REJET"
        print(f"  (doublon tolere : {a.republication})")
    for nom, c in rapport["controles"].items():
        etat = "OK " if not c["problemes"] else "NON"
        print(f"  [{etat}] {nom:9} {c['preuve']}")
        for pb in c["problemes"]:
            print(f"         -> {pb}")
    if rapport["verdict"] != "OK":
        sys.exit(f"\nREJET : {video.name} n'entre pas en file.")

    dossier, fichier_file = DESTINATIONS[a.chaine]
    (DEPOT / dossier).mkdir(parents=True, exist_ok=True)
    dest = DEPOT / dossier / f"{ident}.mp4"
    shutil.copy2(video, dest)

    chemin_file = DEPOT / fichier_file
    file = json.loads(chemin_file.read_text(encoding="utf-8"))
    if any(x["id"] == ident for x in file):
        sys.exit(f"'{ident}' est deja dans {fichier_file}")
    file.append({
        "id": ident,
        "url": BASE_URL + f"{dossier}/{ident}.mp4",
        "caption": a.legende,
        "status": "pending",
        "controle_visuel": True,
        "attempts": 0,
        "verification": tampon(rapport, sha256(dest)),
    })
    chemin_file.write_text(json.dumps(file, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    en_attente = sum(1 for x in file if x["status"] == "pending")
    print(f"\nOK : '{ident}' ajoute a {fichier_file} ({en_attente} en attente).")


if __name__ == "__main__":
    main()
