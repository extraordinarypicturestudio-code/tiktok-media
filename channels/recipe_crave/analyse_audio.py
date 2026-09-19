#!/usr/bin/env python3
"""Le son d'origine est-il conservable ?

    python analyse_audio.py source.mp4 [autre.mp4 ...]

Demande de l'utilisateur du 2026-08-20 : "s'il n'y a aucun bruit, juste du
coupage, tu peux laisser comme tel ou mettre une legere musique en arriere
plan". Garder les bruits de cuisine, c'est garder ce qui fait l'interet du
format ASMR - c'est ce que font les chaines de reference du creneau.

Mais une regle plus ancienne tient toujours : le son d'origine ne doit RIEN
contenir de copyrighte. On ne conserve donc la piste que si elle ne contient
ni parole ni musique.

Une premiere version mesurait la platitude spectrale et la periodicite de
l'enveloppe. Calibree sur des temoins, elle s'est revelee inutilisable : le
hachage rythmique d'une video ASMR sort a 0.64 de periodicite, au-dessus
d'une vraie musique Pixabay a 0.49. Un couteau qui coupe en cadence, c'est
periodique. Le modele de vision-audio, lui, distingue les deux sans erreur
sur les memes temoins ; c'est donc lui qui tranche.

En cas d'echec de l'analyse, on REMPLACE la piste. Un faux positif coute une
musique Pixabay, un faux negatif coute un signalement.
"""

import base64
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import urllib.request

RACINE = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RACINE))

from gemini_check import GEMINI_MODEL, _cle_depuis_env  # noqa: E402

URL = (f"https://generativelanguage.googleapis.com/v1beta/models/"
       f"{GEMINI_MODEL}:generateContent")

PROMPT = (
    "Ecoute cet extrait audio d'une video de cuisine. Reponds en JSON strict :\n"
    '{"musique": true/false, "parole": true/false, "bruits_de_cuisine": '
    'true/false, "description": "10 mots"}\n\n'
    "musique = true s'il y a une piste musicale, meme en fond, meme discrete.\n"
    "parole = true si une personne parle.\n"
    "bruits_de_cuisine = true si on entend couper, frire, verser, remuer."
)


def analyser(fichier, secondes=30):
    """Classe la bande son. Leve une exception si l'analyse echoue."""
    cle = _cle_depuis_env()
    if not cle:
        raise RuntimeError("GEMINI_API_KEY absente")
    with tempfile.TemporaryDirectory() as tmp:
        extrait = os.path.join(tmp, "extrait.mp3")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(fichier),
                        "-t", str(secondes), "-vn", "-ac", "1", "-ar", "16000",
                        "-b:a", "64k", extrait], capture_output=True)
        if not os.path.exists(extrait):
            raise RuntimeError("extraction audio impossible")
        donnees = base64.b64encode(pathlib.Path(extrait).read_bytes()).decode()

    charge = {"contents": [{"parts": [
        {"text": PROMPT},
        {"inline_data": {"mime_type": "audio/mpeg", "data": donnees}}]}],
        "generationConfig": {"responseMimeType": "application/json"}}
    req = urllib.request.Request(f"{URL}?key={cle}",
                                 data=json.dumps(charge).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        rep = json.loads(r.read())
    return json.loads(rep["candidates"][0]["content"]["parts"][0]["text"])


def son_conservable(fichier):
    """Renvoie (bool, detail). True = la piste d'origine peut etre gardee."""
    if not subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", str(fichier)],
            capture_output=True, text=True).stdout.strip():
        return False, "la source n'a pas de piste audio"
    try:
        r = analyser(fichier)
    except Exception as e:
        return False, f"analyse impossible ({e}) : on ne garde pas un son non verifie"

    if r.get("parole"):
        return False, f"voix presente ({r.get('description')})"
    if r.get("musique"):
        return False, f"musique presente ({r.get('description')})"
    if not r.get("bruits_de_cuisine"):
        return False, f"aucun bruit de cuisine exploitable ({r.get('description')})"
    return True, f"bruits de cuisine seuls ({r.get('description')})"


def main():
    for chemin in sys.argv[1:]:
        ok, detail = son_conservable(pathlib.Path(chemin))
        etiquette = "GARDER   " if ok else "REMPLACER"
        print(f"{etiquette}  {pathlib.Path(chemin).name:34} {detail}")


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    main()
