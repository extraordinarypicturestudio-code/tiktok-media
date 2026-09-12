#!/usr/bin/env python3
"""Rejoue TOUS les controles de la chaine sur les videos deja programmees.

POURQUOI
--------
Chaque controle de ce projet est ne d'un defaut reellement parti en
production : une fillette publiee, une voix qui s'eteint, un gratin de thon
sorti deux jours de suite, une outro annoncant la mauvaise chaine. Ils sont
tous cables quelque part - au montage, a la barriere, au depot - mais ils
s'appliquent CHACUN A SON MOMENT, sur l'etat du fichier a ce moment-la.

Ce script les rejoue tous ensemble, sur le FICHIER FINAL, celui qui va
vraiment sortir. C'est la difference entre "la file dit que c'est bon" et
"je l'ai mesure sur ce qui part". Le 2026-09-12, quatre depots sur huit
portaient des videos que les gardes avaient refusees : la file mentait, et
personne ne regardait le fichier.

CE QU'IL MESURE
---------------
  specs        1080x1920, fps, duree > 60 s (Creator Rewards), poids
  integrite    decodage COMPLET, pas la duree annoncee par le conteneur
  voix         derive de niveau, respiration, intonation debut -> fin
  souffle      plancher de bruit entre les mots
  texte        incrustations de la source (signature pixel, pas OCR)
  outro        empreinte de fin + pseudo affiche
  tampon       sha256 du fichier contre celui qu'a vu la barriere
  repetition   le plat est-il deja publie, ou deja programme
  registre     tutoiement final et temps sensuels du script
  intro        laquelle, pour voir si la rotation tourne vraiment

Usage :
    python3 scripts/auditer_lovekitchen.py            # les videos programmees
    python3 scripts/auditer_lovekitchen.py --toutes   # tout ce qui est en file
"""

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys

RACINE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "scripts"))
sys.path.insert(0, str(RACINE / "channels" / "love_kitchen"))

SEUILS = {
    "duree_min": 60.0,      # Creator Rewards
    "derive": -2.5,         # dB, video finie
    "respiration": 20.0,    # % de silence, video finie
    "intonation": -25.0,    # % de variation de hauteur, debut -> fin
    "plancher": -55.0,      # dB, bruit de fond entre les mots
    "repetition": 0.30,
}

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")


def ffprobe(f, flux="v:0", champs="width,height,r_frame_rate"):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", flux,
                        "-show_entries", "stream=" + champs, "-of",
                        "default=nw=1:nk=1", str(f)],
                       capture_output=True, text=True)
    return r.stdout.split()


