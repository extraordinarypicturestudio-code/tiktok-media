#!/usr/bin/env python3
"""Date les entrees de file dont le STATUT vient de changer.

POURQUOI
--------
`fusionner_file.py` arbitre un conflit de file par la fraicheur quand les deux
cotes portent un champ `maj`, et retombe sur l'avancement d'etat sinon. Encore
faut-il que quelqu'un ecrive ce `maj`.

Or trente-six endroits repartis dans dix scripts ecrivent un statut. Les
modifier un par un serait refaire exactement l'erreur du 2026-09-10 : le meme
geste recopie en quatorze exemplaires, dont treize restent armes apres la
correction.

On le pose donc la ou TOUT passe deja : `enregistrer.sh`, seul endroit du
depot ou l'on commite. Juste avant le commit, on compare chaque file a la
version de HEAD et on date les entrees dont le statut a bouge. Peu importe
quel script l'a change, et aucun script n'a a y penser.

Usage (appele par enregistrer.sh) :
    python3 scripts/horodater_file.py queue-lovekitchen.json ...
"""

import datetime
import json
import pathlib
import subprocess
import sys


def ancienne(chemin):
    try:
        # encoding explicite : sous Windows, `text=True` decode en cp1252 et
        # meurt sur le premier accent d'une legende. L'exception etait avalee
        # par le `except` plus bas, `ancienne()` rendait {} et TOUTES les
        # entrees etaient datees - exactement l'inverse du but.
        t = subprocess.run(["git", "show", "HEAD:" + chemin],
                           capture_output=True, encoding="utf-8",
                           errors="replace", check=True).stdout
        d = json.loads(t)
        return {e.get("id") or e.get("url"): e.get("status") for e in d
                if isinstance(e, dict)}
    except Exception:
        return {}          # fichier neuf, ou HEAD illisible : rien a comparer


def main(chemins):
    maintenant = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    total = 0
    for c in chemins:
        p = pathlib.Path(c)
        if not p.exists():
            continue
        try:
            file = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue       # fichier en conflit : la fusion passe avant nous
        if not isinstance(file, list):
            continue
        avant = ancienne(c)
        touchees = 0
        for e in file:
            if not isinstance(e, dict):
                continue
            cle = e.get("id") or e.get("url")
            if cle is None:
                continue
            if cle in avant and avant[cle] == e.get("status"):
                continue   # statut inchange : on ne rajeunit pas sa decision
            e["maj"] = maintenant
            touchees += 1
        if touchees:
            p.write_text(json.dumps(file, ensure_ascii=False, indent=2),
                         encoding="utf-8")
            print("  horodate %s : %d entree(s)" % (p.name, touchees))
            total += touchees
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
