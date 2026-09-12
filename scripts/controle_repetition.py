#!/usr/bin/env python3
"""Refuse de deposer une video dont le PLAT a deja ete publie sur la chaine.

POURQUOI
--------
Le 2026-09-09, l'utilisateur a lui-meme arrete deux videos au moment de les
publier a la main : `53-pouletfrit` rejouait le poulet frit du 23/08 et les
ailes de poulet du 29/08, `54-pizzathon` rejouait le thon en boite des 30 et
31/08. Aucun controle du pipeline ne regardait ca : le montage compare un
nouveau script aux AUTRES SCRIPTS, jamais aux legendes DEJA EN LIGNE.

Le defaut est ancien et il est deja passe en production sans que personne ne
le voie. Mesure du 2026-09-10 sur les 30 videos publiees de love_kitchen,
435 paires :

    mediane                                          0,00
    plats distincts                             0,00 a 0,14
    gratin de thon 30/08 vs 31/08                    1,00   <- deux fois
    gratin de pommes de terre 24/08 vs 28/08          0,43
    gateau sans cuisson 24/08 vs 06/09                0,38
    salade 31/08 vs 01/09                             0,33

La meme video de gratin de thon est sortie DEUX JOURS DE SUITE.

CE QU'ON COMPARE, ET CE QU'ON NE COMPARE PAS
--------------------------------------------
Premiere version du detecteur : comparaison des legendes entieres. Les six
videos en file sortaient toutes "doublon" a 0,23 et plus. Normal - le
tutoiement, la formule "Mon mari m'avait dit" et les hashtags generiques
(#cuisinemaison, #pourtoi, #storytime, #couplegoals) SONT le format, celui
qui a produit la video a 252 432 vues. Un controle qui refuse le format ne
protege de rien : il finit desactive (regle de methode n.2).

On ne garde donc que la signature du PLAT : les hashtags specifiques et les
mots pleins de la legende, formule narrative retiree.

Le score est volontairement ASYMETRIQUE - part de la nouvelle signature deja
couverte par une ancienne. La question est "ce plat est-il deja sorti ?", pas
"les deux legendes se ressemblent-elles ?".

SEUIL
-----
0,30. Il separe avec marge : plats distincts a 0,14 au plus, vraies
repetitions a 0,33 au moins.

Usage :
    python scripts/controle_repetition.py                 # etat de toutes les files
    python scripts/controle_repetition.py --chaine lovekitchen
"""

import argparse
import json
import os
import pathlib
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request

ICI = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ICI))

SEUIL = 0.30
API = "https://zernio.com/api/v1"

# Ce controle ne vaut que la ou la LEGENDE NOMME LE SUJET. C'est le cas des
# deux chaines de cuisine : la legende y annonce le plat, et deux fois le meme
# plat est une repetition.
#
# Il ne vaut PAS sur toprank : ses legendes sont volontairement generiques et
# reprises telles quelles d'une video a l'autre ("It went wrong immediately"),
# si bien que deux videos differentes sortent a 1,00. Ce serait un controle
# qui refuse tout, donc un controle qu'on finit par desactiver (regle de
# methode n.2). toprank est deja protege par l'empreinte perceptuelle.
# mindshift, argile et pisciniste racontent une histoire : meme raison.
CONCERNEES = {"lovekitchen", "recipecrave"}

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

# Hashtags qui decrivent le FORMAT, pas le plat.
GENERIQUES = {
    "cuisinemaison", "storytime", "couplegoals", "pourtoi", "foryou", "fyp",
    "recettefacile", "recette", "cuisine", "dessertfacile", "patisseriemaison",
    "food", "asmr", "faitmaison", "gourmandise", "recettes", "tiktokfood",
    "vendredisoir", "mardisoir", "lundisoir", "samedisoir", "dimanchesoir",
    "motivation", "mindset", "inspiration", "fail", "fails", "humour", "drole",
    # Categories, pas des plats : elles collent a tout le catalogue.
    "patisserie", "anniversaire", "dessert", "gouter", "diner", "repas",
    "petitdejeuner", "brunch", "aperitif", "entree", "platprincipal",
    "healthy", "comfortfood", "foodtok", "recetterapide", "platrapide",
    # Ajoutes le 2026-09-12 : sur recipe_crave la signature est faite QUE de
    # hashtags (la legende n'a pas de recit), donc un generique oublie entre
    # dans TOUTES les signatures et gonfle chaque comparaison. `foodtiktok`
    # etait present sur les cinq videos preparees ce jour-la et les faisait
    # toutes se ressembler.
    "foodtiktok", "reperapide", "repasrapide", "cuisinerapide", "miam",
    "recetteminute", "foodporn", "yummy", "delicious", "trending",
}

