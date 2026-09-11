#!/usr/bin/env python3
"""Trouve le texte INCRUSTE PAR LA SOURCE dans une video finie.

POURQUOI
--------
Le 2026-09-10, une video love_kitchen est partie avec six incrustations
d'ingredients en anglais ("Egg whites - 6", "Sugar - 1 cup", "Dulce de leche
- 2 tbsp"...). L'utilisateur les a vues ; aucun controle ne les voyait.

Le profil `lovekitchen` de `controle_publication.py` ne bloque que
`mineur_visible` et `logo_marque` - c'est un choix deliberé (le profil
`cuisine` refusait 19 videos sur 26 en prenant nos propres sous-titres pour
du texte tiers). La video est donc passee au vert deux fois.

POURQUOI PAS L'OCR
------------------
Teste le meme jour sur cette video : tesseract ne trouvait que 2 des 6
passages. Le texte est blanc sur des fonds souvent clairs (creme, farine,
plan de travail gris), et la confiance tombe sous le seuil. Trois valeurs de
`--psm` donnaient trois resultats differents.

CE QU'ON MESURE A LA PLACE
--------------------------
Ces incrustations sont TOUJOURS du blanc pur cerne de noir - c'est ce qui les
rend lisibles sur n'importe quel fond. Cette coexistence ne se produit pas
dans une image de cuisine : ni la creme (blanche sans cerne), ni une ombre
(noire sans blanc) ne la produisent.

Mesure sur la video du 2026-09-10, bande centrale, 284 images :

    mediane                 0
    90e centile           341
    les six passages   2 281 a 3 498

La separation est franche, et le resultat est stable de 400 a 900. Seuil a
900, soit deux fois et demie le 90e centile.

NOS PROPRES SOUS-TITRES ne declenchent rien : ils sont plus bas dans l'image
(la bande examinee s'arrete au-dessus) et le controle sert justement a
distinguer ce qui vient de la source de ce qui vient de nous.

Usage :
    python scripts/detecter_texte_incruste.py video.mp4 [video2.mp4 ...]
    python scripts/detecter_texte_incruste.py --dossier clips-lovekitchen
"""

import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile

SEUIL = 900
FPS = 4
# Bande examinee : le tiers median-bas de l'image, AU-DESSUS de nos
# sous-titres. C'est la que les comptes sources posent leurs mentions.
BANDE_H = 150
BANDE_Y = 1385

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")


def signature(chemin_png):
    import numpy as np
    from PIL import Image
    a = np.asarray(Image.open(chemin_png).convert("L"), dtype=np.int16)
    blanc = a >= 245
    noir = a <= 60
    proche = noir.copy()
    for k in (1, 2, 3):
        proche[:-k] |= noir[k:]
        proche[k:] |= noir[:-k]
        proche[:, :-k] |= noir[:, k:]
        proche[:, k:] |= noir[:, :-k]
    return int((blanc & proche).sum())


# MODE SOURCE. Sur une video FINIE, on n'examine que la bande au-dessus de
# nos sous-titres. Sur une SOURCE brute, il n'y a pas encore de sous-titres a
# nous : on examine toute la hauteur, par tranches de 150 px, et on garde la
# pire tranche de chaque image. C'est ce qui permet d'ecarter une source AVANT
# d'ecrire son script et de depenser deux requetes TTS dessus.
TRANCHES_SOURCE = list(range(100, 1800, 150))


def analyser(video, seuil=SEUIL, source=False):
    """[(debut, fin, pic)] des passages portant du texte incruste."""
    travail = pathlib.Path(tempfile.mkdtemp(prefix="txt_"))
    try:
        if source:
            vf = "fps=%d,scale=1080:1920" % FPS
        else:
            vf = "fps=%d,crop=1080:%d:0:%d" % (FPS, BANDE_H, BANDE_Y)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video),
                        "-vf", vf, str(travail / "b_%05d.png")], check=True)
        mesures = []
        for f in sorted(travail.glob("b_*.png")):
            t = int(f.stem.split("_")[1]) / FPS
            if not source:
                mesures.append((t, signature(f)))
                continue
            from PIL import Image
            im = Image.open(f)
            pire = 0
            for y in TRANCHES_SOURCE:
                tr = travail / "_tr.png"
                im.crop((0, y, 1080, y + BANDE_H)).save(tr)
                pire = max(pire, signature(tr))
            mesures.append((t, pire))
    finally:
        shutil.rmtree(travail, ignore_errors=True)

    passages = []
    for t, n in mesures:
        if n < seuil:
            continue
        if passages and t - passages[-1][1] <= 1.5:
            passages[-1][1] = t
            passages[-1][2] = max(passages[-1][2], n)
        else:
            passages.append([t, t, n])
    return passages, mesures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("videos", nargs="*")
    ap.add_argument("--dossier")
    ap.add_argument("--seuil", type=int, default=SEUIL)
    ap.add_argument("--source", action="store_true",
                    help="source brute : toute la hauteur, pas seulement la bande")
    a = ap.parse_args()

    cibles = [pathlib.Path(v) for v in a.videos]
    if a.dossier:
        cibles += sorted(pathlib.Path(a.dossier).glob("*.mp4"))
    if not cibles:
        sys.exit("aucune video donnee")

    sales = 0
    for v in cibles:
        passages, mesures = analyser(v, a.seuil, a.source)
        pire = max((n for _, n in mesures), default=0)
        if passages:
            sales += 1
            print("%-34s %d passage(s), pic %d" % (v.name, len(passages), pire))
            for d, f, n in passages:
                print("      %6.2f -> %6.2f s   (%d)" % (d, f, n))
        else:
            print("%-34s propre (pic %d, seuil %d)" % (v.name, pire, a.seuil))
    print("\n%d video(s) sur %d portent du texte incruste" % (sales, len(cibles)))
    return 1 if sales else 0


if __name__ == "__main__":
    sys.exit(main())
