#!/usr/bin/env python3
"""Quel modele TTS donne LA voix de la chaine ? On le mesure, puis on l'epingle.

POURQUOI
--------
La cascade de montage_lovekitchen.py a trois modeles Gemini. Ils portent tous
la voix "Sulafat", mais ne la rendent pas pareil : le 2026-09-20 la mesure du
corpus a montre une hauteur de 143 a 191 Hz et une clarte de 2166 a 3351 Hz
sur 17 videos publiees, soit une voix differente d'un soir a l'autre. Le
modele qui avait produit chaque video n'etait trace nulle part.

Ce script ne devine pas : il fait dire LE SCRIPT DE LA VIDEO DE REFERENCE
(56-onepotpates, 348 767 vues), avec LA CONSIGNE ACTUELLE du montage, par
chaque modele. Meme texte, meme consigne : la seule variable est le modele
(une variable a la fois - regle du projet). Chaque tirage passe par la chaine
de voix du montage, puis on mesure son empreinte et sa distance a la
reference.

A REJOUER quand Google change ses modeles : les "preview" disparaissent, et
un modele renomme peut sonner autrement.

Usage :
    python choisir_modele_voix.py              # mesure et classe
    python choisir_modele_voix.py --epingler   # et ecrit le gagnant
Cout : UNE requete TTS par modele (quota : 10 par jour et par modele).
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ICI = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ICI))

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epingler", action="store_true",
                    help="ecrire le meilleur modele CONFORME dans voix_reference.json")
    ap.add_argument("--script", default=str(ICI / "scripts" / "56_onepotpates.txt"))
    ap.add_argument("--garder", default="", help="dossier ou garder les tirages")
    a = ap.parse_args()

    import montage_lovekitchen as M
    import empreinte_voix as EV

    ref = EV._reference()
    texte = pathlib.Path(a.script).read_text(encoding="utf-8")
    garde = pathlib.Path(a.garder) if a.garder else None
    if garde:
        garde.mkdir(parents=True, exist_ok=True)

    print("reference : %s (%s vues)" % (ref["reference"]["video"],
                                       "{:,}".format(ref["reference"]["vues"]).replace(",", " ")))
    print("           " + EV._resume(ref["empreinte"]))
    print("texte     : %s (%d mots)\n" % (pathlib.Path(a.script).name, len(texte.split())))

    resultats = []
    for modele in M.MODELES_TTS:
        print("=== %s" % modele, flush=True)
        # UN modele, pas de cascade : c'est tout l'objet de l'essai.
        M.voix_epinglee = lambda m=modele: {"epingle": m, "cascade_autorisee": False}
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            brut = td / "brut.wav"
            try:
                moteur = M.gemini_tts(texte, brut, td)
            except SystemExit as e:
                print("   indisponible (%s)\n" % str(e)[:80]); continue
            except Exception as e:
                print("   indisponible : %s\n" % str(e)[:90]); continue
            if not brut.exists() or modele not in str(moteur):
                print("   aucun tirage de ce modele (%s)\n" % moteur); continue

            # la chaine de voix du montage, pour mesurer ce qui sortirait
            voix = td / "voix.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(brut),
                            "-filter:a", M.CHAINE_VOIX, "-ac", "1", "-ar", "44100",
                            str(voix)], check=True)

            # La consigne lue a voix haute fausse tout le reste : on la cherche.
            fuite = None
            cle_groq = os.environ.get("GROQ_API_KEY")
            if cle_groq:
                try:
                    mots = M.whisper_mots(voix, cle_groq)
                    dit = M._cles_mots(" ".join(m.get("word", "") for m in mots))
                    vus = set(zip(dit, dit[1:], dit[2:], dit[3:], dit[4:]))
                    cons = M._cles_mots(M.consigne_style(texte)) if hasattr(M, "consigne_style") else []
                    fuite = next((g for g in zip(cons, cons[1:], cons[2:], cons[3:], cons[4:])
                                  if g in vus), None)
                except Exception as e:
                    print("   (verification de consigne impossible : %s)" % str(e)[:60])
            if fuite:
                print("   ECARTE : le modele a lu la consigne (\"%s\")\n" % " ".join(fuite))
                continue

            e = EV.mesurer(voix, sans_outro=False)
            pire, moyenne = EV.distance(e, ref)
            ok, ecarts = EV.comparer(e, ref)
            print("   " + EV._resume(e))
            print("   distance : pire critere %.2f, moyenne %.2f (1,00 = limite de tolerance)"
                  % (pire, moyenne))
            print("   >> %s\n" % ("CONFORME" if ok else "hors tolerance : " + " ; ".join(ecarts)))
            if garde:
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(voix),
                                "-c:a", "libmp3lame", "-q:a", "3",
                                str(garde / ("essai_%s.mp3" % modele))], check=True)
            resultats.append((ok, pire, moyenne, modele, e))

    if not resultats:
        print("Aucun modele n'a pu etre essaye (quota ?). Rien d'epingle.")
        return 1

    resultats.sort(key=lambda r: (not r[0], r[1], r[2]))
    print("CLASSEMENT")
    for ok, pire, moy, modele, _ in resultats:
        print("  %-32s pire %.2f  moyenne %.2f  %s"
              % (modele, pire, moy, "CONFORME" if ok else "hors tolerance"))

    gagnant = resultats[0]
    if not gagnant[0]:
        print("\nAUCUN modele ne tombe dans la tolerance avec la consigne actuelle.")
        print("Rien n'est epingle : epingler le moins mauvais figerait une voix qui")
        print("n'est pas celle de la chaine. Piste suivante : la CONSIGNE, pas le modele.")
        return 1

    print("\nLe plus proche de la reference : %s" % gagnant[3])
    if a.epingler:
        chemin = EV.REFERENCE
        doc = json.loads(chemin.read_text(encoding="utf-8"))
        doc["modele_tts"]["epingle"] = gagnant[3]
        doc["modele_tts"]["cascade_autorisee"] = False
        doc["modele_tts"]["epingle_le"] = __import__("time").strftime("%Y-%m-%d")
        doc["modele_tts"]["mesure_a_l_epinglage"] = {
            "empreinte": gagnant[4], "distance_pire": round(gagnant[1], 2),
            "distance_moyenne": round(gagnant[2], 2)}
        chemin.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        print("EPINGLE dans %s" % chemin.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
