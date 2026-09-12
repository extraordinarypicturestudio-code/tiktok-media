#!/usr/bin/env python3
"""Montage generatif love_kitchen97 : intro (5s, la femme presente le plat) +
corps POV cuisine, sur un script dit par une voix de synthese feminine
(Sulafat via Gemini TTS, edge-tts en repli).

Adapte de channels/beauty/montage-solo.js (meme principe : TTS -> transcription
Groq Whisper au mot -> sous-titres ASS synchronises -> encodage) mais assemble
DEUX sources video (intro fixe + corps etire/accelere) au lieu d'une seule
source bouclee.

Usage :
    python montage_lovekitchen.py --intro-ref cuisine_robe_bleue \
        --corps CORPS.mp4 --script script.txt --sortie sortie.mp4 [--titre "..."]

L'intro ne se donne PAS par chemin libre : elle se choisit parmi les fenetres
validees a l'oeil dans intros_valides.json (voir charger_intro).
"""

import argparse
import json
import mimetypes
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ICI = pathlib.Path(__file__).resolve().parent
RACINE = ICI.parent.parent
UA = "Mozilla/5.0"  # sans cet en-tete, Cloudflare renvoie 403 devant Groq
GEM_VOICE = "Sulafat"
EDGE_VOICE = "fr-FR-VivienneMultilingualNeural"
DUREE_INTRO = 7.0
# Le debit de Gemini TTS varie d'un rendu a l'autre pour un MEME texte (mesure
# le 2026-08-21 : 177 mots -> 66.1s puis 54.2s, +/-20%). Compter les mots ne
# suffit donc pas a viser une duree precise : on mesure la voix generee puis
# on la recale par atempo (rythme seul, pas la hauteur) si elle sort de la
# fenetre demandee.
DUREE_VOIX_VISEE = 61.5  # -> ~64.5s avec l'outro, au centre de 60-70s
DUREE_VIDEO_MIN, DUREE_VIDEO_MAX = 60.0, 70.0
# L'outro est collee APRES ce script, mais la fenetre 60-70 s porte sur la
# video FINIE. Sans en tenir compte ici, un corps de 69 s passait le controle
# et ressortait a 71.5 s une fois l'outro ajoutee (constate le 2026-08-23).
OUTRO_LOVEKITCHEN = None  # calcule au demarrage, voir duree_outro()
W, H = 1080, 1920


def _cle(fichier_env, nom):
    # L'ENVIRONNEMENT D'ABORD. Ajoute le 2026-09-12 : sur GitHub Actions la
    # cle arrive par `env:` depuis un secret, il n'y a aucun fichier .env a
    # cote du script. Sans ca, le montage mourait sur "GEMINI_API_KEY
    # introuvable" avant meme le premier tirage, et le pilote enchainait les
    # sept videos en croyant a sept echecs de rendu.
    depuis_env = os.environ.get(nom)
    if depuis_env:
        return depuis_env
    for p in (RACINE / fichier_env, ICI.parent / "beauty" / ".env"):
        if not p.exists():
            continue
        for ligne in p.read_text(encoding="utf-8").splitlines():
            if ligne.strip().startswith(nom + "="):
                v = ligne.split("=", 1)[1].strip()
                if v:
                    return v
    sys.exit(f"{nom} introuvable (cherche dans {fichier_env} et beauty/.env)")


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", **kw)


def ff(args, cwd=None):
    r = sh(["ffmpeg", "-v", "error"] + args, cwd=cwd)
    if r.returncode != 0:
        sys.exit(f"ffmpeg a echoue : {r.stderr[-2000:]}")


def duree(fichier):
    r = sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1", str(fichier)])
    return float(r.stdout.strip())


def duree_outro():
    """Duree de l'outro qui sera collee apres ce montage."""
    global OUTRO_LOVEKITCHEN
    if OUTRO_LOVEKITCHEN is None:
        f = RACINE / "outros" / "outro_lovekitchen.mp4"
        OUTRO_LOVEKITCHEN = duree(f) if f.exists() else 2.52
    return OUTRO_LOVEKITCHEN


def volume_moyen(fichier):
    r = sh(["ffmpeg", "-i", str(fichier), "-af", "volumedetect", "-f", "null", "-"])
    m = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", r.stderr)
    return float(m.group(1)) if m else None


# Un tirage Gemini TTS peut s'ETEINDRE en cours de piste : le debut est
# normal, la fin sonne chuchotee. Ca ne se voit pas dans la duree, seul
# critere de selection jusqu'au 2026-08-25 - deux videos sur neuf sont
# parties comme ca. Mesure sur les fichiers rendus, la video de reference
# (06_fraises, 250k vues) servant de temoin :
#
#   temoin 06_fraises ............ -0.9 dB   (ecart-type 0.93)
#   sept videos correctes ........ -0.0 a -1.9 dB
#   v16_gratinpommesviande ....... -5.7 dB   (ecart-type 2.53)
#   v18_ailespoulet .............. -5.7 dB   (ecart-type 2.56)
#
# Les distributions ne se recouvrent pas : un seuil a 3 dB les separe
# proprement. Mesurer sur la VOIX SEULE, jamais sur la video montee - les
# segments video sont rendus en -an, mais un futur mixage fausserait tout.
# ATTENTION AU SIGNAL MESURE. Le seuil ci-dessus a d'abord ete fixe a 3.0 dB
# d'apres des mesures faites sur les VIDEOS FINIES. Or derive_voix() est
# appelee sur le FICHIER DE VOIX BRUT, et le montage egalise ensuite le
# niveau : une fin de phrase qui retombe naturellement s'efface au mixage.
# Mesure des deux signaux sur les memes videos, le 2026-08-26 :
#
#   video            voix brute    video finie
#   tortillas           +0.2          -0.3
#   cheesecake          -2.7          -2.3
#   salade grecque      -3.9          -1.1
#   muffins choco       -4.1          -1.4
#
# La voix brute surestime la derive de 2 a 3 dB. A 3.0 dB le garde-fou
# refusait des videos qui finissent AU NIVEAU DU TEMOIN (-0.9 dB) : quatre
# rendus ont ete jetes pour rien. Le seuil passe donc a 6.0 dB, ce qui
# continue d'ecarter les mauvais tirages reels (-7.9 et -9.3 dB observes le
# meme jour, qui finissaient a -5.7 dB) sans refuser les bons.
#
# La derive reste un critere de CLASSEMENT entre tirages a tout moment :
# a durees comparables, le tirage le moins descendant gagne.
SEUIL_DERIVE_DB = 6.0

