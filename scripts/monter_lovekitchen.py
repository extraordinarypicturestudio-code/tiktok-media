#!/usr/bin/env python3
"""Remonte les videos love_kitchen en attente, sans machine locale.

POURQUOI
--------
La chaine de montage vivait dans "Project 1 TIKTOK", sur le PC de
l'utilisateur : rien ne pouvait etre produit pendant son absence, et sept
videos refusees par le controle d'intonation attendaient qu'on les revoice a
la main. La demande du 2026-09-12 est explicite : *utilisation quotidienne en
mon absence, PC eteint, avec Gemini et de nouvelles videos*.

GitHub Actions repond a ca - le depot est PUBLIC, donc les minutes sont
gratuites et illimitees - a condition que TOUT soit dans le depot : le
montage, les intros (pre-decoupees), les scripts, les sources, et la barriere
de mise en file. C'est fait depuis ce jour.

CE QUE FAIT CE PILOTE
---------------------
Pour chaque video que `sources.json` connait et que la file porte en attente :
montage -> souffle de fond -> controle d'image -> outro -> barriere. Une video
qui echoue a une etape laisse sa place a la suivante, elle ne fait pas tomber
le lot.

DEUX GARDES PROPRES A L'EXECUTION SANS SURVEILLANCE
---------------------------------------------------
1. **La voix doit etre Gemini Sulafat.** Le montage sait se rabattre sur
   edge-tts quand le quota est epuise ; c'est utile en atelier, c'est une
   faute en production - le 2026-09-12, 61-esterhazy est sortie avec une voix
   francaise DIFFERENTE, audible pour n'importe quel abonne. Un rendu qui
   n'est pas du Gemini est jete ici.
2. **On s'arrete au premier quota epuise**, sans sonder : un lot bien ecrit
   apprend la meme chose qu'une sonde sans rien gaspiller (regle 11 bis).
"""

import argparse
import json
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import detecter_texte_incruste as DET  # noqa: E402

RACINE = pathlib.Path(__file__).resolve().parent.parent
LK = RACINE / "channels" / "love_kitchen"
FILE = RACINE / "queue-lovekitchen.json"

# Le souffle de fond vient de `speechnorm`, qui remonte le bruit ENTRE les
# mots. Porte + debruiteur, mesures du 2026-09-12 : plancher -51,9 -> -62,6 dB,
# pic de voix et intonation inchanges.
SOUFFLE = ("agate=threshold=0.0079:ratio=6:attack=5:release=150:knee=4,"
           "afftdn=nr=10:nf=-50")

INTERESSANT = re.compile(
    r"essai|retenue|intonation|respiration|constance|APLATIT|RESPIRE"
    r"|429|indisponible|termine|intro ")

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")


