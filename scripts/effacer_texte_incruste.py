#!/usr/bin/env python3
"""Efface le texte incruste par la source, sans toucher au reste de l'image.

POURQUOI
--------
`detecter_texte_incruste.py` SIGNALE les mentions d'ingredients que certains
comptes gravent dans l'image ("Sugar - 1 cup", "Refrigerate - 1 hour"). Il a
ecarte cinq sources sur treize le 2026-09-11 - dont trois dont le PLAT etait
neuf : tiramisu, gateau aux myrtilles, pain en cocotte. Les jeter pour
quelques secondes de texte revenait a se priver de bons sujets alors que le
vivier des comptes valides est epuise.

CE QUE FAIT LE SCRIPT
---------------------
Il reprend les passages reperes par le detecteur et floute LES BANDES ou le
texte apparait, uniquement pendant ces secondes-la. Ailleurs, l'image est
intacte.

Ce n'est pas un blanchiment de contenu tiers : la source reste la source, on
retire seulement une mention qui n'a pas de sens dans une video en francais et
qui, laissee telle quelle, signale la video comme reprise d'un autre compte.

PREMIERE VERSION, ET POURQUOI ELLE RATAIT (2026-09-12)
------------------------------------------------------
Elle cherchait LA bande la plus marquee de chaque passage et ne floutait que
celle-la. Sur la premiere source traitee, 7 passages ont ete floutes et 6
sont revenus a la verification : une mention d'ingredients tient sur DEUX A
TROIS lignes, donc sur plusieurs tranches de 150 px, et le detecteur retient
le maximum - il ne dit rien des autres. Flouter le maximum laissait le reste
intact et le controle le revoyait aussitot.

Desormais on releve TOUTES les tranches au-dessus du seuil, a plusieurs
instants du passage (le texte se deplace d'une etape a l'autre), et on
regroupe par BANDE plutot que par passage : un seul etage de filtre par
bande, avec la liste de ses intervalles. Le rendu reste un seul encodage.

CE QU'IL NE FAUT PAS LUI DEMANDER (mesure du 2026-09-12)
-------------------------------------------------------
Il ne rattrape PAS une source dont tout le deroule est legende. Les trois
sources nona.foodstory recuperees ce jour-la portaient du texte sur SIX des
douze tranches de l'image, reparties sur 7 passages couvrant 43 s : le flou
couvrait la moitie de la hauteur du cadre pendant les trois quarts de la
video. Regarde a l'image, le resultat ne passe pas - bandes grises a bords
francs, et le mot reste lisible sous le flou alors que la signature, elle,
est retombee sous le seuil.

C'est la limite a retenir : le SEUIL NE GARANTIT PAS LA DISPARITION A L'OEIL,
il garantit seulement que le detecteur ne voit plus rien. L'outil sert donc
aux sources qui portent UNE OU DEUX mentions localisees, pas a celles dont la
legende d'ingredients est le format. Les trois sources concernees restent
ecartees.

Usage :
    python scripts/effacer_texte_incruste.py video.mp4
    python scripts/effacer_texte_incruste.py --source brut.mp4 -o propre.mp4
"""

import argparse
import pathlib
import subprocess
import sys
import tempfile

ICI = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ICI))
import detecter_texte_incruste as det  # noqa: E402

# La bande floutee depasse un peu la zone mesuree : le detecteur travaille par
# tranches de 150 px et une lettre peut mordre sur la tranche voisine.
MARGE_TEMPS = 0.5
ECHANTILLONS = 4       # instants examines dans chaque passage
FLOU = "boxblur=24:5"  # valide le 2026-09-12 : ramene la signature sous 100

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")


def bandes_du_passage(video, debut, fin, source):
    """Toutes les tranches portant du texte pendant ce passage."""
    from PIL import Image
    tranches = det.TRANCHES_SOURCE if source else [det.BANDE_Y]
    instants = [debut + (fin - debut) * i / max(1, ECHANTILLONS - 1)
                for i in range(ECHANTILLONS)] if fin > debut else [debut]
    trouvees = set()
    with tempfile.TemporaryDirectory() as td:
        rep = pathlib.Path(td)
        for t in instants:
            img = rep / "i.png"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "%.3f" % t,
                            "-i", str(video), "-frames:v", "1",
                            "-vf", "scale=1080:1920", str(img)], check=True)
            im = Image.open(img)
            for y in tranches:
                tr = rep / "t.png"
                im.crop((0, y, 1080, y + det.BANDE_H)).save(tr)
                if det.signature(tr) >= det.SEUIL:
                    trouvees.add(y)
    return trouvees


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("-o", "--sortie")
    ap.add_argument("--source", action="store_true",
                    help="video brute : chercher le texte sur toute la hauteur")
    a = ap.parse_args()

    v = pathlib.Path(a.video)
    passages, _ = det.analyser(v, det.SEUIL, a.source)
    if not passages:
        print("aucun texte incruste : rien a faire")
        return 0

    # On regroupe par bande : une bande, un etage de filtre, N intervalles.
    par_bande = {}
    for d, f, n in passages:
        bandes = bandes_du_passage(v, d, f, a.source)
        if not bandes:                       # le pic est tombe entre deux tranches
            bandes = {det.BANDE_Y}
        print("  %6.2f -> %6.2f s   pic %5d   bandes y=%s"
              % (d, f, n, ",".join(str(y) for y in sorted(bandes))))
        for y in bandes:
            par_bande.setdefault(y, []).append(
                (max(0.0, d - MARGE_TEMPS), f + MARGE_TEMPS))

    etapes, entree = [], "[0:v]"
    for i, (y, creneaux) in enumerate(sorted(par_bande.items())):
        quand = "+".join("between(t,%.2f,%.2f)" % c for c in creneaux)
        etapes.append("%ssplit=2[a%d][b%d];"
                      "[b%d]crop=1080:%d:0:%d,%s[f%d];"
                      "[a%d][f%d]overlay=0:%d:enable='%s'[v%d]"
                      % (entree, i, i, i, det.BANDE_H, y, FLOU, i,
                         i, i, y, quand, i))
        entree = "[v%d]" % i

    sortie = pathlib.Path(a.sortie) if a.sortie else v.with_name(v.stem + "_net.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(v),
                    "-filter_complex", ";".join(etapes), "-map", entree,
                    "-map", "0:a?", "-c:v", "libx264", "-preset", "medium",
                    "-crf", "21", "-maxrate", "8000k", "-bufsize", "16000k",
                    "-pix_fmt", "yuv420p", "-c:a", "copy",
                    "-movflags", "+faststart", str(sortie)], check=True)

    reste, _ = det.analyser(sortie, det.SEUIL, a.source)
    print("%d passage(s), %d bande(s) floutee(s) -> %s"
          % (len(passages), len(par_bande), sortie.name))
    print("verification apres traitement : %s"
          % ("il reste %d passage(s)" % len(reste) if reste
             else "plus aucun texte detecte"))
    return 1 if reste else 0


if __name__ == "__main__":
    sys.exit(main())