# Mots qui ne designent pas un aliment mais qui COLLENT a un nom de plat dans
# un hashtag compose : `#briochemaison` contenait `mais` - du mais - et le
# faisait ressembler a toute recette de mais. On les retire du hashtag AVANT
# de chercher les mots du lexique dedans.
PARASITES = ("maison", "facile", "rapide", "express", "minute", "healthy",
             "veggie", "express")

# LEXIQUE DES PLATS. Le corps d'une legende contient surtout du recit ; y
# prendre "tout mot de plus de trois lettres" faisait entrer `etait`,
# `reserve`, `patissiers` dans la signature. Le 2026-09-10, le gateau
# Esterhazy a ete refuse a 0,30 pile contre un CHEESECAKE, sur les seuls mots
# communs `gateau` et `etait`. Une signature de plat ne se compose que de
# mots de plat.
LEXIQUE = set("""
gratin tarte gateau roule roules brioche brioches cheesecake muffin muffins
cookie cookies glace pizza tortilla tortillas salade soupe pates spaghetti
lasagne risotto riz orzotto quiche cake crepe crepes pancake pancakes gaufre
beignet donut tiramisu millefeuille napoleon feuillete feuilletee chausson
poulet ailes tenders escalope dinde canard boeuf steak viande hachee bacon
lardons jambon saucisse thon saumon poisson cabillaud crevette crevettes
gambas moules oeuf oeufs omelette
courgette courgettes aubergine aubergines tomate tomates poivron poivrons
carotte carottes oignon oignons champignon champignons epinard epinards
pomme pommes patate patates dauphinois puree concombre avocat poischiche
haricot brocoli chou mais betterave
fraise fraises framboise myrtille banane citron orange peche peches pomme
ananas mangue cerise
chocolat nutella cannelle vanille caramel amande amandes noisette pistache
praline miel sirop
fromage mozzarella parmesan feta cheddar ricotta mascarpone creme beurre
lait yaourt
pain pate panure friture frit frite sanscuisson onepot etages
""".split())

# Le squelette narratif du format love_kitchen. Commun a toutes les videos.
FORMULE = set("""mon mari belle mere soeur avait dit disait croyait jure jamais
plus rien etait sont est une des les leur nous vous cetait cest quil quelle
mavait quon fait faire cuit cuire mange mangeait toujours bien tres trop peu
deja encore comme tout tous toute toutes donc mais alors quand parce
""".split())


def _sans_accents(t):
    return "".join(c for c in unicodedata.normalize("NFD", t.lower())
                   if unicodedata.category(c) != "Mn")


def _texte(x):
    return x if isinstance(x, str) else ""


def _singulier(m):
    """`fraises` et `fraise` doivent etre le meme mot."""
    return m[:-1] if m.endswith("s") and m[:-1] in LEXIQUE else m


def _decomposer(hashtag):
    """`pouletfrit` -> {poulet, frit}. Sinon le hashtag tel quel.

    Sans ca, `#pouletfrit` et `#poulet` sont deux jetons sans rapport : le
    2026-09-10, 53-pouletfrit sortait a 0,25 contre le poulet frit du 23/08
    qu'il rejoue mot pour mot.
    """
    net = hashtag
    for p in PARASITES:
        net = net.replace(p, "")
    trouves = {m for m in LEXIQUE if len(m) > 3 and m in net}
    # On retire les mots contenus dans un autre mot trouve (pomme dans pommes)
    trouves = {m for m in trouves
               if not any(m != a and m in a for a in trouves)}
    return {_singulier(m) for m in trouves} or ({hashtag} if net else set())


def signature_plat(legende):
    """Ce qui identifie le plat : hashtags specifiques + mots pleins."""
    legende = _texte(legende)
    hashtags = {h.lower() for h in re.findall(r"#(\w+)", legende)} - GENERIQUES

    corps = re.split(r"#", legende, 1)[0].lower()
    corps = _sans_accents(corps)
    corps = re.sub(r"[^a-z' ]", " ", corps)
    mots = set()
    for m in corps.split():
        # Elision : sans ca, "m'avait" et "qu'un" survivent au filtre.
        m = re.sub(r"^(qu|[ldnscjmt])'", "", m.strip("'"))
        # Seuls les mots de plat comptent - voir LEXIQUE.
        if m in LEXIQUE:
            mots.add(_singulier(m))
    s = set(mots)
    for h in hashtags:
        s |= _decomposer(_sans_accents(h))
    return s


def recouvrement(neuve, ancienne):
    return len(neuve & ancienne) / len(neuve) if neuve else 0.0