def ffmpeg(args):
    return subprocess.run(["ffmpeg", "-v", "error", "-y"] + args,
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def python(args):
    return subprocess.run([sys.executable] + args, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def monter(conf, sortie, voix_secours=False):
    """Rend la video. Retourne (exploitable, journal complet)."""
    r = python([str(LK / "montage_lovekitchen.py"),
                "--intro-ref", "auto",
                "--corps", str(LK / conf["source"]),
                "--script", str(LK / conf["script"]),
                "--sortie", str(sortie)])
    j = (r.stdout or "") + (r.stderr or "")
    for ligne in j.splitlines():
        if INTERESSANT.search(ligne):
            print("      " + ligne.strip()[:130])
    if r.returncode != 0:
        return False, j
    # Garde n.1, et elle vise la voix RETENUE, pas le journal entier : le
    # montage essaie volontiers un tirage edge-tts en cours de route puis garde
    # quand meme le Gemini, qui le bat au classement. Chercher "edge-tts"
    # n'importe ou aurait jete de bons rendus sans le dire.
    retenue = [l for l in j.splitlines() if "voix retenue" in l]
    if not voix_secours:
        if not retenue:
            # Un controle qui n'a PAS PU tourner n'autorise pas la sortie : si
            # le montage change le libelle de cette ligne, on s'arrete plutot
            # que de laisser passer une voix inconnue.
            print("      REJET : impossible de savoir quelle voix a ete retenue")
            sortie.unlink(missing_ok=True)
            return False, j
        if "Gemini" not in retenue[-1]:
            print("      REJET : voix retenue %s - la chaine est sur Gemini Sulafat"
                  % retenue[-1].split(":")[-1].strip()[:40])
            sortie.unlink(missing_ok=True)
            return False, j
    return True, j


def finaliser(ident, video, legende, conf):
    """Souffle -> controle d'image -> outro -> barriere. True si en file."""
    net = video.with_name(video.stem + "_net.mp4")
    if ffmpeg(["-i", str(video), "-filter:a", SOUFFLE, "-c:v", "copy",
               "-c:a", "aac", "-b:a", "160k", str(net)]).returncode == 0:
        net.replace(video)
        print("      souffle de fond : traite")
    else:
        print("      souffle de fond : echec, on garde le rendu brut")

    # Texte incruste par la source. Absent de la premiere version de ce pilote
    # alors qu'il etait dans la chaine manuelle : c'est le controle ne des six
    # mentions d'ingredients en anglais parties en ligne le 2026-09-10, que
    # l'utilisateur avait vues et qu'aucun controle ne voyait. Il tourne AVANT
    # l'outro, comme tous les controles d'image - notre outro porte du texte.
    try:
        passages, _ = DET.analyser(video, DET.SEUIL, False)
    except Exception as ex:
        print("      REFUS : detection de texte impossible (%s)" % str(ex)[:50])
        return False
    if passages:
        print("      REFUS : %d passage(s) de texte incruste par la source"
              % len(passages))
        return False
    print("      texte incruste : aucun")

    c = python([str(RACINE / "pipeline" / "controle_publication.py"),
                "--profil", "lovekitchen", str(video)])
    verdict = [l for l in (c.stdout or "").splitlines() if l.startswith("[")]
    if not verdict or not verdict[0].startswith("[OK"):
        print("      REFUS image : %s" % (verdict[0] if verdict else "sans verdict"))
        return False
    print("      controle image : OK")

    avec = video.with_name(video.stem + "_outro.mp4")
    r = ffmpeg(["-i", str(video),
                "-i", str(RACINE / "outros" / "outro_lovekitchen.mp4"),
                "-filter_complex",
                "[0:v]scale=1080:1920,setsar=1,fps=30[v0];"
                "[1:v]scale=1080:1920,setsar=1,fps=30[v1];"
                "[0:a]aformat=sample_rates=44100:channel_layouts=stereo[a0];"
                "[1:a]aformat=sample_rates=44100:channel_layouts=stereo[a1];"
                "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]",
                "-map", "[v]", "-map", "[a]", "-c:v", "libx264",
                "-preset", "medium", "-crf", "21", "-maxrate", "8000k",
                "-bufsize", "16000k", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                str(avec)])
    if r.returncode != 0:
        print("      REFUS : collage de l'outro impossible")
        return False
    # Un rendu se verifie par DECODAGE COMPLET, jamais par la duree annoncee.
    if ffmpeg(["-i", str(avec), "-f", "null", "-"]).stderr.strip():
        print("      REFUS : fichier corrompu au decodage")
        return False
    print("      outro + integrite : OK")

    b = python([str(RACINE / "pipeline" / "mettre_en_file.py"),
                "--chaine", "lovekitchen", "--id", ident,
                "--legende", legende,
                "--source", str(LK / conf["source"]), str(avec)])
    for l in ((b.stdout or "") + (b.stderr or "")).splitlines():
        l = l.strip()
        if l.startswith("[NON") or l.startswith("REJET") or l.startswith("OK :"):
            print("      " + l[:130])
    return b.returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=3,
                    help="videos par execution (quota Gemini : ~10 tirages/jour)")
    ap.add_argument("--voix-secours", action="store_true",
                    help="accepter une voix de secours (edge-tts) quand le quota "
                         "Gemini est epuise. JAMAIS par defaut : la chaine a une "
                         "identite sonore, et une autre voix s'entend. A n'utiliser "
                         "que pour montrer un rendu, pas pour publier.")
    a = ap.parse_args()

    conf = json.loads((LK / "sources.json").read_text(encoding="utf-8"))["videos"]
    file = json.loads(FILE.read_text(encoding="utf-8"))
    par_id = {e.get("id"): e for e in file}

    candidats = [i for i in conf
                 if par_id.get(i, {}).get("status") in ("on_hold", "pending")]
    if not candidats:
        print("rien a remonter.")
        return 0
    print("%d video(s) a remonter, %d au maximum ce tour."
          % (len(candidats), a.max))

    (LK / "exemples").mkdir(exist_ok=True)
    faits = 0
    for ident in candidats:
        if faits >= a.max:
            break
        print("===== %s" % ident)
        legende = par_id[ident].get("caption") or ""
        if not legende:
            print("      passe : aucune legende en file")
            continue
        sortie = LK / "exemples" / ("v_%s.mp4" % ident.replace("-", "_"))
        ok, journal = monter(conf[ident], sortie, a.voix_secours)
        bas = journal.lower()
        # Trois arrets francs, appris en testant ce pilote le 2026-09-12 : il a
        # enchaine les SEPT videos en croyant a sept echecs de rendu, alors que
        # la cle Gemini manquait. Un probleme d'environnement ne se retente pas
        # video par video - et chaque tentative fait avancer la rotation des
        # intros pour rien.
        manque = re.search(r"([A-Z_]+_API_KEY) introuvable", journal)
        if manque:
            # Nommer LAQUELLE. La premiere version annoncait "cle Gemini
            # absente" pour un GROQ_API_KEY manquant, et j'ai cherche au
            # mauvais endroit pendant dix minutes.
            print("      %s absente de l'environnement : rien a tenter."
                  % manque.group(1))
            return 1
        if "indisponible" in bas and "gemini" in bas and not a.voix_secours:
            print("      quota Gemini epuise : on s'arrete la, sans sonder.")
            break
        if "essai" not in bas:
            print("      aucun tirage de voix n'a meme ete tente : on s'arrete.")
            break
        if not ok:
            continue
        if finaliser(ident, sortie, legende, conf[ident]):
            faits += 1
    print("\n%d video(s) remise(s) en file." % faits)
    return 0


if __name__ == "__main__":
    sys.exit(main())