# CHAINE AUDIO DE LA VOIX — ajoutee le 2026-09-09.
#
# Jusqu'ici la voix passait par `loudnorm=I=-14:TP=-1.5:LRA=11` seul. loudnorm
# fixe le niveau MOYEN de la piste, mais `LRA=11` autorise onze unites d'ecart
# de sonie a l'interieur : une voix qui s'eteint progressivement reste donc
# conforme. Mesure sur les videos FINIES le 2026-09-09 :
#
#   v42 tortillas ........ -0.1 dB      v50 gateau choco ..... -3.0 dB
#   v43 gateau ........... +0.5 dB      v52 tarte peches ..... -3.7 dB
#                                       v51 roules fraise .... -4.7 dB
#
# Les anciennes sont plates, les quatre nouvelles perdent 2 a 4,7 dB : la fin
# du texte - celle qui doit donner envie de partager - arrive chuchotee.
# Choisir le meilleur tirage ne suffit pas, ca ne fait que limiter les degats.
#
# `speechnorm` remonte les passages faibles au fil de la piste AVANT que
# loudnorm ne fixe la sonie : la derive devient structurellement impossible au
# lieu de dependre du tirage. `p=0.95` laisse de la marge sous le plafond,
# `r=0.0005` fait evoluer le gain assez lentement pour ne pas s'entendre.
# LRA descend a 7 : au-dela, loudnorm laisse repasser ce que speechnorm vient
# d'egaliser.
# Reglage choisi le 2026-09-09 en comparant quatre chaines sur la voix la plus
# descendante du lot (v51, -4.7 dB au depart) :
#
#   speechnorm doux (e=12.5, r=0.0005) ......... -2.7 dB
#   speechnorm ferme (e=25, r=0.002) ........... -2.2 dB   <- retenu
#   dynaudnorm seul (f=250:g=15) ............... -2.9 dB
#   les deux enchaines .......................... -2.2 dB  (dynaudnorm n'apporte rien)
CHAINE_VOIX = ("aformat=fltp:44100:stereo,"
               "speechnorm=e=25:r=0.002:l=1:p=0.95,"
               "loudnorm=I=-14:TP=-1.5:LRA=7")


