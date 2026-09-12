#!/usr/bin/env python3
"""Garde-fou technique : derniere verification avant qu'une video parte en file.

    python verification_publication.py --chaine recipecrave \
        --legende "Focaccia maison" --source lot/RAW/focaccia.mp4 sortie.mp4

Il existe parce que les controles precedents laissaient passer des defauts
REELS, constates en production :

  - 2026-08-19 : un clip publie sous la legende "Drink recipe idea" montrait
    un plat de riz. Les legendes avaient ete ecrites en lot, sans regarder
    les videos. -> controle LEGENDE.
  - 2026-08-19 : le meme lot a ete signale par TikTok. Le recadrage anti-
    empreinte avait bien ete applique, mais la BANDE SON D'ORIGINE avait ete
    conservee : c'est la musique du createur, empreintee par TikTok, qui
    declenche le signalement. -> controle ORIGINE AUDIO.
  - 2026-08-19 : les 17 clips en attente duraient tous moins de 40 s alors
    que la consigne etait 40 s minimum. -> controle SPECS.
  - 2026-08-17 : une video beauty est sortie totalement muette. -> controle
    SON.
  - recipe_crave : des clips ne montraient que le plat fini, sans aucune
    etape de preparation. -> controle PREPARATION.

Chaque controle renvoie une PREUVE (valeur mesuree), pas un avis. Le verdict
est REJET des qu'un controle bloquant echoue. Sortie JSON avec --json.
"""

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile

import numpy as np

# RACINE = la racine du PROJET, pas le dossier de ce fichier. Le meme fichier
# sert a deux endroits : a plat dans "Project 1 TIKTOK" (usage local) et dans
# `pipeline/` du depot (usage GitHub Actions, PC eteint). On reconnait la
# racine a ce qu'elle porte `outros/`, plutot que de figer une profondeur.
def _racine(depart):
    p = depart
    for _ in range(3):
        if (p / "outros").is_dir():
            return p
        p = p.parent
    return depart

RACINE = _racine(pathlib.Path(__file__).resolve().parent)
sys.path.insert(0, str(RACINE))

from controle_publication import controler, planche_de_contact  # noqa: E402
from gemini_check import gemini_call  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

# Bibliotheques musicales de reference, par chaine. Le controle audio identifie
# la piste posee sur la video ; il faut donc qu'il regarde le MEME dossier que
# le montage. Le 2026-09-02, recipe_crave a bascule de `beauty/music/pixabay/`
# (dix fichiers sans aucune preuve de licence, voir regle 10) vers ses propres
# pistes Kevin MacLeod : ce chemin doit suivre, sinon la verification ne
# reconnait plus rien et rejette des videos parfaitement valides.
BIBLIO_MUSIQUE = RACINE / "channels" / "beauty" / "music" / "pixabay"  # repli historique
REGISTRE_HASHS = (RACINE / "used-clips-hashes.json"
                  if (RACINE / "used-clips-hashes.json").exists()
                  else RACINE.parent / "tiktok-media-work" / "used-clips-hashes.json")