def _appel(chemin, cle):
    """Reprend sur 429/5xx : l'API en renvoie regulierement (regle n.15)."""
    for essai in range(5):
        r = urllib.request.Request(API + chemin,
                                   headers={"Authorization": "Bearer " + cle})
        try:
            with urllib.request.urlopen(r, timeout=90) as rep:
                return json.loads(rep.read())
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503) or essai == 4:
                raise
            time.sleep(3 * (essai + 1))
    return {}


_CACHE = {}


def legendes_publiees(pseudo, cle):
    """Legendes reellement en ligne sur ce compte, lues chez Zernio.

    On lit /analytics et non /posts : c'est le seul endroit qui reflete ce que
    la CHAINE porte, y compris les videos publiees a la main ou par une autre
    voie que notre pipeline. La file, elle, peut mentir.
    """
    if pseudo in _CACHE:
        return _CACHE[pseudo]
    cid = None
    for a in _appel("/accounts", cle).get("accounts", []):
        if a.get("username") == pseudo:
            cid = a.get("_id")
            break
    if not cid:
        _CACHE[pseudo] = []
        return []
    posts = _appel("/analytics?accountId=%s&limit=100" % cid, cle).get("posts", [])
    _CACHE[pseudo] = [(str(p.get("publishedAt"))[:10], _texte(p.get("content")))
                      for p in posts]
    return _CACHE[pseudo]


def deja_publie(legende, publiees):
    """(score, date, legende) du plat publie le plus proche."""
    s = signature_plat(legende)
    if not s:
        return 0.0, "", ""
    meilleur = (0.0, "", "")
    for date, contenu in publiees:
        r = recouvrement(s, signature_plat(contenu))
        if r > meilleur[0]:
            meilleur = (r, date, contenu)
    return meilleur


def legendes_en_file(chemin, sauf_id=None):
    """[(date, legende)] des videos DEJA EN FILE mais pas encore publiees.

    Ajoute le 2026-09-12. Le controle ne regardait que les legendes EN LIGNE :
    une source d'escalope panee est passee a 0,25 alors que `63-escalopepanee`
    etait deja programmee pour le soir meme. Le meme plat serait sorti deux
    fois a quelques jours d'ecart - exactement le defaut du gratin de thon des
    30 et 31/08, deplace de "deja publie" vers "deja prevu".
    """
    import json as _json
    try:
        file = _json.load(open(chemin, encoding="utf-8"))
    except Exception:
        return []
    sortie = []
    for e in file:
        if e.get("status") in ("published", "rejected"):
            continue           # publiee : elle est deja dans le releve Zernio
        if sauf_id and e.get("id") == sauf_id:
            continue           # ne pas se comparer a soi-meme
        leg = e.get("caption") or ""
        if leg:
            sortie.append((e.get("scheduledFor", "") or "en file", leg))
    return sortie


def verdict(video, publiees, chaine=None):
    """Motif de refus, ou None. `video` est une entree de file."""
    if chaine is not None and chaine not in CONCERNEES:
        return None
    score, date, contenu = deja_publie(video.get("caption") or "", publiees)
    if score < SEUIL:
        return None
    return ("plat deja publie le %s (recouvrement %.2f) : %s"
            % (date, score, contenu.split("\n")[0][:70]))


def main():
    import programmer_avance as pa

    ap = argparse.ArgumentParser()
    ap.add_argument("--chaine", help="n'examiner qu'une chaine")
    ap.add_argument("--seuil", type=float, default=SEUIL)
    a = ap.parse_args()

    pa.precharger_cles()
    depot = ICI.parent
    total = doublons = 0

    for nom, fichier, pseudo, numero, quota in pa.CHAINES:
        if a.chaine and nom != a.chaine:
            continue
        chemin = depot / fichier
        if not chemin.exists():
            continue
        try:
            publiees = legendes_publiees(pseudo, pa.cle_zernio(numero))
        except Exception as e:
            print("%-12s lecture Zernio impossible : %s" % (nom, str(e)[:90]))
            continue
        queue = json.loads(chemin.read_text(encoding="utf-8"))
        attente = [v for v in queue if v.get("status") in ("pending", "scheduled")]
        print("\n=== %-12s %d publiees, %d en file" % (nom, len(publiees), len(attente)))
        for v in attente:
            total += 1
            score, date, contenu = deja_publie(v.get("caption") or "", publiees)
            actif = nom in CONCERNEES
            if not actif:
                marque = "  (chaine non concernee)"
            else:
                marque = "  <-- DOUBLON" if score >= a.seuil else ""
            if actif and score >= a.seuil:
                doublons += 1
            print("  %-28s %.2f  vs %s %s%s"
                  % (v.get("id", "?"), score, date or "-",
                     contenu.split("\n")[0][:44], marque))

    print("\n%d video(s) en file, %d rejouent un plat deja publie (seuil %.2f)"
          % (total, doublons, a.seuil))
    return 0


if __name__ == "__main__":
    sys.exit(main())