# RESPIRATION — ajoutee le 2026-09-09, apres un retour de l'utilisateur :
# "la voix parle trop vite et elle reprend le souffle comme une IA".
#
# Le debit, lui, n'avait pas bouge : 3,01 a 3,22 mots/s sur huit videos, les
# anciennes comme les nouvelles. Ce qui change, c'est le SILENCE.
#
#   temoins d'aout (l'epoque qui marchait) : 26 a 32 pauses, 28 a 32 %
#   v50 -> 24 pauses / 24 %      v53 -> 25 / 21 %      v55 -> 29 / 25 %
#   v54, celle que l'utilisateur a entendue -> 16 pauses / 15 %
#
# Un tirage qui enchaine les phrases sans respirer sonne recite, meme au bon
# debit. Le montage ne classait les tirages que sur la duree et la constance :
# rien n'ecartait celui qui ne respire pas.
def intonation(fichier, debut=0.0):
    """Evolution de l'intonation, en %, entre le premier et le dernier tiers.

    POURQUOI : l'utilisateur a entendu le 2026-09-11 une voix qui "perd son
    intonation et son souffle" en cours de video. Aucun controle ne le voyait :
    `derive_voix` mesure le NIVEAU, que la chaine audio egalise justement
    (speechnorm) - elle affichait -0,3 dB sur une voix devenue monotone.

    On mesure donc la VARIATION DE HAUTEUR (ecart-type de la frequence
    fondamentale, en demi-tons) par tiers. Temoins, les trois videos qui ont le
    plus marche sur la chaine :

        756 K ailes de poulet   -4 %
        256 K brioches fraises  +34 %
        145 K pates one-pot     -17 %

    contre -31 a -54 % sur sept des quatorze videos programmees ce jour-la.

    La hauteur n'est touchee ni par speechnorm (amplitude) ni par atempo (qui
    preserve la hauteur) : le meme seuil vaut en principe pour le tirage brut
    et la video finie. A VERIFIER sur des paires brut -> fini (regle n.1).
    """
    import numpy as np
    import librosa
    with tempfile.TemporaryDirectory() as td:
        wav = pathlib.Path(td) / "i.wav"
        ff(["-y", "-ss", str(debut), "-i", str(fichier), "-ac", "1", "-ar", "16000", str(wav)])
        y, sr = librosa.load(str(wav), sr=16000)
    f0, voise, _ = librosa.pyin(y, fmin=120, fmax=450, sr=sr,
                                frame_length=1024, hop_length=256)
    n = len(f0)
    ecarts = []
    for k in (0, 2):
        seg = f0[k * n // 3:(k + 1) * n // 3]
        seg = seg[voise[k * n // 3:(k + 1) * n // 3]]
        if len(seg) < 20:
            return -100.0          # plus de voix exploitable : elle s'est eteinte
        ecarts.append(float(np.std(12 * np.log2(seg / np.median(seg)))))
    return 100.0 * (ecarts[1] / max(0.01, ecarts[0]) - 1.0)


def respiration(fichier, seuil="-32dB", mini=0.25):
    """Part de la piste occupee par des silences, en pourcentage.

    Critere de CLASSEMENT entre tirages sur la voix brute, et critere
    BLOQUANT sur la video finie (seuil calibre sur ce signal-la, voir
    SEUIL_RESPIRATION_PC).
    """
    d = duree(fichier)
    if d <= 0:
        return 0.0
    r = sh(["ffmpeg", "-hide_banner", "-i", str(fichier), "-af",
            f"silencedetect=noise={seuil}:d={mini}", "-f", "null", "-"])
    total = sum(float(x) for x in
                re.findall(r"silence_duration: ([0-9.]+)", r.stderr))
    return total / d * 100.0


# Mesure sur les videos FINIES : temoins 28 a 32 %, la video jugee mauvaise
# 15 %. Le seuil est pose a 20 %, ce qui garde les trois recentes acceptables
# (21, 24, 25 %) et arrete celle qui ne respire pas.
SEUIL_RESPIRATION_PC = 20.0
# Meme mesure, sur le tirage BRUT, pour le classement des tirages. Voir le
# commentaire au point de classement pour la correspondance brut -> fini.
SEUIL_RESPIRATION_BRUT_PC = 25.0
# Perte d'intonation maximale entre premier et dernier tiers. Les trois videos
# qui ont le plus marche : -4, +34, -17 %. Les voix que l'utilisateur a jugees
# plates : -31 a -54 %. Voir intonation().
SEUIL_INTONATION_PC = -25.0


def derive_voix(fichier, fen=6.0):
    """Perte de niveau entre le premier et le dernier tiers de la piste.

    Renvoie un nombre NEGATIF quand la voix s'eteint (ex. -5.7), positif si
    elle monte. Renvoie 0.0 si la piste est trop courte pour etre jugee.
    """
    d = duree(fichier)
    if d < 4 * fen:
        return 0.0
    niveaux = []
    t = 0.0
    while t + fen <= d:
        r = sh(["ffmpeg", "-hide_banner", "-ss", f"{t}", "-t", f"{fen}",
                "-i", str(fichier), "-af", "volumedetect", "-f", "null", "-"])
        m = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", r.stderr)
        if m:
            niveaux.append(float(m.group(1)))
        t += fen
    if len(niveaux) < 4:
        return 0.0
    tiers = max(1, len(niveaux) // 3)
    debut = sum(niveaux[:tiers]) / tiers
    fin = sum(niveaux[-tiers:]) / tiers
    return fin - debut


# ----------------------------------------------------------------- voix
def gemini_tts(texte, dest, travail):
    cle = _cle("gemini.env", "GEMINI_API_KEY")
    # DEUX reglages successifs le 2026-09-09, et le premier etait faux.
    #
    # "Debit dynamique mais pas precipite" poussait au recite : le tirage de
    # v54 enchainait les phrases avec 15 % de silence quand les temoins d'aout
    # en ont 28 a 32 %.
    #
    # La correction "PRENDS TON TEMPS, debit tranquille" a bien ramene la
    # respiration (37 %), mais en ralentissant AUSSI la parole : 199 mots sont
    # passes de 63 s a 80 s, hors de la fenetre 60-70 s.
    #
    # Ce que font vraiment les temoins : ils parlent VITE dans la phrase et
    # s'arretent ENTRE les phrases. 29_gateaujiggly : 205 mots, 63,6 s, 32 % de
    # silence, soit 4,7 mots par seconde de parole reelle. La consigne doit
    # donc demander le silence sans toucher au debit.
    style = ("Lis ce texte a voix haute comme une confidence, en francais, voix "
              "de femme naturelle et chaleureuse. A l'interieur d'une phrase, "
              "parle a ton rythme normal, sans trainer sur les mots. Mais marque "
              "UNE VRAIE PAUSE d'une seconde a chaque retour a la ligne, et "
              "reprends ton souffle entre deux phrases. Ton complice, jamais "
              "theatral. Comme si tu racontais une anecdote vecue a une amie : "
              + texte)
    corps = json.dumps({
        "contents": [{"parts": [{"text": style}]}],
        "generationConfig": {"responseModalities": ["AUDIO"],
                              "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": GEM_VOICE}}}},
    }).encode("utf-8")
    for modele in ["gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts"]:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/{modele}"
               f":generateContent?key={cle}")
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(url, data=corps,
                                            headers={"Content-Type": "application/json"}),
                    timeout=120) as r:
                j = json.loads(r.read())
        except urllib.error.HTTPError as e:
            print(f"   {modele} HTTP {e.code}")
            continue
        for part in (j.get("candidates") or [{}])[0].get("content", {}).get("parts", []):
            d = part.get("inlineData") or part.get("inline_data")
            if d and d.get("data"):
                import base64
                pcm = travail / "v.pcm"
                pcm.write_bytes(base64.b64decode(d["data"]))
                ff(["-y", "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", str(pcm),
                    "-ar", "44100", "-b:a", "160k", str(dest)])
                return f"Gemini {GEM_VOICE} ({modele})"
    raise RuntimeError("Gemini TTS indisponible")


