#!/usr/bin/env python3
"""L'identite sonore de la chaine, mesuree et opposable.

POURQUOI
--------
Le 2026-09-20 l'utilisateur entend DEUX VOIX DIFFERENTES entre deux videos
sorties a un jour d'ecart. La mesure lui donne raison : centre spectral
2472 Hz contre 1793, hauteur 179 contre 169 Hz, distance de timbre 53 la ou
deux tirages du meme modele sont a 20.

La cause n'etait pas le montage mais la GENERATION : la cascade TTS a trois
modeles (pro, flash-3.1, flash-2.5) et bascule silencieusement des qu'un
quota tombe. Le nom de voix reste "Sulafat", le rendu non. Et rien n'etait
ecrit : impossible de savoir apres coup lequel avait produit quoi.

Le public de la chaine connait cette voix. Elle ne doit pas changer d'un soir
a l'autre. Ce module en donne une definition CHIFFREE - pas "la meme voix" en
paroles, mais des nombres qu'on peut opposer a un fichier.

CE QU'ON MESURE, ET POURQUOI CELA
---------------------------------
  hauteur          mediane de f0. Change quand le modele change.
  melodie          ecart-type de f0 en demi-tons : combien la voix module.
  centre spectral  ou se situe le centre de gravite du son - la "clarte".
  couleur          spectre moyen a long terme en 8 bandes, ramene a la moyenne
                   200-4000 Hz. C'est la signature de timbre proprement dite :
                   elle ne bouge pas avec le volume.
  niveau           loudness integree et crete vraie : le calibrage de sortie.
  plancher         bruit de fond entre les mots.

Tout est mesure sur la PAROLE SEULE, outro exclue : l'outro est muette par
construction et tire chaque moyenne vers le bas.

USAGE
-----
    python empreinte_voix.py mesurer <video.mp4>
    python empreinte_voix.py comparer <video.mp4> [voix_reference.json]
"""
import json
import pathlib
import subprocess
import sys

ICI = pathlib.Path(__file__).resolve().parent
REFERENCE = ICI / "voix_reference.json"
SR = 44100
DUREE_OUTRO = 2.6

# 1/3 d'octave ne sert a rien ici : on veut une signature stable, pas un
# analyseur. Huit bandes suffisent a separer deux modeles TTS et restent
# lisibles dans un journal de montage.
BANDES = [(80, 160), (160, 320), (320, 640), (640, 1280),
          (1280, 2560), (2560, 5120), (5120, 8000), (8000, 14000)]
# Bandes de reference pour normaliser la couleur : le coeur de la voix.
NORMALISATION = (200, 4000)

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")


def _duree(f):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(f)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _charger(f, sans_outro=True):
    import numpy as np, librosa  # noqa: E401
    import tempfile
    d = _duree(f)
    w = pathlib.Path(tempfile.mkdtemp()) / "v.wav"
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(f)]
    if sans_outro and d > DUREE_OUTRO + 5:
        cmd += ["-t", "%.2f" % (d - DUREE_OUTRO)]
    subprocess.run(cmd + ["-ac", "1", "-ar", str(SR), str(w)], check=True)
    y, _ = librosa.load(str(w), sr=SR)
    return y, np


