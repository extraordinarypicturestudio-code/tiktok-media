#!/usr/bin/env python3
"""Empreinte visuelle : cette video est-elle deja sortie ?

    python empreintes_video.py indexer          # relit tout l'historique git
    python empreintes_video.py verifier clip.mp4 [autre.mp4 ...]

Le controle de doublon existant compare des sha256 : il ne voit QUE le fichier
identique au bit pres. Une meme video re-telechargee, recadree ou reencodee
passe a travers, et l'utilisateur se retrouve avec la meme recette publiee
deux fois (mise en garde du 2026-08-20).

L'empreinte ici est perceptuelle : 12 images reparties sur la duree, chacune
reduite en 8x8 niveaux de gris et transformee en 64 bits (chaque bit dit si le
pixel est au-dessus de la moyenne de l'image). Le recadrage, le reetalonnage,
le changement de definition et le reencodage ne changent presque rien a cette
signature ; un autre plat la change entierement.

L'index est construit depuis l'HISTORIQUE GIT du depot : les clips publies
sont supprimes du dossier apres publication, mais restent dans l'historique.
"""

import argparse
import json
import pathlib
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

RACINE = pathlib.Path(__file__).resolve().parent
DEPOT = RACINE.parent / "tiktok-media-work"
INDEX = RACINE / "empreintes_publiees.json"

IMAGES = 12          # points de mesure repartis sur la duree
BITS_PAR_IMAGE = 64
# Seuil de ressemblance : sur 768 bits, deux videos differentes tournent
# autour de 50% de bits differents ; le meme contenu reencode reste sous 12%.
SEUIL_DISTANCE = 0.18

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, **kw)


def _duree(fichier):
    r = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
              "-of", "default=nw=1:nk=1", str(fichier)], text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def empreinte(fichier):
    """Signature perceptuelle : IMAGES x 64 bits, en hexadecimal."""
    duree = _duree(fichier)
    if duree <= 0:
        return None
    bits = []
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(IMAGES):
            # On evite les extremites : generique de debut et outro de fin.
            t = duree * (0.08 + 0.84 * i / max(IMAGES - 1, 1))
            img = pathlib.Path(tmp) / f"{i}.png"
            _run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}",
                  "-i", str(fichier), "-frames:v", "1", "-vf", "scale=8:8",
                  str(img)])
            if not img.exists():
                continue
            with Image.open(img) as im:
                g = np.asarray(im.convert("L"), dtype=np.float32)
            bits.append((g > g.mean()).flatten())
    if len(bits) < IMAGES // 2:
        return None
    plat = np.concatenate(bits)
    octets = np.packbits(plat).tobytes()
    return octets.hex()


def distance(a, b):
    """Part de bits differents entre deux empreintes (0 = identique)."""
    if not a or not b:
        return 1.0
    x = np.unpackbits(np.frombuffer(bytes.fromhex(a), dtype=np.uint8))
    y = np.unpackbits(np.frombuffer(bytes.fromhex(b), dtype=np.uint8))
    n = min(x.size, y.size)
    if n == 0:
        return 1.0
    return float(np.count_nonzero(x[:n] != y[:n]) / n)


def _clips_de_lhistorique():
    """(chemin, sha du blob) de tous les clips ayant existe dans le depot."""
    r = _run(["git", "-C", str(DEPOT), "log", "--all", "--diff-filter=AM",
              "--name-only", "--pretty=format:"], text=True, errors="replace")
    chemins = {l.strip() for l in r.stdout.splitlines()
               if l.strip().endswith(".mp4") and l.startswith("clips")}
    trouves = []
    for chemin in sorted(chemins):
        rev = _run(["git", "-C", str(DEPOT), "rev-list", "--all", "-1", "--", chemin],
                   text=True).stdout.strip()
        if rev:
            trouves.append((chemin, rev))
    return trouves


def indexer():
    index = json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else {}
    clips = _clips_de_lhistorique()
    print(f"{len(clips)} clip(s) trouves dans l'historique du depot")
    nouveaux = 0
    for chemin, rev in clips:
        cle = f"{chemin}@{rev[:12]}"
        if cle in index:
            continue
        blob = _run(["git", "-C", str(DEPOT), "show", f"{rev}:{chemin}"]).stdout
        if not blob:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            f = pathlib.Path(tmp) / "clip.mp4"
            f.write_bytes(blob)
            e = empreinte(f)
        if e:
            index[cle] = {"fichier": pathlib.Path(chemin).name, "empreinte": e}
            nouveaux += 1
            print(f"  + {pathlib.Path(chemin).name}")
    INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{nouveaux} nouvelle(s) empreinte(s), {len(index)} au total -> {INDEX}")


def chercher_doublon(fichier, seuil=None):
    """Renvoie (nom_du_doublon, distance) ou (None, distance_min).

    `seuil` permet a une chaine de resserrer la comparaison. Sur un format
    ou toutes les videos sont un plan fixe de quelqu'un qui parle devant un
    fond floute, l'empreinte ne discrimine plus a 0.18 : le 2026-08-24,
    mindshift_denzelwashington_v1 a ete declare identique a
    espritlibre_oprahwinfrey_v1 (0.124), et le 2026-09-06 un discours UCLA
    l'a ete a espritlibre_matthewhussey2_v1 (0.150) - des personnes
    differentes a chaque fois. Les VRAIS appariements de ce catalogue
    tiennent entre 0.001 et 0.044.
    """
    if not INDEX.exists():
        return None, 1.0
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    e = empreinte(fichier)
    if not e:
        return None, 1.0
    meilleur, d_min = None, 1.0
    for entree in index.values():
        d = distance(e, entree["empreinte"])
        if d < d_min:
            d_min, meilleur = d, entree["fichier"]
    s = SEUIL_DISTANCE if seuil is None else seuil
    return (meilleur, d_min) if d_min <= s else (None, d_min)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sous = p.add_subparsers(dest="action", required=True)
    sous.add_parser("indexer")
    v = sous.add_parser("verifier")
    v.add_argument("videos", nargs="+")
    a = p.parse_args()

    if a.action == "indexer":
        indexer()
        return 0
    code = 0
    for chemin in a.videos:
        nom, d = chercher_doublon(pathlib.Path(chemin))
        if nom:
            print(f"DOUBLON   {pathlib.Path(chemin).name} = {nom} (distance {d:.3f})")
            code = 1
        else:
            print(f"inedit    {pathlib.Path(chemin).name} (plus proche : {d:.3f})")
    return code


if __name__ == "__main__":
    sys.exit(main())
