#!/usr/bin/env python3
"""Refuse tout workflow qui reecrit a la main l'enregistrement des files.

POURQUOI CE TEST EXISTE
-----------------------
Releve du 2026-09-10 sur les 400 derniers runs (du 24/08 au 10/09) :

    Aligner les files sur Zernio        21 runs   7 echecs   33 %
    Veilleur (rattrapage continu)        5 runs   1 echec    20 %
    Programmer les sorties a l'avance    4 runs   1 echec    25 %
    Publish love_kitchen / Argile       31 runs   2 echecs
    -------------------------------------------------------------
    TOTAL                              400 runs  11 echecs

Les 11 echecs viennent TOUS de la meme etape : le bloc shell qui commite et
pousse les files. Aucun ne vient d'un script Python, de Zernio, ni d'une
video. Deux formes :

  A. `git add -- clips-argile/` sur un dossier absent -> exit 128 sous
     `bash -e`, huit fois. Reproduit a l'identique le 2026-09-10 :
     `bash -e -c 'git add -A -- ... clips-argile/'` sort en 128.
  B. Un conflit de rebase sur une file JSON laissait le depot en etat
     "unmerged", et les quatre tentatives suivantes echouaient toutes sur
     "Pulling is not possible because you have unmerged files", trois fois.

Ce bloc etait recopie dans QUATORZE workflows, en TROIS variantes
divergentes : quatre avec une garde d'existence, huit avec des chemins en
dur non gardes, une troisieme forme dans relancer-saturation. Corriger une
copie ne corrigeait pas les autres, et chaque nouveau workflow reintroduisait
le defaut - c'est arrive le 2026-09-09 avec `creneaux_manques.txt` dans
`veilleur.yml`, moins de deux heures apres avoir corrige les autres.

C'est pour ca que le probleme "revenait a chaque fois" : ce n'etait pas une
hypothese ratee, c'etait une architecture ou le meme code vit en quatorze
exemplaires.

CE QUE CE TEST GARANTIT
-----------------------
Un workflow n'appelle plus `git add`, `git commit` ni `git push` lui-meme :
il passe par `scripts/enregistrer.sh`, seul endroit ou cette logique existe.

Lancer :  python scripts/verifier_workflows.py
"""

import pathlib
import re
import sys

ICI = pathlib.Path(__file__).resolve().parent.parent
WORKFLOWS = ICI / ".github" / "workflows"

# `git config` reste permis : il ne peut pas echouer sur un pathspec.
INTERDITS = re.compile(r"\bgit\s+(add|commit|push|pull|rebase)\b")

# Le script partage a le droit - et le devoir - d'utiliser ces commandes.
EXEMPTS = {"enregistrer.sh"}


def controler():
    fautes = []
    for f in sorted(WORKFLOWS.glob("*.yml")):
        for numero, ligne in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            nu = ligne.strip()
            if nu.startswith("#"):
                continue
            m = INTERDITS.search(nu)
            if m:
                fautes.append((f.name, numero, m.group(1), nu[:90]))
    return fautes


def main():
    fautes = controler()
    if not fautes:
        print("OK : aucun workflow ne manipule git a la main.")
        print("     L'enregistrement des files passe par scripts/enregistrer.sh.")
        return 0

    print("ECHEC : %d ligne(s) de git a la main dans les workflows.\n" % len(fautes))
    print("Chacune est une copie du bloc qui a produit les 11 echecs du")
    print("2026-08-24 au 2026-09-10. Remplacer par :\n")
    print('    - run: scripts/enregistrer.sh "message" chemin [chemin...]\n')
    for nom, numero, commande, texte in fautes:
        print("  %-32s l.%-4d git %-7s | %s" % (nom, numero, commande, texte))
    return 1


if __name__ == "__main__":
    sys.exit(main())