# duree_min : recipe_crave = 40 s (consigne du 19/08/2026).
# profil : profil de controle visuel ; None = deja controle au montage
#   (esprit_libre incruste NOS sous-titres, un controle apres coup les
#   prendrait pour du texte tiers et rejetterait 100% des clips).
# audio_biblio : la bande son DOIT provenir de la bibliotheque libre de
#   droits. False = la voix d'origine est le contenu meme (esprit_libre).
CHAINES = {
    "recipecrave": {"duree_min": 40.0, "profil": "cuisine", "audio_biblio": True,
                    "musique": RACINE / "channels" / "recipe_crave" / "music",
                    "outro": "outro_recipecrave.mp4", "preparation": True,
                    "pseudo": "recipe_crave"},
    "espritlibre": {"duree_min": 15.0, "profil": None, "audio_biblio": False,
                    "outro": "outro_espritlibre.mp4", "preparation": False,
                    "pseudo": "esprit.libre18", "parole_attendue": True,
                  "seuil_doublon": 0.08},
    # Mindshift remplace Esprit Libre depuis le 2026-08-22 : meme format
    # (images reelles sous-titrees en francais, jamais de clonage vocal),
    # meme duree minimale, mais outro et compte differents. Les montages
    # espritlibre repris ici ont eu leur outro echangee, pas re-rendue :
    # les deux outros font 2,50 s exactement.
    "mindshift": {"duree_min": 15.0, "profil": None, "audio_biblio": False,
                  "outro": "outro_mindshift.mp4", "preparation": False,
                  "pseudo": "mindshift716", "parole_attendue": True,
                  "seuil_doublon": 0.08},
    "toprank": {"duree_min": 10.0, "profil": "sport", "audio_biblio": False,
                "outro": "outro_toprank.mp4", "preparation": False,
                "pseudo": "toprank.tv1",
                    "compilation": True},
    "nextlevelplays": {"duree_min": 10.0, "profil": "sport", "audio_biblio": False,
                       "outro": "outro_nextlevelplays.mp4", "preparation": False,
                       "pseudo": "nextlevelplays88",
                    "compilation": True},
    # love_kitchen97 MANQUAIT ICI jusqu'au 2026-08-26. Consequence : la
    # chaine la plus performante du lot (mediane 5199 vues contre 300-313
    # pour les autres) etait la SEULE dont aucune image n'etait verifiee.
    # Une fillette est partie en publication le 2026-08-26 dans
    # 11_glaceoreo, sur le plan de degustation de la source.
    # duree_min 60 : Creator Rewards exige plus de 60 s, et le format vise
    # deja 60-70 s. parole_attendue : la voix de synthese est le contenu.
    # profil "lovekitchen" et non "cuisine" - voir controle_publication.py.
    "lovekitchen": {"duree_min": 60.0, "profil": "lovekitchen", "audio_biblio": False,
                    "outro": "outro_lovekitchen.mp4", "preparation": False,
                    "pseudo": "love_kitchen97", "parole_attendue": True,
                    # `parole_attendue` ne sert qu'a la bande son ici : la
                    # grille de legende est declaree a part, sinon la chaine
                    # est jugee comme un discours (voir PROMPT_LEGENDE_*).
                    "grille_legende": "recit_cuisine"},
    # argile.histoires : ouverte le 2026-08-26, meme moteur que love_kitchen
    # (voix narrative de synthese + sous-titres mot a mot + outro) mais SANS
    # intro : le corps occupe toute la video. Compte sur la QUATRIEME cle.
    # Meme profil de controle que love_kitchen : nos sous-titres et notre
    # outro ne doivent pas etre lus comme du texte tiers, mais mineur_visible
    # et logo_marque restent bloquants.
    "argile": {"duree_min": 60.0, "profil": "lovekitchen", "audio_biblio": False,
               "outro": "outro_argile.mp4", "preparation": False,
               "pseudo": "argile.histoires", "parole_attendue": True,
              "narration_off": True},
    # Le Pisciniste : narration masculine sur des images de nettoyage de
    # piscine. duree_min a 62 s parce que l'utilisateur exige "une minute
    # minimum" ET que le Programme de recompenses createur demande plus de
    # 60 s : viser 60 pile laisserait passer des videos inelegibles.
    # audio_biblio False : la bande son est la VOIX, la musique n'est qu'un
    # lit. parole_attendue True, sinon le controle "aucune parole" rejette
    # 100 % des videos, comme il l'a fait sur mindshift le 2026-08-22.
    "pisciniste": {"duree_min": 62.0, "profil": "pisciniste", "audio_biblio": False,
                   "outro": "outro_pisciniste.mp4", "preparation": False,
                   "pseudo": "le.pisciniste7", "parole_attendue": True,
                  "narration_off": True},
}

# Une correspondance audio reelle sort a ~1.00 (temoin : video fabriquee avec
# une piste connue). Deux morceaux differents mais de meme energie montent a
# 0.63. Le seuil est donc haut, ET on exige un ecart net avec le second :
# sans cette marge, n'importe quelle musique rythmee "matchait".
SEUIL_AUDIO = 0.85
# Quand la musique n'est qu'un lit derriere les bruits de cuisine, elle est
# 12 dB plus bas : la correlation tombe vers 0.6 sans que rien ne soit
# anormal. Mesure du 2026-08-20 : 0.63 pour la bonne piste, 0.24 pour la
# suivante - l'ecart reste franc.
SEUIL_AUDIO_MELANGE = 0.50
MARGE_AUDIO = 0.15


def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace")


def sonde(video, entrees, flux=None):
    cmd = ["ffprobe", "-v", "error"]
    if flux:
        cmd += ["-select_streams", flux]
    cmd += ["-show_entries", entrees, "-of", "default=nw=1:nk=1", str(video)]
    return sh(cmd).stdout.strip()


# ----------------------------------------------------------- controles