def mesurer(f, sans_outro=True):
    """Empreinte d'un fichier. Tout en flottants, tout comparable.

    `sans_outro=False` pour une piste de voix seule (essai de modele) : elle
    n'a pas d'outro, et en couper 2,6 s amputerait la derniere phrase.
    """
    import librosa
    y, np = _charger(f, sans_outro)

    Sx = np.abs(librosa.stft(y, n_fft=4096, hop_length=1024))
    fr = librosa.fft_frequencies(sr=SR, n_fft=4096)
    niv = 20*np.log10(np.maximum(Sx.mean(axis=0), 1e-10))
    # Les trames de PAROLE. Le silence porte le bruit de fond, pas la voix :
    # l'y melanger fait passer un souffle pour un timbre.
    parle = niv > np.percentile(niv, 95) - 18
    if parle.sum() < 20:
        parle = np.ones_like(parle, dtype=bool)
    moy = Sx[:, parle].mean(axis=1)
    spec = 20*np.log10(np.maximum(moy, 1e-10))

    m = (fr >= NORMALISATION[0]) & (fr < NORMALISATION[1])
    zero = float(spec[m].mean())
    couleur = []
    for a, b in BANDES:
        mb = (fr >= a) & (fr < b)
        couleur.append(round(float(spec[mb].mean()) - zero, 2))

    f0 = librosa.yin(y, fmin=70, fmax=400, sr=SR, frame_length=2048)
    f0 = f0[(f0 > 90) & (f0 < 340)]
    hauteur = float(np.median(f0)) if len(f0) > 50 else 0.0
    melodie = float(np.std(12*np.log2(f0/np.median(f0)))) if len(f0) > 50 else 0.0

    cen = librosa.feature.spectral_centroid(y=y, sr=SR)[0]
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
    rdb = 20*np.log10(np.maximum(rms, 1e-10))

    return {
        "hauteur_Hz": round(hauteur, 1),
        "melodie_demitons": round(melodie, 2),
        "centre_spectral_Hz": round(float(np.median(cen)), 0),
        "couleur_dB": couleur,
        "niveau_dB": round(float(np.percentile(rdb, 50)), 1),
        "plancher_dB": round(float(np.percentile(rdb, 5)), 1),
    }


# ------------------------------------------------------------ debut propre
# "Il y a un bruit de fond bizarre dans les 2 premieres secondes" (utilisateur,
# 2026-09-20). Trois defauts distincts, tous mesures ce jour-la :
#   - ATTAQUE : la voix partait a pleine puissance des la 1re milliseconde
#     (-14 a -23 dB sur 100 ms ; la reference ouvre a -49 dB) ;
#   - SIFFLEMENT : la bande 6-11 kHz montait de -76 a -4 dB en 300 ms, jusqu'a
#     +13 dB, la ou la reference reste vers -20 ;
#   - GRONDEMENT : 0-120 Hz a +3,5 dB au-dessus de la reference sur une video.
# DEUX PIEGES de mesure payes ce jour-la, et que ces reglages evitent :
#   - a 22 kHz la bande 6-11 kHz colle a Nyquist et le filtre anti-repliement
#     la deforme : on mesure a 44,1 kHz ;
#   - sur la piste ENTIERE l'ecart se dilue sous 1 dB : on mesure la ou
#     l'utilisateur ecoute, les 2 premieres secondes.
DEBUT_S = 2.0
DEBUT_TOL = {"attaque_dB": -30.0,      # plafond absolu, 100 premieres ms
             "sifflement_dB": 3.0,     # ecart max a la reference, 6-11 kHz
             "grondement_dB": 3.5}     # ecart max a la reference, 0-120 Hz


def mesurer_debut(f):
    """Attaque, sifflement et grondement des 2 premieres secondes."""
    import numpy as np, librosa  # noqa: E401
    import tempfile
    w = pathlib.Path(tempfile.mkdtemp()) / "d.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(f), "-t", str(DEBUT_S),
                    "-vn", "-ac", "1", "-ar", str(SR), str(w)], check=True)
    y, _ = librosa.load(str(w), sr=SR)
    tete = y[:int(0.10*SR)]
    attaque = 20*np.log10(max(float(np.sqrt(np.mean(tete**2))), 1e-10))
    Sx = np.abs(librosa.stft(y, n_fft=2048, hop_length=512))
    fr = librosa.fft_frequencies(sr=SR, n_fft=2048)
    def bande(a, b):
        m = (fr >= a) & (fr < b)
        return 20*np.log10(max(float(Sx[m].mean()), 1e-10))
    return {"attaque_dB": round(attaque, 1),
            "sifflement_dB": round(bande(6000, 11000), 1),
            "grondement_dB": round(bande(0, 120), 1)}


