#!/usr/bin/env python3
"""
PREPARATION COMPLETE D'UN CLIP AVANT MISE EN FILE.

Enchaine, dans l'ordre, les etapes qui etaient jusqu'ici manuelles et donc
oubliees une fois sur deux :

  1. normalisation 1080x1920
  2. CONTROLE (controle_publication.py) sur le clip SOURCE
  3. ajout de l'OUTRO de la chaine, seulement si le controle passe

L'ordre compte : le controle tourne AVANT l'outro. Sinon il detecte notre
propre outro comme un watermark et un texte incruste - ce qu'il est
techniquement, mais c'est notre marque, ajoutee volontairement. Controler
apres refuserait 100% des clips.

Les etapes 2 et 3 ont chacune deja fait defaut en production :
  - l'outro : oublie sur plusieurs jours de publications (signale le
    06/08/2026), puis a nouveau le 09/08/2026 ;
  - le controle : absent, ce qui a laisse passer des clips avec watermark
    (@MillaChats), sous-titres incrustes et logos de source, l'OCR les ayant
    declares "propres".

Les chainer dans un seul script est le seul moyen fiable de ne plus les
sauter : il n'y a plus d'ordre a se rappeler.

Usage :
  python preparer_publication.py --chaine toprank        <clip.mp4> [...]
  python preparer_publication.py --chaine recipecrave    --dossier lot/PROPRES
  python preparer_publication.py --chaine nextlevelplays <clip.mp4> -o sortie/

Sortie : les clips prets vont dans <sortie>/, les refuses sont listes avec
leur motif et ne sont PAS ecrits. Code de sortie 1 si au moins un refus.
"""

import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)

from controle_publication import controler  # noqa: E402

W, H = 1080, 1920

# Chaque chaine a son outro et son profil de controle. Le profil "cuisine"
# rend le visage bloquant (format "mains uniquement" de recipe_crave) ;
# "sport" ne le bloque pas, un skateur ayant forcement un visage.
CHAINES = {
    "toprank":        {"outro": "outro_toprank.mp4",        "profil": "sport"},
    "recipecrave":    {"outro": "outro_recipecrave.mp4",    "profil": "cuisine"},
    "nextlevelplays": {"outro": "outro_nextlevelplays.mp4", "profil": "sport"},
}


def _duree(v):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", v],
        capture_output=True, text=True)
    return float(out.stdout.strip())


def _a_de_l_audio(v):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", v],
        capture_output=True, text=True)
    return "audio" in out.stdout


def normaliser(source, dest):
    """Ramene a 1080x1920. Les sources verticales sont completees par des
    bandes, les horizontales recadrees au centre : letterboxer une video
    16:9 dans un cadre 9:16 laisserait deux enormes bandes noires."""
    largeur, hauteur = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", source],
        capture_output=True, text=True).stdout.strip().split("x")
    horizontale = int(largeur) > int(hauteur)

    if horizontale:
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
              f"crop={W}:{H},setsar=1")
    else:
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
              f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1")

    cmd = ["ffmpeg", "-y", "-i", source, "-vf", vf,
           "-c:v", "libx264", "-preset", "medium", "-crf", "21"]
    if _a_de_l_audio(source):
        cmd += ["-c:a", "aac", "-b:a", "160k"]
    else:
        # Pas d'audio en entree : on n'en fabrique pas. Le controle final
        # refusera le clip, et c'est voulu - une video muette sort muette
        # sur TikTok, ce n'est pas rattrapable en ajoutant du silence.
        cmd += ["-an"]
    cmd += ["-loglevel", "error", dest]
    subprocess.run(cmd, check=True)
    return dest


def ajouter_outro(video, outro, dest):
    """Concatene video + outro en normalisant les deux entrees.

    Le concat demuxer exigerait des flux strictement identiques ; on passe
    donc par filter_complex. Si la video n'a pas d'audio, on injecte du
    silence sur sa portion, sinon concat refuse de mixer une entree muette
    avec l'outro qui, lui, a une piste.
    """
    audio = _a_de_l_audio(video)
    entrees = ["-i", video, "-i", outro]
    filtres = [
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1[v0]",
        f"[1:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1[v1]",
    ]
    if audio:
        filtres.append("[v0][0:a][v1][1:a]concat=n=2:v=1:a=1[outv][outa]")
    else:
        entrees += ["-f", "lavfi", "-t", str(_duree(video)),
                    "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
        filtres.append("[v0][2:a][v1][1:a]concat=n=2:v=1:a=1[outv][outa]")

    subprocess.run(
        ["ffmpeg", "-y"] + entrees +
        ["-filter_complex", ";".join(filtres),
         "-map", "[outv]", "-map", "[outa]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "21",
         "-c:a", "aac", "-b:a", "160k", "-loglevel", "error", dest],
        check=True)
    return dest


def preparer(source, chaine, dossier_sortie):
    conf = CHAINES[chaine]
    outro = os.path.join(ICI, "outros", conf["outro"])
    if not os.path.exists(outro):
        raise RuntimeError(
            f"outro introuvable : {outro}\n"
            f"Le regenerer avec : python generer_outros.py {chaine}")

    nom = os.path.basename(source)
    tmp = tempfile.mkdtemp(prefix="prep_")
    try:
        norm = normaliser(source, os.path.join(tmp, "norm.mp4"))

        # Controle AVANT l'outro : notre outro contient volontairement un
        # pseudo et du texte, que le controle qualifierait de watermark.
        rapport = controler(norm, conf["profil"])
        rapport["fichier"] = nom

        if rapport["verdict"] == "OK":
            avec_outro = ajouter_outro(norm, outro, os.path.join(tmp, "final.mp4"))
            os.makedirs(dossier_sortie, exist_ok=True)
            dest = os.path.join(dossier_sortie, nom)
            shutil.copy(avec_outro, dest)
            rapport["sortie"] = dest
            rapport["outro"] = conf["outro"]
        return rapport
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("videos", nargs="*")
    p.add_argument("--chaine", required=True, choices=sorted(CHAINES))
    p.add_argument("--dossier", help="prend tous les .mp4 de ce dossier")
    p.add_argument("-o", "--out", default="PRETS",
                   help="dossier de sortie (defaut : PRETS)")
    a = p.parse_args()

    videos = list(a.videos)
    if a.dossier:
        videos += sorted(glob.glob(os.path.join(a.dossier, "*.mp4")))
    if not videos:
        p.error("aucune video a preparer")

    prets, refuses = [], []
    for v in videos:
        print(f"... {os.path.basename(v)}")
        r = preparer(v, a.chaine, a.out)
        (prets if r["verdict"] == "OK" else refuses).append(r)

    print()
    for r in prets:
        f = r["format"]
        print(f"[PRET ] {r['fichier']}  ({f['duree']}s, outro inclus)")
    for r in refuses:
        print(f"[REFUS] {r['fichier']}")
        for m in r["motifs_refus"]:
            print(f"         -> {m}")
        d = r["visuel"].get("details")
        if d:
            print(f"         vu : {d}")

    print(f"\n{len(prets)} pret(s) dans {a.out}/, {len(refuses)} refuse(s).")
    sys.exit(1 if refuses else 0)


if __name__ == "__main__":
    main()