def c_specs(video, conf):
    """Resolution, FPS, duree minimale, taille de fichier."""
    larg = sonde(video, "stream=width", "v:0")
    haut = sonde(video, "stream=height", "v:0")
    fps_brut = sonde(video, "stream=r_frame_rate", "v:0")
    duree = float(sonde(video, "format=duration") or 0)
    taille = os.path.getsize(video) / 1024 / 1024
    num, _, den = fps_brut.partition("/")
    fps = float(num) / float(den or 1)

    problemes = []
    if (larg, haut) != ("1080", "1920"):
        problemes.append(f"resolution {larg}x{haut} au lieu de 1080x1920")
    if not 23 <= fps <= 60:
        problemes.append(f"FPS {fps:.1f} hors de la plage 23-60")
    if duree < conf["duree_min"]:
        problemes.append(f"duree {duree:.1f}s < minimum {conf['duree_min']:.0f}s")
    if taille > 100:
        problemes.append(f"taille {taille:.0f} Mo > limite GitHub de 100 Mo")
    return {"bloquant": True,
            "preuve": f"{larg}x{haut}, {fps:.0f} fps, {duree:.1f}s, {taille:.1f} Mo",
            "problemes": problemes}


def enveloppe(fichier, secondes=25):
    """Enveloppe d'energie a 50 Hz : signature audio robuste au reencodage."""
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(fichier), "-t", str(secondes),
         "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
        capture_output=True)
    ech = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32)
    if ech.size < 8000:
        return None
    pas = 160  # 20 ms
    n = ech.size // pas
    env = np.sqrt((ech[:n * pas].reshape(n, pas) ** 2).mean(axis=1) + 1e-9)
    env = np.log1p(env)
    return (env - env.mean()) / (env.std() + 1e-9)


def ressemblance(a, b, decalage_max=250):
    """Meilleure correlation de Pearson entre deux enveloppes, a decalage pres.

    La correlation est recalculee SUR LE RECOUVREMENT de chaque decalage :
    normaliser une fois pour toutes puis tronquer donnait des scores > 1,
    donc de faux "c'est la meme musique". decalage_max = 250 pas de 20 ms,
    soit 5 s : au-dela, deux morceaux differents finissent toujours par
    s'aligner par hasard sur un motif rythmique commun.
    """
    if a is None or b is None:
        return 0.0
    meilleur = 0.0
    for d in range(-decalage_max, decalage_max + 1):
        x = a[max(d, 0):]
        y = b[max(-d, 0):]
        n = min(x.size, y.size)
        if n < 100:
            continue
        x, y = x[:n], y[:n]
        sx, sy = x.std(), y.std()
        if sx < 1e-6 or sy < 1e-6:
            continue
        r = float(np.dot(x - x.mean(), y - y.mean()) / (n * sx * sy))
        meilleur = max(meilleur, abs(r))
    return meilleur