def controler_debut(f, ref=None):
    """(propre, ecarts). Compare les 2 premieres secondes a la reference."""
    r = ref or _reference()
    cible = r.get("debut")
    m = mesurer_debut(f)
    ecarts = []
    if m["attaque_dB"] > DEBUT_TOL["attaque_dB"]:
        ecarts.append("attaque a %.1f dB sur les 100 premieres ms (plafond %.0f)"
                      % (m["attaque_dB"], DEBUT_TOL["attaque_dB"]))
    if cible:
        for cle, nom in (("sifflement_dB", "sifflement 6-11 kHz"),
                         ("grondement_dB", "grondement 0-120 Hz")):
            d = m[cle] - cible[cle]
            if d > DEBUT_TOL[cle]:
                ecarts.append("%s %+.1f dB au-dessus de la reference (tolerance %.1f)"
                              % (nom, d, DEBUT_TOL[cle]))
    return (not ecarts), ecarts, m


def _reference(chemin=None):
    return json.loads(pathlib.Path(chemin or REFERENCE).read_text(encoding="utf-8"))


def comparer(empreinte, ref=None):
    """Retourne (conforme, liste d'ecarts lisibles)."""
    r = ref or _reference()
    cible, tol = r["empreinte"], r["tolerances"]
    ecarts = []

    for cle, libelle, unite in (("hauteur_Hz", "hauteur", " Hz"),
                                ("melodie_demitons", "melodie", " demi-tons"),
                                ("centre_spectral_Hz", "clarte", " Hz")):
        d = empreinte[cle] - cible[cle]
        if abs(d) > tol[cle]:
            ecarts.append("%s %+.1f%s (tolerance %s%s)"
                          % (libelle, d, unite, tol[cle], unite))

    pires = []
    for i, (a, b) in enumerate(BANDES):
        d = empreinte["couleur_dB"][i] - cible["couleur_dB"][i]
        if abs(d) > tol["couleur_dB"]:
            pires.append("%d-%d Hz %+.1f dB" % (a, b, d))
    if pires:
        ecarts.append("couleur : " + ", ".join(pires) + " (tolerance %s dB)"
                      % tol["couleur_dB"])

    return (not ecarts), ecarts


def distance(empreinte, ref=None):
    """Un seul nombre : l'ecart total, EN TOLERANCES.

    Chaque mesure est divisee par sa tolerance : 1,0 = pile a la limite. La
    distance est la pire de toutes (un seul critere hors tolerance suffit a
    changer la voix), et la moyenne sert a departager deux conformes.
    """
    r = ref or _reference()
    cible, tol = r["empreinte"], r["tolerances"]
    parts = [abs(empreinte[k] - cible[k]) / tol[k]
             for k in ("hauteur_Hz", "melodie_demitons", "centre_spectral_Hz")]
    parts += [abs(a - b) / tol["couleur_dB"]
              for a, b in zip(empreinte["couleur_dB"], cible["couleur_dB"])]
    return max(parts), sum(parts) / len(parts)


def _resume(e):
    return ("hauteur %5.1f Hz | melodie %4.2f | clarte %5.0f Hz | couleur %s"
            % (e["hauteur_Hz"], e["melodie_demitons"], e["centre_spectral_Hz"],
               " ".join("%+5.1f" % v for v in e["couleur_dB"])))


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    action, cible = sys.argv[1], pathlib.Path(sys.argv[2])
    e = mesurer(cible)
    if action == "mesurer":
        print(json.dumps(e, indent=1))
        print("\n" + _resume(e))
        return 0
    if action == "comparer":
        ref = _reference(sys.argv[3] if len(sys.argv) > 3 else None)
        ok, ecarts = comparer(e, ref)
        print("mesure    " + _resume(e))
        print("reference " + _resume(ref["empreinte"]))
        if ok:
            print("\n>> CONFORME a la voix de la chaine")
            return 0
        print("\n>> HORS TOLERANCE :")
        for x in ecarts:
            print("   " + x)
        return 1
    print("action inconnue : " + action)
    return 2


if __name__ == "__main__":
    sys.exit(main())