def voicebox_tts(texte, dest, travail):
    """Voix Sulafat CLONEE, servie par le Voicebox local (Qwen3-TTS).

    Sans quota et sans reseau, contrairement a Gemini qui plafonne a 10
    tirages par jour et par modele - c'est cette limite qui bornait la
    production de la chaine a quatre ou cinq videos par jour.

    Ce qui est clone est une voix de SYNTHESE deja utilisee par la chaine, pas
    une personne : la regle du projet interdit le clonage d'une personne
    identifiable, elle ne s'y oppose pas. Le profil vit dans
    `.voicebox_profil.json`, cree par `voicebox_sulafat.py --installer`.

    Reglage retenu apres deux essais mesures le 2026-09-09, sur la
    respiration (part de silence, temoin Gemini 27 %) :
        0.6B, reference de 16 s .... 13 %   refuse par l'utilisateur
        1.7B, reference de 29 s .... 22 %   retenu
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("vb", ICI / "voicebox_sulafat.py")
    vb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vb)
    wav = travail / (dest.stem + ".wav")
    vb.dire(texte, str(wav), "1.7B")
    ff(["-y", "-i", str(wav), "-ar", "44100", "-b:a", "160k", str(dest)])
    return "Voicebox Sulafat (Qwen3-TTS 1.7B, local)"


def edge_tts(texte, dest, travail):
    txt = travail / "t.txt"
    txt.write_text(texte, encoding="utf-8")
    r = subprocess.run(["python", "-m", "edge_tts", "--voice", EDGE_VOICE,
                         "-f", str(txt), "--write-media", str(dest)],
                        capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"edge-tts a echoue : {r.stderr[-1000:]}")
    return f"edge-tts {EDGE_VOICE}"


# ----------------------------------------------------------- sous-titres
def _poste_groq(url, corps, entetes, timeout=180):
    for essai in range(4):
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(url, data=corps, headers=entetes),
                    timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code != 429 or essai == 3:
                print(f"   Groq HTTP {e.code} : {e.read()[:300]!r}")
                return None
            attente = int(e.headers.get("retry-after") or 0) or 15 * (essai + 1)
            print(f"   quota Groq, reprise dans {attente}s")
            time.sleep(attente)


def whisper_mots(audio_path, cle):
    frontiere = "----" + uuid.uuid4().hex
    lim = "\r\n"
    corps = b""
    for k, v in (("model", "whisper-large-v3-turbo"),
                 ("response_format", "verbose_json"),
                 ("timestamp_granularities[]", "word"),
                 ("language", "fr")):
        corps += (f"--{frontiere}{lim}Content-Disposition: form-data; "
                  f'name="{k}"{lim}{lim}{v}{lim}').encode()
    mime = mimetypes.guess_type(audio_path.name)[0] or "audio/mpeg"
    corps += (f"--{frontiere}{lim}Content-Disposition: form-data; name=\"file\"; "
              f'filename="{audio_path.name}"{lim}Content-Type: {mime}{lim}{lim}'
              ).encode() + audio_path.read_bytes() + lim.encode()
    corps += f"--{frontiere}--{lim}".encode()
    rep = _poste_groq("https://api.groq.com/openai/v1/audio/transcriptions", corps,
                       {"Authorization": f"Bearer {cle}", "User-Agent": UA,
                        "Content-Type": f"multipart/form-data; boundary={frontiere}"})
    if not rep:
        return None
    return rep.get("words")


def ass_time(s):
    s = max(0, s)
    cs = round(s * 100)
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    sec, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{sec:02d}.{cs:02d}"


# Sous-titres "stylés" : un mot a la fois, gros, en bas de l'ecran, avec un
# leger pop d'echelle a l'apparition et une alternance de deux couleurs
# (blanc / or) pour le rythme visuel - le style word-by-word popularise par
# les montages type Capcut/Hormozi, plus lisible et plus vivant que des
# blocs de 3 mots statiques.
ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,94,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,3,2,60,60,160,1
Style: Accent,Arial,94,&H004DD2FF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,3,2,60,60,160,1
Style: Title,Arial,88,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,2,60,60,700,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

POP = r"{\fad(30,30)\t(0,90,\fscx116\fscy116)\t(90,170,\fscx100\fscy100)}"


def esc(t):
    return t.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")


def _ligne_mot(i, mot, debut, fin):
    style = "Accent" if i % 2 else "Default"
    texte = esc(mot.strip().upper())
    return f"Dialogue: 0,{ass_time(debut)},{ass_time(max(fin, debut + 0.08))},{style},,0,0,0,,{POP}{texte}\n"


def ass_depuis_mots(mots, titre=None):
    ass = ASS_HEADER
    if titre:
        ass += (f"Dialogue: 0,{ass_time(0)},{ass_time(3.4)},Title,,0,0,0,,"
                f"{{\\fad(0,120)}}{esc(titre)}\n")
    for i, m in enumerate(mots):
        ass += _ligne_mot(i, m["word"], m["start"], m["end"])
    return ass


def ass_estime(texte, duree_voix, titre=None):
    ass = ASS_HEADER
    if titre:
        ass += (f"Dialogue: 0,{ass_time(0)},{ass_time(3.4)},Title,,0,0,0,,"
                f"{{\\fad(0,120)}}{esc(titre)}\n")
    mots = texte.split()
    total = sum(len(m) for m in mots) or 1
    t = 0.0
    for i, mot in enumerate(mots):
        d = duree_voix * (len(mot) / total)
        fin = t + d
        ass += _ligne_mot(i, mot, t, max(fin, t + 0.15))
        t = fin
    return ass


# --------------------------------------------------------------- video
def segment_intro(source, dest, t_debut, t_duree):
    """Les t_duree premieres secondes (depuis t_debut) de source, sans
    boucle ni changement de vitesse : c'est le plan fixe fourni tel quel."""
    chain = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps=30"
    args = ["-y"]
    if t_debut > 0:
        args += ["-ss", f"{t_debut:.3f}"]
    args += ["-i", str(source), "-t", f"{t_duree:.3f}",
             "-vf", chain, "-an", "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "17", str(dest)]
    ff(args)


def segment_corps(source, dest, t_debut, t_duree_cible):
    """Boucle la source indefiniment (comme montage-solo.js) et la coupe a
    t_duree_cible EN SORTIE, apres avoir applique la vitesse necessaire
    (bornee x0.5-x2.5) pour que le rythme reste naturel."""
    usable = max(0.1, duree(source) - t_debut)
    # Une source nettement plus courte que la cible se retrouve soit bouclee
    # (on revoit deux fois la meme preparation), soit fortement ralentie. Les
    # comptes @tagesrezept publient beaucoup de formats 20-35s : les ecarter.
    if usable < t_duree_cible * 0.78:
        print(f"   ATTENTION : source de {usable:.1f}s pour un corps de "
              f"{t_duree_cible:.1f}s — elle sera bouclee ou ralentie a l'exces. "
              f"Preferer une source d'au moins {t_duree_cible * 0.78:.0f}s.")
    # Vitesse PLAFONNEE a 1.15. Au-dela, on ne comprime pas la source : on
    # en prend seulement le debut (le `-t` de sortie s'en charge). Une source
    # de 82 s ramenee a 60 s donnait x1.37, et une preparation de cuisine a
    # +37 % parait precipitee — remonte par l'utilisateur le 2026-08-23.
    # Le ralenti reste borne a 0.5 pour les sources trop courtes.
    brut = usable / t_duree_cible if t_duree_cible > 0 else 1.0
    vitesse = max(0.5, min(1.15, brut))
    if brut > 1.15:
        print(f"   source de {usable:.0f}s pour {t_duree_cible:.0f}s : "
              f"x{brut:.2f} aurait paru precipite, on garde x1.15 et on coupe "
              f"la fin")
    chain = f"setpts=PTS/{vitesse:.4f}," if abs(vitesse - 1.0) > 0.01 else ""
    chain += f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps=30"
    args = ["-y", "-stream_loop", "-1"]
    if t_debut > 0:
        args += ["-ss", f"{t_debut:.3f}"]
    args += ["-i", str(source), "-t", f"{t_duree_cible:.3f}",
              "-vf", chain, "-an", "-c:v", "libx264", "-preset", "veryfast",
              "-crf", "17", str(dest)]
    ff(args)
    return vitesse


# ROTATION DES INTROS. Jusqu'au 2026-09-11, toutes les videos prenaient
# `cuisine_robe_bleue` : c'est celle des trois records (756 K, 256 K, 145 K),
# et produire_lot la mettait par defaut. L'utilisateur l'a releve - un abonne
# voyait le meme plan de 5 secondes a chaque video.
#
# Vues medianes par intro sur les 24 publiees dont l'intro est connue :
#   sophie_cuisine_micro 6 664 (n=3)   cuisine_robe_bleue 6 080 (n=10)
#   sophie_cuisine_four  2 480 (n=3)
# robe_bleue porte les records, sophie_micro fait aussi bien en mediane,
# sophie_four est la plus faible : elle ne sort qu'une fois sur cinq.
#
# Seules les intros EN CUISINE entrent dans la rotation : hors cuisine, la
# mesure d'aout donnait 265 a 1 388 vues contre 4 153 a 249 655.
ROTATION_INTROS = ["cuisine_robe_bleue", "sophie_cuisine_micro",
                   "cuisine_robe_bleue", "sophie_cuisine_micro",
                   "sophie_cuisine_four"]
HISTORIQUE_INTROS = ICI / "intros_historique.json"


def historique_intros():
    try:
        return json.loads(HISTORIQUE_INTROS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def choisir_intro():
    """Prochaine intro : on suit la rotation, jamais deux fois la meme d'affilee."""
    h = historique_intros()
    derniere = h[-1]["intro"] if h else None
    rang = len(h) % len(ROTATION_INTROS)
    for k in range(len(ROTATION_INTROS)):
        choix = ROTATION_INTROS[(rang + k) % len(ROTATION_INTROS)]
        if choix != derniere:
            return choix
    return ROTATION_INTROS[0]


def noter_intro(ref, sortie):
    h = historique_intros()
    h.append({"intro": ref, "video": pathlib.Path(sortie).stem,
              "date": time.strftime("%Y-%m-%d %H:%M")})
    HISTORIQUE_INTROS.write_text(json.dumps(h[-200:], ensure_ascii=False, indent=1),
                                 encoding="utf-8")


def charger_intro(ref):
    """Resout une reference d'intro depuis intros_valides.json.

    Le manifeste ne contient que des fenetres REGARDEES image par image, ou
    l'on voit la femme puis la cuisine / la nourriture. Passer par une
    reference plutot que par un chemin+timecode libres est deliberé : le
    2026-08-21, une intro prise a 0s dans un clip 'clean with me' a mis une
    salle de bain et un lavage de sol en tete de video, et la video etait
    deja programmee en publication quand le defaut a ete vu.
    """
    manifeste = json.loads((ICI / "intros_valides.json").read_text(encoding="utf-8"))
    dispo = {i["ref"]: i for i in manifeste["intros"]}
    if ref not in dispo:
        sys.exit(f"intro '{ref}' inconnue. Disponibles : {', '.join(sorted(dispo))}\n"
                 f"(pour en ajouter une : extraire des images, LES REGARDER, "
                 f"puis completer intros_valides.json)")
    entree = dispo[ref]
    # `dossier` est relatif a CE fichier depuis le 2026-09-12 : les intros
    # sont pre-decoupees dans le depot pour que le montage tourne sur GitHub
    # Actions. Un chemin absolu reste accepte (ancien fonctionnement local).
    dossier = pathlib.Path(manifeste["dossier"])
    if not dossier.is_absolute():
        dossier = ICI / dossier
    chemin = dossier / entree["fichier"]
    if not chemin.exists():
        sys.exit(f"fichier d'intro introuvable : {chemin}")
    # La fenetre validee prime TOUJOURS sur la duree cible : aucun clip
    # disponible ne tient 7s propres, et deborder d'une demi-seconde suffit
    # a mettre un nettoyeur vapeur ou une salle de bain en tete de video.
    fenetre = entree["fin"] - entree["debut"]
    duree = min(DUREE_INTRO, fenetre)
    if duree < 3.0:
        sys.exit(f"intro '{ref}' : fenetre validee de {fenetre:.1f}s, trop courte")
    return str(chemin.resolve()), entree["debut"], duree, entree


# --- Controle de registre -------------------------------------------------
#
# Mesure du 2026-09-08 sur les 29 videos publiees, chiffres Zernio :
#
#   scripts 01 a 29 : tutoiement final, 1 a 6 "temps sensuels" par texte
#                     -> mediane 5 199 vues, maximum 252 432 (201 partages)
#   scripts 30 a 43 : vouvoiement, ZERO temps sensuel
#                     -> 460 a 1 775 vues
#
# Le registre a change sans que personne ne le decide, et l'audience a suivi.
# C'est l'utilisateur qui l'a vu, pas le pipeline. Ce controle existe pour que
# ca ne se reproduise pas : un script qui a perdu le registre de la chaine ne
# part plus au montage, comme un clip sans tampon ne part plus en publication.
#
# Les "temps sensuels" sont les gestes du mari pendant qu'elle cuisine - il la
# regarde, il s'approche, il reste contre elle, il goute lentement. C'est la
# colonne vertebrale du format, pas un ornement : la video a 252 432 vues en
# compte quatre, et ses 201 partages sont ce qui a declenche la diffusion.
REGISTRE_MOTIFS = [
    r"regard", r"derri[eè]re moi", r"contre moi", r"lentement",
    r"s'est approch", r"sans me l[aâ]cher", r"dans mon dos",
    r"m'a fait rougir", r"la nuque", r"l'[eé]paule", r"ses mains",
]
REGISTRE_MIN = 2


def controle_registre(texte, chemin):
    """Refuse un script qui a perdu le registre de la chaine."""
    bas = texte.lower()
    manques = []
    if "tu veux savoir" not in bas and "tu veux connaitre" not in bas:
        manques.append("la question finale n'est pas au tutoiement "
                       "(\"Tu veux savoir laquelle ?\")")
    n = sum(1 for m in REGISTRE_MOTIFS if re.search(m, bas))
    if n < REGISTRE_MIN:
        manques.append("%d temps sensuel(s) sur %d attendus (il la regarde, "
                       "il s'approche, il reste contre elle, il goute "
                       "lentement)" % (n, REGISTRE_MIN))
    if manques:
        print("REFUS DE REGISTRE sur %s :" % chemin)
        for m in manques:
            print("  - " + m)
        print("  Voir scripts/06_fraises.txt (252 432 vues) comme modele.")
        sys.exit(2)
    print("0 bis) registre : tutoiement OK, %d temps sensuels" % n)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--intro-ref", required=True,
                   help="reference validee dans intros_valides.json, ou auto (rotation)")
    p.add_argument("--corps", required=True)
    p.add_argument("--corps-debut", type=float, default=0.0)
    p.add_argument("--script", required=True, help="fichier texte du script")
    p.add_argument("--titre", default="")
    p.add_argument("--sortie", required=True)
    p.add_argument("--moteur", choices=("gemini", "voicebox"), default="gemini",
                   help="voicebox : clone local, aucun quota (voir voicebox_tts)")
    p.add_argument("--voix-source",
                   help="reutiliser la bande son d'un rendu existant (mp4/mp3) "
                        "au lieu d'appeler le TTS. Sert a corriger l'IMAGE d'une "
                        "video sans redepenser du quota Gemini (10 req/jour/modele) "
                        "et sans risquer de changer de voix.")
    a = p.parse_args()
    if a.intro_ref == "auto":
        a.intro_ref = choisir_intro()
        # On note le choix TOUT DE SUITE, pas a la fin. Sinon un rendu refuse
        # (voix qui s'aplatit, respiration) ne fait pas avancer le tour et la
        # meme intro ressort au rendu suivant - constate le 2026-09-12.
        noter_intro(a.intro_ref, a.sortie)
    a.intro, a.intro_debut, a.intro_duree, _entree = charger_intro(a.intro_ref)
    print(f"0) intro '{a.intro_ref}' : {_entree['description']} "
          f"({a.intro_debut:.1f}s -> {a.intro_debut + a.intro_duree:.1f}s, "
          f"{a.intro_duree:.1f}s)")
    a.corps = str(pathlib.Path(a.corps).resolve())

    texte = pathlib.Path(a.script).read_text(encoding="utf-8").strip()
    controle_registre(texte, a.script)
    texte = re.sub(r"\s+", " ", texte)
    mots_n = len(texte.split())
    print(f"0) script : {mots_n} mots")

    with tempfile.TemporaryDirectory(prefix="lovekitchen_") as td:
        travail = pathlib.Path(td)

        # 1) voix — le debit de Gemini TTS varie fortement d'un appel a
        # l'autre pour un MEME texte (mesure le 2026-08-21 : 177 mots -> 66.1s
        # puis 54.2s). Plutot que de compenser un mauvais tirage par un gros
        # atempo (ca degrade l'audio : la voix se hache, sonne chuchotee sur
        # la fin), on retire le des jusqu'a 3 fois et on garde le tirage le
        # plus proche de la cible ; l'atempo final ne sert plus qu'a un petit
        # rattrapage, borne pour rester inaudible.
        if a.voix_source:
            src = pathlib.Path(a.voix_source).resolve()
            if not src.exists():
                sys.exit(f"voix source introuvable : {src}")
            voix = travail / "voix.mp3"
            ff(["-y", "-i", str(src), "-vn", "-ar", "44100", "-b:a", "160k", str(voix)])
            d_voix = duree(voix)
            D = d_voix + 0.6
            print(f"1) voix REUTILISEE de {src.name} -> {d_voix:.2f}s "
                  f"(video totale {D:.2f}s, aucun appel TTS)")

            # Une voix reutilisee doit passer le MEME recalage que les voix
            # fraiches : sans ca elle sortait de la fenetre 60-70 s sans que
            # rien ne la rattrape (constate le 2026-08-23, 70.01 s).
            if not (DUREE_VIDEO_MIN <= D + duree_outro() <= DUREE_VIDEO_MAX):
                facteur = max(0.90, min(1.12, d_voix / DUREE_VOIX_VISEE))
                recalee = travail / "voix_recalee.mp3"
                ff(["-y", "-i", str(voix), "-filter:a", f"atempo={facteur:.4f}",
                    "-ar", "44100", "-b:a", "160k", str(recalee)])
                voix = recalee
                d_voix = duree(voix)
                D = d_voix + 0.6
                print(f"   hors fenetre -> recale x{facteur:.3f} -> {d_voix:.2f}s "
                      f"(video {D:.2f}s)")

            _monter(a, travail, voix, d_voix, D, texte)
            return

        # Quota Gemini gratuit : 10 requetes/JOUR/modele
        # (GenerateRequestsPerDayPerProjectPerModel-FreeTier, quotaValue 10,
        # constate le 2026-08-21). A 2 videos/jour, 2 essais laissent de la
        # marge ; 3 essais + quelques re-rendus epuisent la journee entiere,
        # et le montage bascule alors sur edge-tts, donc sur une AUTRE VOIX.
        # Sans quota, un tirage de plus ne coute qu'un peu de temps machine :
        # on en fait quatre et on garde le mieux respire.
        # Trois tirages Gemini : un sur deux perdait son intonation le
        # 2026-09-11, deux tirages laissaient donc une chance sur quatre de
        # n'avoir que des mauvais. L'arret anticipe (premier tirage bon sur
        # tous les criteres) garde la depense moyenne autour de 1,8 requete.
        ESSAIS_MAX = 4 if a.moteur == "voicebox" else 3
        meilleure, meilleur_ecart, comment = None, None, None
        for essai in range(ESSAIS_MAX):
            candidate = travail / f"voix_{essai}.mp3"
            deterministe = False
            try:
                if a.moteur == "voicebox":
                    c = voicebox_tts(texte, candidate, travail)
                else:
                    c = gemini_tts(texte, candidate, travail)
            except Exception as e:
                print(f"   {a.moteur} KO : {e}")
                c = edge_tts(texte, candidate, travail)
                # edge-tts rend EXACTEMENT la meme piste a chaque appel :
                # retirer n'apporte rien, ca ne fait que perdre du temps.
                # Constate le 2026-08-21 : 3 essais, 53.23s les trois fois.
                deterministe = True
            d = duree(candidate)
            ecart = abs(d - DUREE_VOIX_VISEE)
            der = derive_voix(candidate)
            resp = respiration(candidate)
            inton = intonation(candidate)
            faible = der < -SEUIL_DERIVE_DB
            print(f"1) essai {essai + 1}/{ESSAIS_MAX} : {c} -> {d:.2f}s "
                  f"(ecart {ecart:.1f}s, derive {der:+.1f} dB, "
                  f"respiration {resp:.0f}%, intonation {inton:+.0f}%"
                  f"{' — S ETEINT' if faible else ''}"
                  f"{' — MONOTONE' if inton <= SEUIL_INTONATION_PC else ''})")
            # La VOIX prime sur la duree. Un tirage Sulafat un peu plus loin de
            # la cible vaut mieux qu'un tirage edge-tts un peu plus proche :
            # l'ecart de duree se rattrape a l'atempo, le changement de voix
            # s'entend. Le 2026-08-22 un take Sulafat a 53.6s a ete ecarte au
            # profit d'un edge-tts a 54.0s, pour 0.4s de difference.
            # Ordre des criteres : la VOIX d'abord (Gemini plutot qu'edge-tts),
            # puis la CONSTANCE du niveau, puis la duree. Une piste qui
            # s'eteint ne se rattrape par aucun traitement en aval, alors
            # qu'un ecart de duree se rattrape a l'atempo.
            # LA RESPIRATION DOIT ENTRER DANS LE CLASSEMENT. Elle est bloquante
            # sur la video finie (SEUIL_RESPIRATION_PC), mais jusqu'au
            # 2026-09-11 le classement l'ignorait : le montage a retenu pour
            # 73-bananecacao un tirage a 14 % plutot qu'un tirage a 27 %, parce
            # qu'il etait plus proche de la duree cible. Resultat : 9 % sur la
            # video finie, refusee - et deux requetes TTS perdues.
            #
            # Le seuil ne se transpose pas tel quel (regle de methode n.1) : la
            # respiration du tirage BRUT n'est pas celle de la video FINIE.
            # Paires mesurees le 2026-09-10 et 11 (brut -> fini) :
            #   14 -> 9   23 -> 19   26 -> 22   29 -> 28
            #   33 -> 31  34 -> 31   36 -> 31   38 -> 34
            # La video finie perd 2 a 5 points. Sous 25 % en brut, elle tombe
            # sous les 20 % bloquants ; a partir de 26 %, elle passe.
            essouffle = resp < SEUIL_RESPIRATION_BRUT_PC
            # L'INTONATION passe avant la duree, pour la meme raison que la
            # respiration : un ecart de duree se rattrape a l'atempo, une voix
            # qui devient monotone ne se rattrape par rien. Voir intonation().
            monotone = inton <= SEUIL_INTONATION_PC
            score = (deterministe, faible, monotone, essouffle, ecart)  # False < True : bon tirage gagne
            if meilleur_ecart is None or score < meilleur_ecart:
                meilleure, meilleur_ecart, comment = candidate, score, c
            # Arret anticipe : un tirage bon sur TOUS les criteres de voix, et
            # rattrapable a l'atempo (borne x1,12, soit ~8 s sur 70 s), suffit.
            # Avec trois tirages, c'est ce qui evite de bruler le quota.
            if (not deterministe and ecart <= 8.0 and not faible
                    and not essouffle and not monotone):
                break
            if deterministe:
                print("   moteur deterministe : inutile de retirer")
                break
        voix = meilleure
        d_voix = duree(voix)
        D = d_voix + 0.6
        der_ret = derive_voix(voix)
        print(f"1) voix retenue : {comment} -> {d_voix:.2f}s "
              f"(video totale {D:.2f}s, derive {der_ret:+.1f} dB)")
        if der_ret < -SEUIL_DERIVE_DB:
            print(f"   ATTENTION : la voix perd {abs(der_ret):.1f} dB entre le debut et "
                  f"la fin (seuil {SEUIL_DERIVE_DB} dB, temoin 06_fraises -0.9 dB).")
            print("   Elle sonnera chuchotee sur la fin. Tous les tirages disponibles")
            print("   avaient ce defaut : refaire quand le quota Gemini sera revenu.")
        if "edge-tts" in comment:
            print("   ATTENTION : voix de SECOURS (edge-tts Vivienne), pas Sulafat.")
            print("   La chaine perd sa voix habituelle - quota Gemini epuise.")
            print("   Ne pas publier tel quel : reprendre quand le quota est revenu.")

        if not (DUREE_VIDEO_MIN <= D + duree_outro() <= DUREE_VIDEO_MAX):
            facteur = max(0.90, min(1.12, d_voix / DUREE_VOIX_VISEE))
            voix_recalee = travail / "voix_recalee.mp3"
            ff(["-y", "-i", str(voix), "-filter:a", f"atempo={facteur:.4f}",
                "-ar", "44100", "-b:a", "160k", str(voix_recalee)])
            voix = voix_recalee
            d_voix = duree(voix)
            D = d_voix + 0.6
            print(f"   encore hors fenetre -> leger recalage atempo x{facteur:.3f} "
                  f"-> {d_voix:.2f}s (video {D:.2f}s)")
            if not (DUREE_VIDEO_MIN <= D <= DUREE_VIDEO_MAX):
                print(f"   ATTENTION : reste hors 60-70s malgre le recalage borne "
                      f"({D:.1f}s) — le meilleur tirage etait trop loin de la cible ; "
                      f"ajuster la longueur du script plutot que de forcer l'atempo.")

        _monter(a, travail, voix, d_voix, D, texte)


def _monter(a, travail, voix, d_voix, D, texte):
    """Image + sous-titres + assemblage, une fois la voix arretee."""
    if True:
        # 2) segments video
        intro_src = travail / "intro.mp4"
        segment_intro(a.intro, intro_src, a.intro_debut, a.intro_duree)
        d_corps_visee = D - a.intro_duree
        corps_seg = travail / "corps.mp4"
        vitesse = segment_corps(a.corps, corps_seg, a.corps_debut, d_corps_visee)
        print(f"2) intro {a.intro_duree:.1f}s + corps {d_corps_visee:.2f}s (vitesse x{vitesse:.2f})")

        liste = travail / "concat.txt"
        liste.write_text(f"file '{intro_src.name}'\nfile '{corps_seg.name}'\n", encoding="ascii")
        base = travail / "base.mp4"
        ff(["-y", "-f", "concat", "-safe", "0", "-i", str(liste),
            "-t", f"{D:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
            str(base)], cwd=travail)

        # 3) sous-titres
        ass_txt = None
        try:
            cle_groq = _cle("gemini.env", "GROQ_API_KEY")
            mots = whisper_mots(voix, cle_groq)
            if mots:
                ass_txt = ass_depuis_mots(mots, a.titre)
                print(f"3) sous-titres OK synchro Whisper ({len(mots)} mots)")
        except Exception as e:
            print(f"   Whisper KO : {e}")
        if ass_txt is None:
            ass_txt = ass_estime(texte, d_voix, a.titre)
            print("3) sous-titres OK (timings estimes)")
        ass_path = travail / "subs.ass"
        ass_path.write_text(ass_txt, encoding="utf-8")

        # 4) assemblage final
        vf = f"subtitles=f='{ass_path.name}',fade=t=out:st={max(0.5, D - 0.6):.2f}:d=0.5"
        sortie = pathlib.Path(a.sortie).resolve()
        ff(["-y", "-i", str(base), "-i", str(voix),
            "-vf", vf,
            "-filter_complex", "[1:a]" + CHAINE_VOIX + "[aout]",
            "-map", "0:v:0", "-map", "[aout]", "-t", f"{D:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
            "-movflags", "+faststart", str(sortie)],
           cwd=travail)

    # INTEGRITE DU FICHIER RENDU — voir montage_argile.py, meme incident du
    # 2026-08-29 : NAL corrompues et piste audio tronquee (53 s pour 65 s de
    # video) sans qu'aucune etape ne le signale. ffprobe annoncait la bonne
    # duree ; seul un decodage complet attrape le defaut.
    ctrl = subprocess.run(["ffmpeg", "-v", "error", "-i", str(sortie), "-f", "null", "-"],
                          capture_output=True, text=True, errors="replace")
    if ctrl.stderr.strip():
        print("4) FICHIER CORROMPU : le decodage complet remonte des erreurs.")
        print("  ", ctrl.stderr.strip().splitlines()[0][:200])
        print("   Ne pas publier : relancer le rendu (--voix-source pour ne pas")
        print("   reconsommer le quota Gemini si la voix est saine).")
        sys.exit(1)

    # CONSTANCE DE LA VOIX SUR LA VIDEO FINIE — bloquant depuis le 2026-09-09.
    #
    # Jusqu'ici la derive n'etait mesuree que sur la VOIX BRUTE, et servait a
    # classer les tirages : la video sortait meme quand le meilleur tirage
    # descendait. Quatre videos sont parties comme ca entre -2,0 et -4,7 dB.
    #
    # Ce controle-ci porte sur un AUTRE SIGNAL — le fichier final, celui que
    # l'abonne entend — et a donc son propre seuil (regle de methode n.1 : un
    # seuil vaut pour le signal sur lequel il est calibre).
    #
    #   temoins de l'epoque qui marchait : v42 -0,1 dB   v43 +0,5 dB
    #   avec la nouvelle chaine audio ... : v51 -0,7 dB
    #   les quatre videos fautives ...... : -2,0 / -3,0 / -3,7 / -4,7 dB
    #
    # Seuil a -2,5 dB : il laisse passer ce que la chaine audio produit et
    # arrete tout ce qui recommencerait a s'eteindre.
    SEUIL_DERIVE_FINALE_DB = 2.5
    der_finale = derive_voix(sortie)
    if der_finale < -SEUIL_DERIVE_FINALE_DB:
        print(f"4) VOIX QUI S'ETEINT : {der_finale:+.1f} dB entre le premier et le "
              f"dernier tiers (limite {-SEUIL_DERIVE_FINALE_DB:+.1f}).")
        print("   La fin du texte - celle qui declenche les partages - arriverait")
        print("   chuchotee. Ne pas publier : relancer le rendu.")
        sys.exit(3)
    print(f"4) constance de la voix : {der_finale:+.1f} dB (limite "
          f"{-SEUIL_DERIVE_FINALE_DB:+.1f})")

    resp_finale = respiration(sortie)
    if resp_finale < SEUIL_RESPIRATION_PC:
        print(f"4) VOIX QUI NE RESPIRE PAS : {resp_finale:.0f}% de silence "
              f"(minimum {SEUIL_RESPIRATION_PC:.0f}%, temoins d'aout 28 a 32%).")
        print("   Elle enchaine les phrases sans pause : c'est ce qui fait")
        print("   entendre une voix de synthese. Relancer le rendu.")
        sys.exit(4)
    print(f"4) respiration : {resp_finale:.0f}% de silence "
          f"(minimum {SEUIL_RESPIRATION_PC:.0f}%)")

    inton_finale = intonation(sortie, debut=DUREE_INTRO)
    if inton_finale <= SEUIL_INTONATION_PC:
        print(f"4) VOIX QUI S'APLATIT : l'intonation perd {abs(inton_finale):.0f}% "
              f"entre le debut et la fin (seuil {SEUIL_INTONATION_PC:.0f}%, temoins "
              f"-4 / +34 / -17%).")
        print("   C'est ce que l'utilisateur entend comme une voix qui perd son")
        print("   souffle. Relancer le rendu.")
        sys.exit(5)
    print(f"4) intonation : {inton_finale:+.0f}% entre debut et fin "
          f"(limite {SEUIL_INTONATION_PC:.0f}%)")

    vm = volume_moyen(sortie)
    print(f"4) son : {vm:.1f} dB moyen" if vm is not None else "4) son : mesure impossible")
    d_finale = duree(sortie)
    print(f"4) termine -> {sortie} ({d_finale:.1f}s)")
    if not (DUREE_VIDEO_MIN <= d_finale + duree_outro() <= DUREE_VIDEO_MAX):
        print(f"   ATTENTION : {d_finale + duree_outro():.1f}s avec l'outro, hors de la fenetre "
              f"{DUREE_VIDEO_MIN:.0f}-{DUREE_VIDEO_MAX:.0f}s demandee ({d_finale:.1f}s)")


if __name__ == "__main__":
    main()
