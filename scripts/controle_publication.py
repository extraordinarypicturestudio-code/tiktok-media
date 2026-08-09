#!/usr/bin/env python3
"""
CONTROLE OBLIGATOIRE AVANT PUBLICATION.

Aucun clip ne doit entrer en file d'attente sans avoir passe ce controle.
Il existe parce que la detection OCR de tiktok_pipeline.py n'est PAS fiable :
le 09/08/2026, slide-fail-31 a ete declare "propre" par l'OCR alors qu'il
portait un watermark "@MillaChats" repete sur toute l'image plus la marque
"WATER CIRCUS". gym-comedy-36 etait couvert de sous-titres incrustes, et
gym-fails-28 affichait des logos de marques (XENDURANCE, Nike).

L'OCR echoue sur : texte semi-transparent, police stylisee, texte blanc sur
fond clair, logos graphiques sans lettres nettes. Un modele de vision voit
tout ca. C'est donc lui qui a le dernier mot.

Trois familles de risque controlees, toutes bloquantes :
  1. TEXTE / WATERMARK / LOGO  -> risque copyright + TikTok marque la video
     comme repost et la rend inelligible a la recommandation
  2. AUDIO (musique sous droits) -> risque de reclamation
  3. CADRAGE / CONTENU          -> corps, visages, mineurs

Usage :
  python controle_publication.py --profil cuisine <video.mp4> [...]
  python controle_publication.py --profil sport --dossier clips-toprank/
  python controle_publication.py --profil sport --json <video.mp4>

Code de sortie : 0 si TOUT est valide, 1 si au moins un clip est refuse.
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gemini_check import gemini_call, _cle_depuis_env  # noqa: E402


# La planche de contact doit etre DENSE : un watermark peut n'etre lisible
# que sur quelques images, et un logo de coin est petit. 24 vignettes en
# 4 colonnes laissent assez de pixels par vignette pour qu'un texte de
# petite taille reste dechiffrable par le modele.
TUILES = 24
COLONNES = 4
LARGEUR_VIGNETTE = 320


# Ce qui bloque depend de la chaine. Les trois premiers criteres sont des
# risques COPYRIGHT : ils bloquent partout, sans exception. "visage" en
# revanche est une regle de FORMAT propre a recipe_crave ("mains uniquement") :
# l'imposer a une chaine de sport ou de fails refuserait 100% des clips, un
# skateur ayant forcement un visage.
CRITERES_COPYRIGHT = {
    "watermark": "WATERMARK / pseudo / URL incruste",
    "texte_incruste": "texte incruste (sous-titres, legendes)",
    "logo_marque": "logo de marque commerciale",
}
CRITERES_SECURITE = {
    "cadrage_corps": "cadrage centre sur le corps",
    "mineur_visible": "mineur visible",
}
# nextlevelplays88 et toprank.tv1 sont des chaines de sport AMATEUR. Les
# images d'evenements professionnels (ligue, federation, championnat) sont
# sous droits de diffusion. Deja rencontre trois fois : un but de Bellingham,
# une competition d'une federation nationale de skate, une manche du WRC.
CRITERE_PRO = {"evenement_professionnel": "images d'evenement sportif professionnel (droits de diffusion)"}
CRITERE_VISAGE = {"visage_identifiable": "visage identifiable"}

PROFILS = {
    # recipe_crave : format "mains uniquement, jamais de visage"
    "cuisine": {**CRITERES_COPYRIGHT, **CRITERES_SECURITE, **CRITERE_VISAGE},
    # toprank.tv1 et nextlevelplays88 : le visage est inherent au contenu,
    # mais l'evenement professionnel est disqualifiant.
    "sport": {**CRITERES_COPYRIGHT, **CRITERES_SECURITE, **CRITERE_PRO},
}


PROMPT_VISUEL = (
    "Tu controles une video destinee a etre republiee sur TikTok. La chaine "
    "n'a AUCUN droit sur le contenu d'origine : tout element graphique qui "
    "identifie une autre source est disqualifiant, car TikTok marque alors la "
    "video comme repost et la rend inelligible a la recommandation.\n\n"
    "Ces images sont extraites a intervalles reguliers (de gauche a droite, "
    "de haut en bas). Examine CHAQUE image, y compris les bords et les coins, "
    "y compris un texte pale, semi-transparent, stylise, ou present sur une "
    "seule image.\n\n"
    "Reponds en JSON strict :\n"
    '{"watermark": true/false, "texte_incruste": true/false, '
    '"logo_marque": true/false, "visage_identifiable": true/false, '
    '"cadrage_corps": true/false, "mineur_visible": true/false, '
    '"evenement_professionnel": true/false, '
    '"details": "ce que tu as vu, en citant les textes lus", '
    '"verdict": "OK" ou "REJET"}\n\n'
    "watermark = true si un pseudo, un @handle, une URL, un nom de site ou "
    "un logo repete apparait (souvent en filigrane, en diagonale, ou fixe "
    "dans un coin sur toute la duree).\n"
    "texte_incruste = true s'il y a des sous-titres, des legendes, un texte "
    "de narration type 'POV:', un compteur, ou tout texte ajoute au montage. "
    "Le texte naturellement present dans la scene (panneau de rue, emballage "
    "d'un produit qu'on cuisine) ne compte PAS.\n"
    "logo_marque = true UNIQUEMENT si un logo sert de signature de source : "
    "logo d'un media, d'un agregateur, d'une chaine, d'un parc, d'un "
    "evenement ou d'une salle, incruste au montage ou filme de facon a "
    "identifier qui a produit la video. Un produit du commerce manipule dans "
    "la scene (paquet de pates, tablette de chocolat, bouteille de sauce, "
    "vetement de sport porte par quelqu'un) ne compte PAS : c'est un objet "
    "filme, pas une signature, et une video de cuisine en montre forcement.\n"
    "visage_identifiable = true si un visage humain reconnaissable apparait, "
    "meme sur une seule image.\n"
    "cadrage_corps = true si le cadrage se concentre sur le corps d'une "
    "personne (silhouette, fessier, torse, maillot de bain) plutot que sur "
    "l'action.\n"
    "mineur_visible = true si un enfant ou un bebe est visible de facon "
    "identifiable.\n"
    "evenement_professionnel = true si la scene est une competition ou un "
    "evenement organise de niveau professionnel : vehicule ou maillot aux "
    "couleurs d'une ecurie ou d'un club, dossard officiel, commissaire de "
    "course, barrieres et panneaux publicitaires d'epreuve, tribunes, foule "
    "de spectateurs, stade. Ces images sont sous droits de diffusion. Du "
    "sport pratique par des amateurs dans un lieu public ou prive ordinaire "
    "(skatepark, foret, riviere, rue, salle de sport) n'est PAS un evenement "
    "professionnel.\n"
    "verdict = REJET des qu'UN SEUL de ces champs est true. Dans le doute, "
    "REJET : un faux positif coute un clip, un faux negatif coute la chaine."
)


def planche_de_contact(video, tuiles=TUILES):
    """Grille dense d'images extraites regulierement sur toute la duree."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video],
        capture_output=True, text=True)
    duree = float(out.stdout.strip())
    fps = tuiles / duree
    lignes = (tuiles + COLONNES - 1) // COLONNES

    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    tmp.close()
    subprocess.run(
        ["ffmpeg", "-y", "-i", video, "-vf",
         f"fps={fps},scale={LARGEUR_VIGNETTE}:-1,tile={COLONNES}x{lignes}",
         "-frames:v", "1", "-q:v", "2", tmp.name],
        capture_output=True)
    return tmp.name