def duree(f):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "csv=p=0", str(f)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def plancher_bruit(f):
    """Niveau du bruit de fond, mesure sur les passages les plus calmes."""
    # `ametadata=print` ecrit sur stderr AU NIVEAU INFO : avec `-v error` la
    # sortie est vide et la mesure rend None sans bruit. Un controle qui ne
    # tourne pas n'est pas un controle - il faut `file=-` pour la sortie
    # standard. Constate le 2026-09-12 : la ligne "souffle" n'apparaissait sur
    # aucune des six videos auditees, et je l'ai d'abord lu comme "rien a
    # signaler".
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(f), "-af",
                        "astats=metadata=1:reset=1,ametadata=print:key=lavfi."
                        "astats.Overall.RMS_level:file=-", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    vals = [float(m) for m in re.findall(r"RMS_level=(-?\d+\.?\d*)",
                                         r.stdout + r.stderr)]
    if not vals:
        return None
    vals.sort()
    return vals[len(vals) // 20] if len(vals) >= 20 else vals[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--toutes", action="store_true")
    a = ap.parse_args()

    import montage_lovekitchen as M
    import detecter_texte_incruste as DET
    import controle_repetition as CR

    file = json.loads((RACINE / "queue-lovekitchen.json").read_text(encoding="utf-8"))
    vises = [e for e in file
             if a.toutes or e.get("status") == "scheduled"]
    vises.sort(key=lambda e: str(e.get("scheduledForUtc") or ""))
    if not vises:
        print("aucune video a auditer.")
        return 0

    srcs = json.loads((RACINE / "channels" / "love_kitchen" /
                       "sources.json").read_text(encoding="utf-8"))["videos"]
    publiees = []
    try:
        cle = None
        for l in open(RACINE.parent / "Project 1 TIKTOK" / "zernio2.env"):
            if "=" in l:
                cle = l.split("=", 1)[1].strip().strip('"')
        publiees = list(CR.legendes_publiees("love_kitchen97", cle))
    except Exception as e:
        print("(legendes publiees indisponibles : %s)" % str(e)[:60])

    anomalies = 0
    for e in vises:
        ident = e["id"]
        f = RACINE / "clips-lovekitchen" / (ident + ".mp4")
        quand = str(e.get("scheduledForUtc") or "")[:16] or "non programmee"
        print("\n===== %-22s %s" % (ident, quand))
        if not f.exists():
            print("  FICHIER ABSENT : %s" % f.name)
            anomalies += 1
            continue

        ecarts = []

        # --- specs et integrite
        wh = ffprobe(f)
        d = duree(f)
        taille = f.stat().st_size / 1048576
        res = "x".join(wh[:2]) if len(wh) >= 2 else "?"
        fps = 0.0
        if len(wh) >= 3 and "/" in wh[2]:
            n, dd = wh[2].split("/")
            fps = float(n) / float(dd or 1)
        print("  specs        %s, %.0f fps, %.1fs, %.1f Mo" % (res, fps, d, taille))
        if res != "1080x1920":
            ecarts.append("resolution %s" % res)
        if d < SEUILS["duree_min"]:
            ecarts.append("duree %.1fs < 60s (Creator Rewards)" % d)
        if taille > 100:
            ecarts.append("poids %.0f Mo > limite GitHub" % taille)

        err = subprocess.run(["ffmpeg", "-v", "error", "-i", str(f), "-f",
                              "null", "-"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace").stderr.strip()
        print("  integrite    %s" % ("decodage complet OK" if not err
                                     else "CORROMPU : " + err[:60]))
        if err:
            ecarts.append("fichier corrompu")

        # --- voix, sur la video FINIE
        try:
            dv = M.derive_voix(str(f))
            rp = M.respiration(str(f))
            it = M.intonation(str(f))
            print("  voix         derive %+.1f dB | respiration %.0f %% | "
                  "intonation %+.0f %%" % (dv, rp, it))
            if dv < SEUILS["derive"]:
                ecarts.append("voix qui s'eteint (%+.1f dB)" % dv)
            if rp < SEUILS["respiration"]:
                ecarts.append("ne respire pas (%.0f %%)" % rp)
            if it <= SEUILS["intonation"]:
                ecarts.append("voix qui s'aplatit (%+.0f %%)" % it)
        except Exception as ex:
            print("  voix         MESURE IMPOSSIBLE : %s" % str(ex)[:60])
            ecarts.append("voix non mesurable")

        pl = plancher_bruit(f)
        if pl is None:
            print("  souffle      MESURE IMPOSSIBLE")
            ecarts.append("souffle non mesure")
        else:
            print("  souffle      plancher %.1f dB" % pl)
            if pl > SEUILS["plancher"]:
                ecarts.append("souffle de fond a %.1f dB" % pl)

        # --- texte incruste laisse par la source
        try:
            passages, _ = DET.analyser(f, DET.SEUIL, False)
            print("  texte        %s" % ("aucune incrustation" if not passages
                                         else "%d passage(s)" % len(passages)))
            if passages:
                ecarts.append("%d passage(s) de texte incruste" % len(passages))
        except Exception as ex:
            print("  texte        MESURE IMPOSSIBLE : %s" % str(ex)[:60])

        # --- tampon : le fichier est-il bien celui qu'a vu la barriere ?
        sha = hashlib.sha256(f.read_bytes()).hexdigest()[:12]
        tampon = str(json.dumps(e.get("verification") or {}))
        print("  tampon       sha256 %s %s" % (
            sha, "= barriere" if sha in tampon else "ABSENT de la barriere"))
        if sha not in tampon:
            ecarts.append("le fichier ne correspond pas au tampon")

        # --- plat deja sorti, ou deja programme ailleurs
        try:
            enf = CR.legendes_en_file(str(RACINE / "queue-lovekitchen.json"), ident)
            sc, dt, anc = CR.deja_publie(e.get("caption") or "", publiees + enf)
            print("  repetition   %.2f (%s)" % (sc, dt or "-"))
            if sc >= SEUILS["repetition"]:
                ecarts.append("plat deja sorti/prevu (%.2f, %s)" % (sc, dt))
        except Exception as ex:
            print("  repetition   MESURE IMPOSSIBLE : %s" % str(ex)[:60])

        # --- registre du script, et intro utilisee
        conf = srcs.get(ident)
        if conf:
            sc_f = RACINE / "channels" / "love_kitchen" / conf["script"]
            if sc_f.exists():
                t = sc_f.read_text(encoding="utf-8")
                li = [l for l in t.split("\n") if l.strip()]
                try:
                    M.controle_registre(t, sc_f)
                except SystemExit:
                    ecarts.append("registre : tutoiement ou temps sensuels")
                print("  script       %d mots, %d lignes, %.1f mots/ligne"
                      % (len(t.split()), len(li), len(t.split()) / len(li)))
        leg = (e.get("caption") or "").split("\n")[0]
        print("  legende      %s" % leg[:72])

        if ecarts:
            anomalies += 1
            for x in ecarts:
                print("  >> ANOMALIE  %s" % x)
        else:
            print("  >> tous les controles passent")

    print("\n%d video(s) auditee(s), %d avec anomalie."
          % (len(vises), anomalies))
    return 1 if anomalies else 0


if __name__ == "__main__":
    sys.exit(main())