def c_audio(video, conf, source):
    """Son present, non muet, et surtout : D'OU VIENT-IL.

    C'est le controle qui manquait le 19/08. Recadrer l'image ne protege de
    rien si la piste audio du createur est republiee telle quelle : c'est
    elle que TikTok empreinte.
    """
    problemes, preuves = [], []
    if not sonde(video, "stream=index", "a"):
        return {"bloquant": True, "preuve": "aucune piste audio",
                "problemes": ["aucune piste audio : la video sortirait muette"]}

    err = sh(["ffmpeg", "-i", str(video), "-af", "volumedetect",
              "-f", "null", "-"]).stderr
    moyen = None
    for ligne in err.splitlines():
        if "mean_volume:" in ligne:
            moyen = float(ligne.split("mean_volume:")[1].split("dB")[0])
    if moyen is None:
        problemes.append("volume non mesurable")
    else:
        preuves.append(f"volume moyen {moyen:.1f} dB")
        if moyen < -50:
            problemes.append(f"piste silencieuse ({moyen:.1f} dB)")

    env_video = enveloppe(video)

    # Deux montages legitimes, qui ne se verifient pas de la meme facon :
    #
    #  - bande son REMPLACEE par une musique de bibliotheque : elle doit y
    #    correspondre nettement ;
    #  - bande son d'origine CONSERVEE (bruits de cuisine) + lit musical a
    #    -12 dB : la musique y est noyee. Mesure du 2026-08-20 sur 12 videos
    #    correctes : correlations de 0.29 a 0.40, sans ecart avec la seconde
    #    piste. Exiger une correspondance ici rejetait 12 videos sur 22, TOUTES
    #    a tort. Ce n'est pas le bon instrument : ce qui garantit l'absence de
    #    musique sous droits, c'est que la SOURCE en etait exempte.
    son_conserve = False
    if source and pathlib.Path(source).exists():
        r = ressemblance(env_video, enveloppe(source))
        son_conserve = r > 0.55
        preuves.append(f"ressemblance avec la source {r:.2f}"
                       + (" (son d'origine conserve)" if son_conserve else ""))
        if son_conserve:
            try:
                sys.path.insert(0, str(RACINE / "channels" / "recipe_crave"))
                from analyse_audio import son_conservable  # noqa: E402
                ok, detail = son_conservable(pathlib.Path(source))
                preuves.append(f"source : {detail}")
                if not ok:
                    problemes.append(
                        f"le son d'origine a ete conserve alors que la source "
                        f"n'est pas propre : {detail}")
            except Exception as e:
                problemes.append(f"verification de la source impossible ({e})")
    elif conf["audio_biblio"]:
        preuves.append("source non fournie : la bande son ne peut pas etre "
                       "recoupee avec l'original")

    if conf["audio_biblio"] and not son_conserve:
        biblio = conf.get("musique") or BIBLIO_MUSIQUE
        # `*.mp3` et non `t*.mp3` : le filtre sur "t" datait du nommage t1..t10
        # du dossier pixabay et ne trouvait aucune des pistes nommees par leur
        # titre reel.
        scores = sorted(((ressemblance(env_video, enveloppe(m)), m.name)
                         for m in biblio.glob("*.mp3")), reverse=True)
        if not scores:
            problemes.append(f"bibliotheque musicale introuvable ({biblio})")
        else:
            (s1, n1), (s2, _) = scores[0], (scores[1] if len(scores) > 1 else (0.0, ""))
            preuves.append(f"bibliotheque : {n1} {s1:.2f} (2e {s2:.2f})")
            # Deux cas legitimes depuis le 2026-08-20 :
            #  - musique seule : correspondance nette avec une piste (~1.00) ;
            #  - bruits de cuisine d'origine + lit musical a -12 dB : la
            #    musique est noyee, la correspondance tombe vers 0.6.
            # Dans les deux cas on exige un ECART net avec la deuxieme piste :
            # c'est lui qui distingue "notre musique est la" d'une
            # correlation de hasard entre deux morceaux rythmes.
            if s1 < SEUIL_AUDIO_MELANGE or (s1 - s2) < MARGE_AUDIO:
                problemes.append(
                    "aucune musique de la bibliotheque n'est identifiable dans la "
                    f"bande son (meilleur {s1:.2f}, second {s2:.2f})")

    # Le son final ne doit contenir AUCUNE parole : ni voix de createur
    # laissee par erreur, ni narration sur une chaine qui n'en veut pas.
    #
    # Sauf sur les chaines dont c'est le format meme : espritlibre et
    # mindshift publient des discours sous-titres, ou la voix d'origine est
    # le contenu. La regle etait ecrite sans exception et rejetait 100 % de
    # ces videos ; sur ces chaines on inverse l'attente, c'est l'ABSENCE de
    # voix qui est anormale.
    try:
        sys.path.insert(0, str(RACINE / "channels" / "recipe_crave"))
        from analyse_audio import analyser as analyser_bande_son  # noqa: E402
        r = analyser_bande_son(video)
        preuves.append(f"contenu sonore : {r.get('description')}")
        if conf.get("parole_attendue"):
            if not r.get("parole"):
                problemes.append("aucune voix dans la bande son alors que la "
                                 "chaine publie des discours sous-titres")
        elif r.get("parole"):
            problemes.append("une voix est presente dans la bande son finale")
    except Exception as e:
        problemes.append(f"analyse de la bande son impossible ({e})")

    return {"bloquant": True, "preuve": " ; ".join(preuves), "problemes": problemes}


PROMPT_LEGENDE = (
    "Tu verifies une video destinee a TikTok, presentee sous forme d'une "
    "planche d'images extraites a intervalles reguliers (de gauche a droite, "
    "de haut en bas). Reponds en JSON strict :\n"
    '{"contenu": "ce que montre reellement la video, 20 mots max", '
    '"aliments_visibles": ["..."], '
    '"montre_preparation": true/false, '
    '"legende_correspond": true/false, '
    '"raison": "explication courte"}\n\n'
    "montre_preparation = true seulement si on voit des ETAPES de fabrication "
    "(couper, melanger, cuire, petrir...). false si on ne voit que le plat "
    "fini, le dressage ou la degustation.\n"
    "legende_correspond = false si la legende annonce autre chose que ce qu'on "
    "voit (par exemple une boisson alors que la video montre du riz), ou si "
    "elle est si vague qu'elle pourrait decrire n'importe quelle video.\n\n"
    "LEGENDE A VERIFIER : "
)

