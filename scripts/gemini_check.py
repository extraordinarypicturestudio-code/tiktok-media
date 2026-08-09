#!/usr/bin/env python3
"""
Verification visuelle automatique via l'API Gemini (tier gratuit Google).

Objectif : remplacer la relecture manuelle d'images par un appel a un modele
de vision gratuit, pour deux criteres qu'un script OCR ne peut pas juger :
  1. La video montre-t-elle UNE SEULE recette complete (pas de melange,
     pas d'etape manquante) ?
  2. Le contenu est-il approprie (pas de cadrage centre sur une personne
     plutot que sur l'action) ?

Cle API : cree gratuitement sur https://aistudio.google.com/apikey (aucune
carte bancaire requise). A stocker dans GEMINI_API_KEY (variable d'env ou
fichier gemini.env, meme principe que zernio.env).

Cout : 0 - tier gratuit Gemini (jusqu'a ~500-1000 requetes/jour selon le
modele). Ne consomme aucun credit Claude.

Usage :
  python gemini_check.py recette <video.mp4>
  python gemini_check.py fail <video.mp4>
"""

import base64
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
import urllib.error

GEMINI_MODEL = "gemini-flash-lite-latest"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

PROMPTS = {
    "recette": (
        "Tu analyses une video de cuisine composee de plusieurs images "
        "extraites a intervalles reguliers (frise chronologique de gauche a "
        "droite, haut en bas). Le format impose 'mains uniquement, jamais de "
        "visage' - examine CHAQUE image individuellement, y compris celles "
        "en bas a droite (fin de la video), ou un createur apparait parfois "
        "juste pour gouter/signer le plat. Reponds en JSON strict avec ces "
        "champs :\n"
        '{"recette_unique": true/false, "etape_manquante": true/false, '
        '"visage_identifiable": true/false, "raison": "explication courte", '
        '"verdict": "OK" ou "REJET"}\n\n'
        "recette_unique = false si plusieurs plats differents sont montres.\n"
        "etape_manquante = true si on saute clairement une transformation "
        "(ex: passe d'un ingredient cru a un plat cuit sans montrer la "
        "cuisson).\n"
        "visage_identifiable = true si un visage humain reconnaissable "
        "apparait dans NE SERAIT-CE QU'UNE SEULE image, meme brievement ou "
        "meme si le reste de la video ne montre que des mains.\n"
        "verdict = REJET si recette_unique est false OU etape_manquante est "
        "true OU visage_identifiable est true, sinon OK."
    ),
    "fail": (
        "Tu analyses une video de fails/humour composee de plusieurs images "
        "extraites a intervalles reguliers. Reponds en JSON strict :\n"
        '{"contenu_approprie": true/false, "raison": "explication courte", '
        '"verdict": "OK" ou "REJET"}\n\n'
        "contenu_approprie = false si le cadrage se concentre sur le corps "
        "d'une personne plutot que sur une action/chute/erreur comique, ou "
        "si le contenu semble sexualise plutot que humoristique."
    ),
}


def contact_sheet(video, tiles=24):
    """Genere une planche de contact (grille d'images) pour un clip."""
    dur_out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video],
        capture_output=True, text=True)
    duree = float(dur_out.stdout.strip())
    fps = tiles / duree

    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    tmp.close()
    subprocess.run(
        ["ffmpeg", "-y", "-i", video, "-vf",
         f"fps={fps},scale=180:-1,tile=6x{(tiles + 5) // 6}",
         "-frames:v", "1", tmp.name],
        capture_output=True)
    return tmp.name


def _cle_depuis_env():
    cle = os.environ.get("GEMINI_API_KEY")
    if cle:
        return cle
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gemini.env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as fh:
            for ligne in fh:
                if ligne.startswith("GEMINI_API_KEY="):
                    return ligne.split("=", 1)[1].strip()
    return None


def gemini_call(image_path, prompt):
    cle = _cle_depuis_env()
    if not cle:
        raise RuntimeError("GEMINI_API_KEY non definie (ni env, ni gemini.env)")

    with open(image_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/jpeg", "data": img_b64}},
            ]
        }],
        "generationConfig": {"responseMimeType": "application/json"},
    }

    req = urllib.request.Request(
        f"{GEMINI_URL}?key={cle}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Gemini {e.code}: {e.read().decode()[:300]}")

    texte = data["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(texte)


def verifier(type_contenu, video):
    if type_contenu not in PROMPTS:
        raise ValueError(f"type inconnu: {type_contenu}")

    img = contact_sheet(video)
    try:
        resultat = gemini_call(img, PROMPTS[type_contenu])
    finally:
        os.remove(img)
    return resultat


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: gemini_check.py <recette|fail> <video.mp4>")
        sys.exit(1)
    r = verifier(sys.argv[1], sys.argv[2])
    print(json.dumps(r, indent=2, ensure_ascii=False))
    sys.exit(0 if r.get("verdict") == "OK" else 1)
