#!/usr/bin/env python3
"""
Regenere les outros "follow me" des 3 chaines TikTok.

Les outros n'ont longtemps existe que dans des scratchpads de session et
etaient perdus a chaque fois, obligeant l'utilisateur a redemander l'etape.
Ce script les reconstruit a l'identique et les ecrit dans `outros/`, qui est
versionne : ils ne doivent plus jamais disparaitre.

Recette (documentee en memoire projet, 02-06/08/2026) : avatar TikTok du
compte recupere via l'API Zernio, decoupe en cercle par masque alpha `geq`,
fondu en entree, puis le texte "Follow for more <X>" et le pseudo, sur fond
noir, ~2,5s en 1080x1920.

Usage :
  python generer_outros.py            # les 3 chaines
  python generer_outros.py toprank    # une seule
"""

import os
import subprocess
import sys
import urllib.request

ICI = os.path.dirname(os.path.abspath(__file__))
SORTIE = os.path.join(ICI, "outros")

W, H = 1080, 1920
DUREE = 2.5
TAILLE_LOGO = 400          # diametre du cercle dans la frame finale

ZERNIO_BASE = "https://zernio.com/api/v1"

# Windows n'a pas fontconfig : sans `fontfile` explicite, drawtext plante
# ("Cannot load default config file") et ffmpeg meurt sur un code d'erreur
# opaque. Le chemin doit etre echappe pour le parseur de filtres (`C\:/...`).
def _police(nom_fichier):
    chemin = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", nom_fichier)
    if not os.path.exists(chemin):
        raise RuntimeError(f"police introuvable : {chemin}")
    return chemin.replace("\\", "/").replace(":", r"\:")


POLICE_GRASSE = _police("arialbd.ttf")
POLICE_NORMALE = _police("arial.ttf")

# "pseudo" sert a retrouver le compte cote API Zernio (avatar) - c'est le
# handle technique (@...). "label" est ce qui s'affiche dans l'outro : par
# defaut identique au pseudo, sauf si le nom d'affichage reel sur TikTok
# differe du handle (ex: recipe_crave affiche "cuisine_beauty" comme nom de
# profil, confirme via GET /accounts -> displayName, TikTok limite le
# changement de displayName a 1x/7 jours donc l'incoherence persiste).
CHAINES = {
    "toprank":        {"pseudo": "toprank.tv1",      "env": "zernio.env",  "texte": "Follow for more fails"},
    "recipecrave":    {"pseudo": "recipe_crave",     "env": "zernio.env",  "texte": "Follow for more recipes", "label": "Cuisine_Beauty"},
    "nextlevelplays": {"pseudo": "nextlevelplays88", "env": "zernio2.env", "texte": "Follow for more sports"},
}


def cle(fichier_env):
    with open(os.path.join(ICI, fichier_env), encoding="utf-8") as fh:
        for ligne in fh:
            if ligne.startswith("ZERNIO_API_KEY="):
                return ligne.split("=", 1)[1].strip()
    raise RuntimeError(f"ZERNIO_API_KEY absente de {fichier_env}")


def url_avatar(pseudo, fichier_env):
    req = urllib.request.Request(
        ZERNIO_BASE + "/accounts",
        headers={"Authorization": f"Bearer {cle(fichier_env)}"})
    import json
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    items = data.get("accounts", data) if isinstance(data, dict) else data
    for a in items:
        if (a.get("username") or a.get("name")) == pseudo:
            return a["profilePicture"]
    raise RuntimeError(f"compte {pseudo} introuvable")


def telecharger(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as fh:
        fh.write(r.read())
    return dest


def rond(source, dest, taille=TAILLE_LOGO):
    """Decoupe l'avatar en cercle. Le masque alpha `geq` met a 0 tout pixel
    au-dela du rayon ; sans ca l'avatar reste un carre, visuellement moche
    sur fond noir."""
    r = taille / 2
    subprocess.run(
        ["ffmpeg", "-y", "-i", source,
         "-vf", (f"scale={taille}:{taille},format=rgba,"
                 f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
                 f"a='if(lte(hypot(X-{r},Y-{r}),{r}),255,0)'"),
         "-loglevel", "error", dest],
        check=True)
    return dest


def construire(nom, conf):
    os.makedirs(SORTIE, exist_ok=True)
    brut = os.path.join(SORTIE, f"_avatar_{nom}.jpg")
    cercle = os.path.join(SORTIE, f"_avatar_{nom}.png")
    final = os.path.join(SORTIE, f"outro_{nom}.mp4")

    telecharger(url_avatar(conf["pseudo"], conf["env"]), brut)
    rond(brut, cercle)

    # Le label affiche est soit le nom d'affichage reel (label explicite,
    # pas de @ : ce n'est pas un handle cliquable), soit par defaut le
    # handle technique avec son @.
    label = conf.get("label") or f"@{conf['pseudo']}"

    y_logo = (H - TAILLE_LOGO) // 2 - 160
    y_texte = y_logo + TAILLE_LOGO + 110
    y_pseudo = y_texte + 90

    # -loop 1 sur l'entree PNG est OBLIGATOIRE : sans lui ffmpeg ne lit
    # qu'une frame a pts=0, le fade s'applique dessus et le logo reste
    # invisible toute la duree - sans la moindre erreur a l'execution.
    subprocess.run(
        ["ffmpeg", "-y",
         "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:d={DUREE}",
         "-loop", "1", "-t", str(DUREE), "-i", cercle,
         "-f", "lavfi", "-i", f"anullsrc=channel_layout=stereo:sample_rate=44100:d={DUREE}",
         "-filter_complex",
         (f"[1:v]format=rgba,fade=in:st=0:d=0.6:alpha=1[logo];"
          f"[0:v][logo]overlay=(W-w)/2:{y_logo}[v1];"
          f"[v1]drawtext=fontfile='{POLICE_GRASSE}':text='{conf['texte']}':"
          f"fontcolor=white:fontsize=64:"
          f"x=(w-text_w)/2:y={y_texte}:"
          f"alpha='if(lt(t,0.6),t/0.6,1)'[v2];"
          f"[v2]drawtext=fontfile='{POLICE_NORMALE}':text='{label}':"
          f"fontcolor=0xBBBBBB:fontsize=46:"
          f"x=(w-text_w)/2:y={y_pseudo}:"
          f"alpha='if(lt(t,0.8),max(0\\,(t-0.2)/0.6),1)',setsar=1[vout]"),
         "-map", "[vout]", "-map", "2:a",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-c:a", "aac", "-b:a", "192k", "-pix_fmt", "yuv420p",
         "-loglevel", "error", final],
        check=True)

    for tmp in (brut, cercle):
        os.remove(tmp)
    return final


def main():
    demandes = sys.argv[1:] or list(CHAINES)
    for nom in demandes:
        if nom not in CHAINES:
            print(f"chaine inconnue : {nom} (attendu : {', '.join(CHAINES)})")
            sys.exit(2)
        chemin = construire(nom, CHAINES[nom])
        print(f"-> {chemin}")
    print("\nVerifier VISUELLEMENT une frame de chaque outro avant usage :")
    print("le piege -loop 1 casse le rendu en silence, sans erreur ffmpeg.")


if __name__ == "__main__":
    main()