# Le prompt ci-dessus est entierement formule pour la cuisine : il demande
# les aliments visibles, les etapes de preparation, et son exemple parle de
# riz et de boisson. Applique a une chaine de discours, le modele reprend
# cette grille et refuse des legendes correctes au motif qu'elles ne
# decrivent "aucune recette" - c'est ce qui a rejete la video de Jamel
# Debbouze le 2026-08-22, apres etre passe 34 fois par chance sur le meme
# lot. Les chaines a parole_attendue utilisent celui-ci.
PROMPT_LEGENDE_PAROLE = (
    "Tu verifies une video destinee a TikTok, presentee sous forme d'une "
    "planche d'images extraites a intervalles reguliers (de gauche a droite, "
    "de haut en bas). C'est un extrait de DISCOURS ou d'INTERVIEW : une "
    "personne parle, et des sous-titres sont incrustes. Il n'y a ni recette "
    "ni preparation a y chercher. Reponds en JSON strict :\n"
    '{"contenu": "ce que montre reellement la video, 20 mots max", '
    '"aliments_visibles": [], '
    '"montre_preparation": false, '
    '"legende_correspond": true/false, '
    '"raison": "explication courte"}\n\n'
    "legende_correspond = true si la legende exprime bien l'IDEE que la "
    "personne developpe, ou nomme correctement la personne qui parle. Une "
    "legende sous forme de citation ou de formule condensee est normale et "
    "attendue sur cette chaine.\n"
    "legende_correspond = false uniquement si la legende annonce un sujet "
    "ou une personne qui n'a rien a voir avec ce qu'on voit.\n\n"
    "LEGENDE A VERIFIER : "
)


# Troisieme grille, ajoutee le 2026-09-06. Les deux precedentes supposent
# toutes deux que le SUJET DE LA LEGENDE EST A L'IMAGE : une recette pour
# l'une, la personne qui parle pour l'autre. Sur une chaine de narration en
# voix off, il n'est ni l'un ni l'autre — on voit une piscine, on entend un
# pisciniste raconter. `06_le_pourboire_du_gamin` a ete refusee ce jour-la
# sur "piscine sale", le modele cherchant l'orateur annonce par le prompt.
# argile a le meme format et passait jusqu'ici par chance : la grille
# "parole" ne refuse que les ecarts francs.
PROMPT_LEGENDE_VOIXOFF = (
    "Tu verifies une video destinee a TikTok, presentee sous forme d'une "
    "planche d'images extraites a intervalles reguliers (de gauche a droite, "
    "de haut en bas). C'est une NARRATION EN VOIX OFF : le narrateur n'est "
    "PAS a l'image, on voit ce dont il parle ou le decor de son metier. Il "
    "n'y a ni recette, ni preparation, ni orateur filme a y chercher. "
    "Reponds en JSON strict :\n"
    '{"contenu": "ce que montre reellement la video, 20 mots max", '
    '"aliments_visibles": [], '
    '"montre_preparation": false, '
    '"legende_correspond": true/false, '
    '"raison": "explication courte"}\n\n'
    "legende_correspond = true si la legende annonce l'HISTOIRE racontee ou "
    "le theme du recit, meme si l'image ne montre que le decor. Une accroche "
    "ou une phrase d'anecdote est normale et attendue sur cette chaine : "
    "elle n'a pas a decrire l'image.\n"
    "legende_correspond = false uniquement si l'image contredit franchement "
    "la legende (un chantier de terrassement pour une anecdote de "
    "nettoyage), ou si elle est vide de sens.\n\n"
    "LEGENDE A VERIFIER : ")