def controle_visuel(video):
    img = planche_de_contact(video)
    try:
        return gemini_call(img, PROMPT_VISUEL)
    finally:
        os.remove(img)


def controle_audio(video):
    """Verifie qu'une piste audio existe et n'est pas silencieuse.

    Le controle de la MUSIQUE elle-meme se fait en amont, sur l'URL source,
    par verifier_musique() dans tiktok_pipeline.py : c'est la seule etape ou
    les metadonnees TikTok ('track', 'artists') sont encore disponibles. Une
    fois le fichier telecharge, elles sont perdues. Ce controle-ci ne fait
    donc que confirmer qu'on n'a pas casse la piste au montage.
    """
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", video],
        capture_output=True, text=True)
    if "audio" not in out.stdout:
        return {"audio_present": False,
                "detail": "aucune piste audio : TikTok refuse ou la video sort muette"}

    # Niveau moyen : une piste presente mais silencieuse est aussi un defaut.
    r = subprocess.run(
        ["ffmpeg", "-i", video, "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True)
    moyen = None
    for ligne in r.stderr.splitlines():
        if "mean_volume:" in ligne:
            moyen = float(ligne.split("mean_volume:")[1].split("dB")[0].strip())
    if moyen is not None and moyen < -50:
        return {"audio_present": True, "mean_volume_db": moyen,
                "detail": "piste quasi silencieuse"}
    return {"audio_present": True, "mean_volume_db": moyen, "detail": "ok"}


def controle_format(video):
    """Resolution, duree, FPS : les contraintes dures de TikTok et du depot."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate",
         "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video],
        capture_output=True, text=True)
    vals = out.stdout.split()
    largeur, hauteur, taux, duree = int(vals[0]), int(vals[1]), vals[2], float(vals[3])
    num, den = taux.split("/")
    fps = float(num) / float(den)
    taille_mo = os.path.getsize(video) / (1024 * 1024)

    problemes = []
    if (largeur, hauteur) != (1080, 1920):
        problemes.append(f"resolution {largeur}x{hauteur} au lieu de 1080x1920")
    if not 23 <= fps <= 60:
        problemes.append(f"{fps:.1f} FPS hors de la plage 23-60 acceptee par TikTok")
    if duree < 8:
        problemes.append(f"{duree:.1f}s : trop court pour une publication")
    if taille_mo > 100:
        problemes.append(f"{taille_mo:.1f} MB : au-dessus de la limite GitHub de 100 MB")
    return {"largeur": largeur, "hauteur": hauteur, "fps": round(fps, 2),
            "duree": round(duree, 1), "taille_mo": round(taille_mo, 1),
            "problemes": problemes}


def controler(video, profil="cuisine"):
    """Renvoie un rapport complet. verdict == 'OK' seulement si tout passe."""
    rapport = {"fichier": os.path.basename(video), "profil": profil}
    rapport["format"] = controle_format(video)
    rapport["audio"] = controle_audio(video)
    try:
        rapport["visuel"] = controle_visuel(video)
    except Exception as e:
        # Un controle visuel qui echoue n'autorise PAS la publication.
        rapport["visuel"] = {"verdict": "REJET",
                             "details": f"controle visuel impossible : {e}"}

    motifs = []
    motifs += rapport["format"]["problemes"]
    if not rapport["audio"]["audio_present"]:
        motifs.append(rapport["audio"]["detail"])
    elif rapport["audio"]["detail"] != "ok":
        motifs.append(rapport["audio"]["detail"])

    v = rapport["visuel"]
    for champ, libelle in PROFILS[profil].items():
        if v.get(champ) is True:
            motifs.append(libelle)

    # Un visage sur une chaine sport ne bloque pas, mais reste signale :
    # c'est une information utile pour un arbitrage humain.
    if profil == "sport" and v.get("visage_identifiable") is True:
        rapport["avertissements"] = ["visage identifiable (non bloquant sur cette chaine)"]

    rapport["motifs_refus"] = motifs
    rapport["verdict"] = "OK" if not motifs else "REJET"
    return rapport


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("videos", nargs="*")
    p.add_argument("--dossier", help="controle tous les .mp4 d'un dossier")
    p.add_argument("--json", action="store_true", help="sortie JSON brute")
    p.add_argument("--profil", choices=sorted(PROFILS), default="cuisine",
                   help="cuisine = recipe_crave (visage bloquant) ; "
                        "sport = toprank.tv1 / nextlevelplays88")
    a = p.parse_args()

    videos = list(a.videos)
    if a.dossier:
        videos += sorted(glob.glob(os.path.join(a.dossier, "*.mp4")))
    if not videos:
        p.error("aucune video a controler")

    rapports = [controler(v, a.profil) for v in videos]

    if a.json:
        print(json.dumps(rapports, indent=2, ensure_ascii=False))
    else:
        for r in rapports:
            marque = "OK   " if r["verdict"] == "OK" else "REFUS"
            print(f"[{marque}] {r['fichier']}")
            f = r["format"]
            print(f"         {f['largeur']}x{f['hauteur']} | {f['duree']}s | "
                  f"{f['fps']} fps | {f['taille_mo']} MB")
            if r["verdict"] != "OK":
                for m in r["motifs_refus"]:
                    print(f"         -> {m}")
                d = r["visuel"].get("details")
                if d:
                    print(f"         vu : {d}")
        refuses = [r for r in rapports if r["verdict"] != "OK"]
        print(f"\n{len(rapports) - len(refuses)}/{len(rapports)} valide(s), "
              f"{len(refuses)} refuse(s).")

    sys.exit(1 if any(r["verdict"] != "OK" for r in rapports) else 0)


if __name__ == "__main__":
    main()
