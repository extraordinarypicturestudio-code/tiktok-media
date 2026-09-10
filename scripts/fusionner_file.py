#!/usr/bin/env python3
"""Fusionne deux versions d'une file JSON, entree par entree.

POURQUOI
--------
Deux executions peuvent ecrire la meme file en meme temps : un workflow lit le
depot au demarrage de son job, travaille quelques minutes, puis commite - et
tout ce qui a ete pousse entre-temps entre en conflit.

Jusqu'au 2026-09-10, `enregistrer.sh` tranchait un conflit en gardant NOTRE
fichier en entier. C'est faux dans les deux sens et ca perd du travail sans
rien dire : le 2026-09-09 au soir, `56-onepotpates` a ete marquee `published`
a la main (l'utilisateur l'avait publiee lui-meme, son depot Zernio supprime)
et un workflow parti avant cette poussee l'a remise a `pending` en resolvant
son conflit. Le lendemain elle etait redeposee - exactement le doublon qu'on
cherchait a eviter.

Un conflit sur une file n'est pas un conflit de TEXTE, c'est deux avis sur
l'etat de quelques videos. On fusionne donc par identifiant, et pour chaque
video on garde l'etat LE PLUS AVANCE.

L'ordre encode ce qu'on peut se permettre de perdre :

  published  une video en ligne ne redevient jamais candidate. La reverter
             fait republier, c'est le pire cas.
  on_hold    decision explicite (humaine, ou d'une barriere). Une execution
             plus ancienne ne doit pas la lever.
  rejected   idem.
  failed     verdict porte sur un essai reel.
  scheduled  depot confirme chez Zernio.
  pending    l'etat par defaut : celui qu'on peut ecraser sans rien perdre.

En cas d'egalite, on garde NOTRE version : elle vient d'etre recalculee sur
l'etat reel des comptes.

Usage (appele par enregistrer.sh) :
    python3 scripts/fusionner_file.py <fichier> <notre.json> <leur.json>
"""

import json
import pathlib
import sys

RANG = {
    "published": 100,
    "on_hold": 90,
    "rejected": 90,
    "held": 90,
    "failed": 50,
    "scheduled": 40,
    "unconfirmed": 30,
    "pending": 10,
}


def rang(entree):
    return RANG.get(entree.get("status"), 20)


def fusionner(notre, leur):
    """Liste fusionnee, dans l'ordre de `notre` puis les ajouts de `leur`."""
    par_id = {}
    ordre = []

    # Notre file donne l'ordre : c'est celle que le run vient d'ecrire.
    for v in notre:
        cle = v.get("id") or v.get("url")
        if cle is None:
            continue
        par_id[cle] = v
        ordre.append(cle)

    # La leur n'apporte que ce qui est PLUS AVANCE, plus ses entrees inconnues.
    for v in leur:
        cle = v.get("id") or v.get("url")
        if cle is None:
            continue
        if cle not in par_id:
            par_id[cle] = v
            ordre.append(cle)
        elif rang(v) > rang(par_id[cle]):
            par_id[cle] = v

    return [par_id[c] for c in ordre]


def charger(chemin):
    texte = pathlib.Path(chemin).read_text(encoding="utf-8")
    d = json.loads(texte)
    return d if isinstance(d, list) else []


def main():
    if len(sys.argv) != 4:
        sys.exit("usage: fusionner_file.py <sortie> <notre.json> <leur.json>")
    sortie, notre, leur = sys.argv[1:4]
    try:
        a, b = charger(notre), charger(leur)
    except ValueError as e:
        # Un des deux cotes n'est pas du JSON exploitable (fichier a moitie
        # ecrit, marqueurs de conflit) : on ne devine pas, on laisse
        # enregistrer.sh retomber sur sa strategie de repli.
        print("fusion impossible (%s)" % str(e)[:80], file=sys.stderr)
        return 2

    f = fusionner(a, b)
    pathlib.Path(sortie).write_text(
        json.dumps(f, indent=2, ensure_ascii=False), encoding="utf-8")

    change = sum(1 for x, y in zip(a, f) if x is not y)
    print("fusion %s : %d entrees (%d des notres, %d des leurs, %d arbitrees)"
          % (pathlib.Path(sortie).name, len(f), len(a), len(b), change))
    return 0


if __name__ == "__main__":
    sys.exit(main())