# Cinquieme grille, ajoutee le 2026-09-12. love_kitchen cumule ce que les
# quatre autres separent : l'image montre une RECETTE, la voix est une
# NARRATION HORS CHAMP, et la legende est une ACCROCHE D'ANECDOTE qui ne
# nomme pas le plat ("Il rentrait d'un rendez-vous rate"). Faute de grille a
# elle, elle etait jugee par celle des discours - parce que `parole_attendue`
# servait a LA FOIS a exiger de la parole dans la bande son et a choisir la
# grille de legende. Cette grille annonce "ni recette ni preparation a y
# chercher" : le modele refusait donc 72-roulespoulet au motif exact que
# "la video montre une recette de cuisine et non un discours ou une
# interview". Le refus etait intermittent (11 videos sont passees), ce qui
# l'a rendu invisible : une variance de modele, pas un defaut de la video.
PROMPT_LEGENDE_RECIT_CUISINE = (
    "Tu verifies une video destinee a TikTok, presentee sous forme d'une "
    "planche d'images extraites a intervalles reguliers (de gauche a droite, "
    "de haut en bas). Format de la chaine : on voit la PREPARATION D'UNE "
    "RECETTE, parfois precedee d'un bref plan d'une femme dans sa cuisine, "
    "et une voix HORS CHAMP raconte une anecdote de couple pendant ce "
    "temps-la. Ne cherche pas d'orateur filme. Reponds en JSON strict :\n"
    '{"contenu": "ce que montre reellement la video, 20 mots max", '
    '"aliments_visibles": ["..."], '
    '"montre_preparation": true/false, '
    '"legende_correspond": true/false, '
    '"raison": "explication courte"}\n\n'
    "legende_correspond = true si la legende est une accroche de recit "
    "plausible pour cette scene de cuisine, ou si elle nomme le plat. Une "
    "phrase d'anecdote qui ne decrit pas l'image est NORMALE et attendue "
    "sur cette chaine : c'est son format.\n"
    "legende_correspond = false uniquement si elle nomme un PLAT que "
    "l'image ne montre pas (une legende de saumon sur un gateau), ou si "
    "elle est vide de sens.\n\n"
    "LEGENDE A VERIFIER : ")


# Quatrieme grille. Les trois autres supposent que la legende NOMME ce qu'on
# voit : un plat, un orateur, une histoire. Sur une compilation de fails, la
# legende est une PUNCHLINE — "That escalated fast", format de la chaine sur
# ses 28 publications. Le modele la refusait au motif qu'elle est "si vague
# qu'elle pourrait decrire n'importe quelle video", alors que sa propre
# description du contenu disait "des situations qui degenerent rapidement".
# Faux positif constate le 2026-09-06 sur tr_20260907_2100.
PROMPT_LEGENDE_COMPILATION = (
    "Tu verifies une video destinee a TikTok, presentee sous forme d'une "
    "planche d'images extraites a intervalles reguliers (de gauche a droite, "
    "de haut en bas). C'est une COMPILATION de plans courts et sans rapport "
    "entre eux (chutes, ratages, moments comiques). Il n'y a ni recette, ni "
    "orateur, ni fil narratif a y chercher.\n"
    "Reponds en JSON strict :\n"
    '{"contenu": "ce que montre reellement la video, 20 mots max", '
    '"aliments_visibles": [], '
    '"montre_preparation": false, '
    '"legende_correspond": true/false, '
    '"raison": "explication courte"}\n\n'
    "legende_correspond = true si la legende est une accroche ou une chute "
    "qui colle au TON de ce qu'on voit. Une formule courte et generique est "
    "le format normal et attendu de cette chaine : ne la refuse PAS pour "
    "cette raison.\n"
    "legende_correspond = false uniquement si la legende annonce un sujet "
    "absent de la video (une recette, un match, un tutoriel).\n\n"
    "LEGENDE A VERIFIER : ")


def c_contenu(video, conf, legende):
    """La legende decrit-elle vraiment cette video, et voit-on une preparation."""
    if not legende:
        return {"bloquant": True, "preuve": "aucune legende fournie",
                "problemes": ["legende absente : impossible de la verifier"]}
    img = planche_de_contact(str(video))
    try:
        if conf.get("grille_legende") == "recit_cuisine":
            prompt = PROMPT_LEGENDE_RECIT_CUISINE
        elif conf.get("compilation"):
            prompt = PROMPT_LEGENDE_COMPILATION
        elif conf.get("narration_off"):
            prompt = PROMPT_LEGENDE_VOIXOFF
        elif conf.get("parole_attendue"):
            prompt = PROMPT_LEGENDE_PAROLE
        else:
            prompt = PROMPT_LEGENDE
        rep = gemini_call(img, prompt + legende)
    except Exception as e:
        # Un controle qui n'a pas pu tourner n'autorise pas la publication.
        return {"bloquant": True, "preuve": f"echec : {e}",
                "problemes": [f"controle de legende impossible : {e}"]}
    finally:
        os.remove(img)

    problemes = []
    if rep.get("legende_correspond") is False:
        problemes.append(f"la legende ne correspond pas au contenu : {rep.get('raison')}")
    if conf["preparation"] and rep.get("montre_preparation") is False:
        problemes.append("la video ne montre aucune etape de preparation, "
                         "seulement le plat fini")
    return {"bloquant": True,
            "preuve": f"contenu vu : {rep.get('contenu')} | aliments : "
                      f"{rep.get('aliments_visibles')} | preparation : "
                      f"{rep.get('montre_preparation')}",
            "problemes": problemes}


def empreinte_image(chemin):
    from PIL import Image
    with Image.open(chemin) as im:
        g = np.asarray(im.convert("L").resize((8, 8)), dtype=np.float32)
    return g > g.mean()


