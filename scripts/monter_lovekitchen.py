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
#
# RETIRE LE 2026-09-19. Cette porte + debruiteur hachait la voix : silence
# numerique a chaque pause, coupures au milieu des phrases - ce que
# l'utilisateur a entendu comme "des bruits parasites tout au long" et "une
# voix qui sonne IA". Elle soignait un symptome : le souffle que speechnorm
# faisait remonter. La cause est corrigee dans CHAINE_VOIX du montage, cette
# etape n'a plus rien a faire. Laissee neutre plutot que supprimee, pour que la
# raison reste lisible ici.
SOUFFLE = "anull"
# id -> modele TTS qui a produit la voix, rempli par le controle de voix
MODELE_RETENU = {}

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
        # La voix attendue est celle que declare voix_reference.json. Depuis le
        # 2026-09-22 ce peut etre le CLONE Voicebox de Sulafat (demande de
        # l'utilisateur) : lui seul, ou Gemini, selon ce qui est declare. Tout
        # le reste - edge-tts surtout, une autre voix - reste refuse.
        try:
            _ref = json.loads((LK / "voix_reference.json").read_text(encoding="utf-8"))
            _moteur = (_ref.get("modele_tts") or {}).get("moteur") or "gemini"
        except (OSError, ValueError):
            _moteur = "gemini"
        _attendu = "Voicebox Sulafat" if _moteur == "voicebox" else "Gemini"
        if _attendu not in retenue[-1]:
            print("      REJET : voix retenue %s - la chaine est sur %s"
                  % (retenue[-1].split(":")[-1].strip()[:40], _attendu))
            sortie.unlink(missing_ok=True)
            return False, j

    # QUEL MODELE a produit cette voix. La cascade en a trois (pro, flash-3.1,
    # flash-2.5) et bascule silencieusement quand un quota tombe. Le nom de
    # voix reste "Sulafat" mais le rendu change : le 2026-09-20 l'utilisateur
    # entend deux voix differentes entre deux videos, et la mesure lui donne
    # raison - centre spectral 2472 Hz contre 1793, distance de timbre 53 la
    # ou deux tirages du meme modele sont a 20. Le modele n'etait ecrit nulle
    # part : impossible de savoir apres coup lequel avait fait quoi. Il est
    # desormais trace, pour qu'on puisse au moins constater.
    if retenue:
        m = re.search(r"\(([^)]*tts[^)]*)\)", retenue[-1])
        if m:
            MODELE_RETENU[pathlib.Path(sortie).stem] = m.group(1)
            print("      moteur de voix : %s" % m.group(1))
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
        # DIRE POURQUOI. Pendant six jours ce message est tombe chaque matin
        # sur des rendus parfaitement bons, sans jamais nommer la cause :
        # `outros/outro_lovekitchen.mp4` n'avait jamais ete ajoutee au depot.
        # Un refus qui ne dit pas son motif coute une semaine.
        lignes = (r.stderr or "").strip().splitlines()
        print("      REFUS : collage de l'outro impossible%s"
              % (" -> " + lignes[-1][:120] if lignes else ""))
        return False
    # Un rendu se verifie par DECODAGE COMPLET, jamais par la duree annoncee.
    if ffmpeg(["-i", str(avec), "-f", "null", "-"]).stderr.strip():
        print("      REFUS : fichier corrompu au decodage")
        return False
    print("      outro + integrite : OK")

    # Une video REVOICEE a deja une entree en file, avec l'empreinte de son
    # ANCIEN fichier : la barriere refuse un identifiant deja present, et une
    # entree gardee perdrait contre la nouvelle a la fusion (2026-09-11). On
    # la retire juste avant de repasser la barriere, qui la recree avec le bon
    # tampon.
    import json as _j
    _f = _j.loads(FILE.read_text(encoding="utf-8"))
    _g = [e for e in _f if not (e.get("id") == ident
                                and e.get("status") in ("on_hold", "pending"))]
    if len(_g) != len(_f):
        FILE.write_text(_j.dumps(_g, ensure_ascii=False, indent=2), encoding="utf-8")

    b = python([str(RACINE / "pipeline" / "mettre_en_file.py"),
                "--chaine", "lovekitchen", "--id", ident,
                "--legende", legende,
                "--source", str(LK / conf["source"]), str(avec)])
    for l in ((b.stdout or "") + (b.stderr or "")).splitlines():
        l = l.strip()
        # La ligne "->" porte le MOTIF du refus. Sans elle on ne lisait que
        # "[NON] audio" - et analyse_audio manquant au depot est reste
        # invisible (2026-09-19).
        if l.startswith(("[NON", "->", "REJET", "OK :")):
            print("      " + l[:160])
    if b.returncode != 0:
        return False

    # RIEN DE CE QUE CE PILOTE PRODUIT NE PART EN LIGNE. Consigne explicite de
    # l'utilisateur, 2026-09-12 : *le projet de montage automatique est en
    # beta, tu ne sors rien*. Seules les videos terminees et validees a la main
    # sortent. L'entree est donc mise en attente de validation des qu'elle a
    # passe la barriere : `programmer_avance` ne depose que du `pending`, un
    # `on_hold` reste sur place.
    import json as _json
    file = _json.loads(FILE.read_text(encoding="utf-8"))
    for e in file:
        if e.get("id") == ident:
            e["status"] = "on_hold"
            e["error"] = ("montage automatique (beta) : a valider a la main avant "
                          "toute sortie, consigne du 2026-09-12")
    FILE.write_text(_json.dumps(file, ensure_ascii=False, indent=2), encoding="utf-8")
    print("      mise en attente de validation : elle ne sortira pas seule")
    return True


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

    # Candidates : les videos a REFAIRE (en file, en attente) et les videos
    # NEUVES que sources.json connait mais que la file n'a jamais vues - sans
    # ce second cas, un plat neuf ne pouvait jamais entrer (2026-09-19).
    candidats = [i for i in conf
                 if i not in par_id
                 or par_id[i].get("status") in ("on_hold", "pending")]
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
        legende = ((par_id.get(ident) or {}).get("caption")
                   or conf[ident].get("legende") or "")
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
        # Le quota n'arrete le lot que si le RENDU a echoue. Un tirage peut
        # tomber sur un 429 alors que le montage a deja retenu un bon tirage
        # Gemini : le 2026-09-19, 85-patesboeuf etait rendue et conforme, et le
        # pilote s'est arrete AVANT de la finaliser. On finalise d'abord, on
        # s'arrete ensuite.
        quota_epuise = "indisponible" in bas and "gemini" in bas
        if quota_epuise and not ok and not a.voix_secours:
            print("      quota Gemini epuise : on s'arrete la, sans sonder.")
            break
        if "essai" not in bas:
            print("      aucun tirage de voix n'a meme ete tente : on s'arrete.")
            break
        if not ok:
            continue
        if finaliser(ident, sortie, legende, conf[ident]):
            faits += 1
        if quota_epuise and not a.voix_secours:
            print("      quota Gemini epuise apres ce rendu : on s'arrete la.")
            break
    print("\n%d video(s) remise(s) en file." % faits)
    return 0


if __name__ == "__main__":
    sys.exit(main())
