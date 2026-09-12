#!/usr/bin/env python3
"""Temoins de `fusionner_file.py`. Lancer : python3 scripts/test_fusion.py"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from fusionner_file import fusionner  # noqa: E402

CAS = []


def cas(nom):
    def deco(f):
        CAS.append((nom, f))
        return f
    return deco


@cas("une video publiee a la main ne redevient jamais pending")
def _():
    # Le cas reel du 2026-09-09 : 56-onepotpates marquee published, un
    # workflow parti avant la poussee la croit encore pending.
    notre = [{"id": "56", "status": "pending"}]
    leur = [{"id": "56", "status": "published"}]
    return fusionner(notre, leur)[0]["status"] == "published"


@cas("un depot confirme l'emporte sur un pending perime")
def _():
    return fusionner([{"id": "a", "status": "pending"}],
                     [{"id": "a", "status": "scheduled"}])[0]["status"] == "scheduled"


@cas("une mise en attente deliberee n'est pas levee par un pending")
def _():
    return fusionner([{"id": "a", "status": "pending"}],
                     [{"id": "a", "status": "on_hold"}])[0]["status"] == "on_hold"


@cas("a egalite, notre version gagne")
def _():
    f = fusionner([{"id": "a", "status": "scheduled", "postId": "neuf"}],
                  [{"id": "a", "status": "scheduled", "postId": "vieux"}])
    return f[0]["postId"] == "neuf"


@cas("une entree presente d'un seul cote est conservee")
def _():
    f = fusionner([{"id": "a", "status": "pending"}],
                  [{"id": "b", "status": "pending"}])
    return {v["id"] for v in f} == {"a", "b"}


@cas("l'ordre de notre file est preserve")
def _():
    f = fusionner([{"id": "a"}, {"id": "b"}, {"id": "c"}], [{"id": "b"}])
    return [v["id"] for v in f] == ["a", "b", "c"]


@cas("une entree sans identifiant est ignoree, pas fatale")
def _():
    return len(fusionner([{"status": "pending"}, {"id": "a"}], [])) == 1


# --- Fraicheur : trois cas reels du 2026-09-12 -----------------------------

@cas("une remise en file datee bat une mise en attente plus ancienne")
def _():
    # 72-roulespoulet : revoicee, remise en file, puis deposee. Le workflow
    # d'a cote portait encore son vieux on_hold et l'a fait gagner sur le rang.
    notre = [{"id": "72", "status": "scheduled", "maj": "2026-09-12T15:10:00Z"}]
    leur = [{"id": "72", "status": "on_hold"}]
    return fusionner(notre, leur)[0]["status"] == "scheduled"


@cas("une correction datee bat un published errone")
def _():
    # 60-patescrevettes : marquee published alors que c'est 71-tacos qui etait
    # sortie. La correction doit tenir, meme si published est le rang le plus haut.
    notre = [{"id": "60", "status": "on_hold", "maj": "2026-09-12T15:10:00Z"}]
    leur = [{"id": "60", "status": "published"}]
    return fusionner(notre, leur)[0]["status"] == "on_hold"


@cas("entre deux dates, la plus recente gagne - meme si elle est moins avancee")
def _():
    notre = [{"id": "a", "status": "published", "maj": "2026-09-12T08:00:00Z"}]
    leur = [{"id": "a", "status": "pending", "maj": "2026-09-12T15:00:00Z"}]
    return fusionner(notre, leur)[0]["status"] == "pending"


@cas("sans date des deux cotes, l'ancienne regle tient (temoin 56-onepotpates)")
def _():
    # 2026-09-09 : publiee A LA MAIN, un workflow parti avant la poussee l'a
    # remise a pending. Aucune des deux entrees n'est datee : le rang tranche.
    notre = [{"id": "56", "status": "published"}]
    leur = [{"id": "56", "status": "pending"}]
    return fusionner(notre, leur)[0]["status"] == "published"


@cas("un seul cote date : c'est lui qui a decide")
def _():
    notre = [{"id": "a", "status": "published"}]
    leur = [{"id": "a", "status": "on_hold", "maj": "2026-09-12T15:00:00Z"}]
    return fusionner(notre, leur)[0]["status"] == "on_hold"


def main():
    rates = 0
    for nom, f in CAS:
        try:
            ok = f()
        except Exception as e:
            ok = False
            nom += "  (exception : %s)" % str(e)[:60]
        print("  %s  %s" % ("OK  " if ok else "RATE", nom))
        rates += not ok
    print("\n%d/%d temoins passent" % (len(CAS) - rates, len(CAS)))
    return 1 if rates else 0


if __name__ == "__main__":
    sys.exit(main())