def c_outro(video, conf):
    """L'outro de la chaine est-elle bien presente a la fin."""
    outro = RACINE / "outros" / conf["outro"]
    if not outro.exists():
        return {"bloquant": True, "preuve": "outro de reference introuvable",
                "problemes": [f"outro manquante dans outros/ : {conf['outro']}"]}
    d_v = float(sonde(video, "format=duration") or 0)
    d_o = float(sonde(outro, "format=duration") or 0)
    with tempfile.TemporaryDirectory() as tmp:
        a, b = os.path.join(tmp, "a.png"), os.path.join(tmp, "b.png")
        sh(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(d_v - d_o / 2, 0):.2f}",
            "-i", str(video), "-frames:v", "1", a])
        sh(["ffmpeg", "-v", "error", "-y", "-ss", f"{d_o / 2:.2f}",
            "-i", str(outro), "-frames:v", "1", b])
        if not (os.path.exists(a) and os.path.exists(b)):
            return {"bloquant": True, "preuve": "extraction impossible",
                    "problemes": ["impossible de verifier la presence de l'outro"]}
        ecart = int(np.count_nonzero(empreinte_image(a) != empreinte_image(b)))
    return {"bloquant": True, "preuve": f"ecart d'empreinte avec l'outro {ecart}/64",
            "problemes": [] if ecart <= 12 else
            [f"outro absente ou differente (ecart {ecart}/64)"]}


def c_doublon(video, conf=None):
    """Deja publie ? Deux mesures, parce qu'une seule ne suffit pas.

    Le sha256 ne voit que le fichier identique au bit pres : la meme video
    re-telechargee ou reencodee passait a travers (mise en garde de
    l'utilisateur, 2026-08-20). On y ajoute une empreinte perceptuelle,
    comparee a l'historique complet du depot.

    Temoins : deux montages differents d'une meme source sortent a 0.004 de
    distance, deux recettes differentes a 0.47.
    """
    h = hashlib.sha256(pathlib.Path(video).read_bytes()).hexdigest()
    problemes, preuves = [], [f"sha256 {h[:12]}"]

    if REGISTRE_HASHS.exists():
        registre = json.loads(REGISTRE_HASHS.read_text(encoding="utf-8"))
        connus = registre if isinstance(registre, list) else list(registre)
        if h in connus:
            problemes.append("ce fichier exact a deja ete publie")
    else:
        preuves.append("registre de hashs absent")

    try:
        sys.path.insert(0, str(RACINE))
        from empreintes_video import chercher_doublon  # noqa: E402
        nom, d = chercher_doublon(pathlib.Path(video),
                                  (conf or {}).get("seuil_doublon"))
        preuves.append(f"empreinte visuelle : plus proche {d:.3f}"
                       + (f" ({nom})" if nom else ""))
        if nom:
            problemes.append(f"meme contenu que '{nom}', deja publie "
                             f"(distance {d:.3f})")
    except Exception as e:
        problemes.append(f"comparaison d'empreinte impossible ({e})")

    return {"bloquant": True, "preuve": " ; ".join(preuves), "problemes": problemes}


def c_visuel(video, conf):
    """Watermark, texte incruste, logo source, visage, evenement pro."""
    if conf["profil"] is None:
        return {"bloquant": False,
                "preuve": "controle visuel fait au montage (avant incrustation "
                          "de nos sous-titres) : non rejouable ici",
                "problemes": []}
    r = controler(str(video), conf["profil"])
    return {"bloquant": True, "preuve": f"profil {conf['profil']} : {r['verdict']}",
            "problemes": list(r["motifs_refus"])}


# ---------------------------------------------------------------- main

def sans_outro(video, conf, dossier):
    """Copie de la video privee de son outro finale.

    Indispensable : l'outro porte volontairement notre pseudo et un appel a
    l'action. Analysee telle quelle, elle fait echouer le controle visuel
    ("texte incruste") et pollue le controle de legende, qui verrait un
    plan de fin la ou il cherche le contenu reel.
    """
    outro = RACINE / "outros" / conf["outro"]
    d_v = float(sonde(video, "format=duration") or 0)
    d_o = float(sonde(outro, "format=duration") or 0) if outro.exists() else 0.0
    utile = d_v - d_o
    if utile < 3:
        return str(video)
    dest = os.path.join(dossier, "sans_outro.mp4")
    sh(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-t", f"{utile:.2f}",
        "-c", "copy", dest])
    return dest if os.path.exists(dest) else str(video)


def verifier(video, chaine, legende, source):
    conf = CHAINES[chaine]
    with tempfile.TemporaryDirectory() as tmp:
        corps = sans_outro(video, conf, tmp)
        controles = {
            "specs": c_specs(video, conf),
            "audio": c_audio(video, conf, source),
            "visuel": c_visuel(corps, conf),
            "contenu": c_contenu(corps, conf, legende),
            "outro": c_outro(video, conf),
            "doublon": c_doublon(video, conf),
        }
    refus = [f"[{nom}] {p}" for nom, c in controles.items()
             if c["bloquant"] for p in c["problemes"]]
    return {"fichier": os.path.basename(video), "chaine": chaine,
            "legende": legende, "controles": controles,
            "motifs_refus": refus, "verdict": "OK" if not refus else "REJET"}


PROMPT_OUTRO = (
    "Cette image est la carte de fin d'une video TikTok. Reponds en JSON "
    'strict : {"pseudo_affiche": "le pseudo/compte exactement tel qu\'il '
    'apparait, ou null", "texte": "tout le texte lisible"}'
)


def verifier_outro_de_reference(chaine):
    """Verifie une fois que l'outro affiche bien le pseudo de LA BONNE chaine.

    Existe parce que outros/outro_recipecrave.mp4 affichait 'Cuisine_Beauty'
    au lieu de 'recipe_crave' : toutes les videos de la chaine sortaient avec
    un appel a s'abonner a un compte qui n'est pas le sien.
    """
    conf = CHAINES[chaine]
    outro = RACINE / "outros" / conf["outro"]
    if not outro.exists():
        return {"verdict": "REJET", "detail": f"outro absente : {conf['outro']}"}
    with tempfile.TemporaryDirectory() as tmp:
        img = os.path.join(tmp, "outro.jpg")
        d = float(sonde(outro, "format=duration") or 2)
        sh(["ffmpeg", "-v", "error", "-y", "-ss", f"{d / 2:.2f}", "-i", str(outro),
            "-frames:v", "1", "-q:v", "2", img])
        try:
            rep = gemini_call(img, PROMPT_OUTRO)
        except Exception as e:
            return {"verdict": "REJET", "detail": f"lecture impossible : {e}"}
    # Le nom AFFICHE sur TikTok peut differer de l'identifiant : recipe_crave
    # s'affiche "Cuisine_Beauty" (confirme par l'utilisateur le 19/08/2026).
    # generer_outros.py porte deja cette correspondance dans son champ
    # 'label' : on la lit la-bas plutot que de la redupliquer ici.
    attendus = {conf["pseudo"]}
    try:
        from generer_outros import CHAINES as CFG_OUTROS  # noqa: E402
        label = CFG_OUTROS.get(chaine, {}).get("label")
        if label:
            attendus.add(label)
    except Exception:
        pass

    affiche = ((rep.get("pseudo_affiche") or "") + " " + (rep.get("texte") or "")
               ).lower().replace("_", "")
    ok = any(a.lower().replace("_", "") in affiche for a in attendus)
    return {"verdict": "OK" if ok else "REJET",
            "detail": f"attendu l'un de {sorted(attendus)}, outro affiche : "
                      f"{rep.get('pseudo_affiche')!r} / texte {rep.get('texte')!r}"}


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("videos", nargs="+")
    p.add_argument("--chaine", required=True, choices=sorted(CHAINES))
    p.add_argument("--legende", help="legende exacte qui sera publiee")
    p.add_argument("--source", help="fichier source d'origine, pour comparer la "
                                    "bande son (fortement recommande)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--verifier-outro", action="store_true",
                   help="verifie aussi que l'outro de la chaine affiche le bon "
                        "pseudo (a relancer apres toute regeneration d'outro)")
    a = p.parse_args()

    if a.verifier_outro:
        r = verifier_outro_de_reference(a.chaine)
        print(f"[outro de reference] {r['verdict']} : {r['detail']}")
        if r["verdict"] != "OK":
            return 1

    rapports = [verifier(v, a.chaine, a.legende, a.source) for v in a.videos]
    if a.json:
        print(json.dumps(rapports, ensure_ascii=False, indent=2))
    else:
        for r in rapports:
            print(f"\n=== {r['fichier']} : {r['verdict']}")
            for nom, c in r["controles"].items():
                etat = "OK " if not c["problemes"] else "NON"
                print(f"  [{etat}] {nom:9} {c['preuve']}")
                for pb in c["problemes"]:
                    print(f"         -> {pb}")
    return 0 if all(r["verdict"] == "OK" for r in rapports) else 1


if __name__ == "__main__":
    sys.exit(main())
