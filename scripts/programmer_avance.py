#!/usr/bin/env python3
"""Depose la file d'attente chez Zernio en posts PROGRAMMES, etales dans le temps.

Pourquoi ce script existe
-------------------------
Jusqu'ici chaque creneau cron appelait `publish_next.py`, qui publiait
IMMEDIATEMENT (`publishNow: true`) et tentait jusqu'a trois candidats d'affilee
en cas de refus. Le 2026-09-06, 25 tentatives consecutives ont ete refusees par

    TikTok direct posting is at capacity right now.

Mesure sur les 300 dernieres tentatives, en ne retenant que celles qui n'avaient
AUCUNE autre tentative de notre part dans les 30 minutes precedentes :

    tentatives ISOLEES : 210 reussites / 215  (98 %)
    toutes tentatives  : 260 reussites / 300  (87 %), et 48 % a 17h UTC

Le taux des tentatives isolees est de 90 a 100 % A TOUTE HEURE DU JOUR. Ce n'est
donc pas l'heure qui fait echouer une publication : c'est le fait d'en tenter
plusieurs coup sur coup. Nos propres rafales - trois candidats par run, six
workflows qui tombent dans le meme quart d'heure, plus le relanceur - se
refusent mutuellement. Cela corrige la regle 6 bis, qui disait deja que la
cause n'etait pas l'heure sans pouvoir le demontrer.

Ce que fait ce script
---------------------
Il construit UNE grille horaire commune a toutes les chaines, avec un ecart fixe
entre deux creneaux (ESPACEMENT_MIN), et y depose chaque video en attente avec
un `scheduledFor`. Zernio garde le post et sollicite TikTok a l'heure dite. Plus
rien n'est publie dans la minute ou tourne un cron, et deux videos ne peuvent
plus partir a trois minutes d'intervalle.

Effets de bord voulus :
- les creneaux GitHub manques (les evenements `schedule` sont massivement
  retardes ou perdus) ne coutent plus une sortie : le post est deja depose ;
- une chaine sans stock ne bloque pas les autres, son creneau passe a la
  suivante.

Ce qu'il NE fait pas
--------------------
Il ne contourne pas la saturation : un post programme peut encore etre refuse a
son declenchement. C'est `relancer_saturation.py` qui rattrape ce cas, et
`reconcilier_programmes.py` qui remet la video en file si le rattrapage renonce.

Toutes les barrieres de `publish_next.py` sont reutilisees telles quelles
(specs du fichier, controle visuel, TAMPON sha256, choix de vignette) : ce
script ne cree pas un second chemin de publication moins surveille.
"""

import argparse
import collections
import json
import os
import pathlib
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import publish_next as pn

ICI = pathlib.Path(__file__).resolve().parent.parent
TZ = "Europe/Paris"
FUSEAU = ZoneInfo(TZ)

# `scheduledFor` part SANS decalage et Zernio le lit dans le fuseau annonce par
# le champ `timezone`. Y ecrire une heure UTC la recule de deux heures en ete :
# la programmation tombe dans le passe et Zernio publie aussitot. C'est ce qui a
# sorti trois love_kitchen dans la meme minute le 2026-09-06.
FORMAT = "%Y-%m-%dT%H:%M:%S"

# Ecart entre deux creneaux, toutes chaines confondues. La mesure ci-dessus
# porte sur une fenetre de 30 minutes ; 80 minutes laissent en plus de la place
# aux relances du rattrapage, qui sollicitent le meme quota.
ESPACEMENT_MIN = 80

# Premier creneau du jour, heure de Paris.
DEBUT = (0, 20)

# Chaines servies, dans l'ordre de rotation.
# (nom, fichier de file, pseudo TikTok, numero de cle Zernio, creneaux par jour)
# love_kitchen passe en tete : consigne permanente de l'utilisateur.
CHAINES = [
    ("lovekitchen", "queue-lovekitchen.json", "love_kitchen97", 2, 3),
    ("recipecrave", "queue-recipecrave.json", "recipe_crave", 1, 4),
    ("toprank", "queue-toprank.json", "toprank.tv1", 1, 4),
    ("mindshift", "queue-mindshift.json", "mindshift716", 2, 3),
    ("argile", "queue-argile.json", "argile.histoires", 4, 2),
    ("pisciniste", "queue-pisciniste.json", "le.pisciniste7", 5, 2),
]


_CLES = {}


def cle_zernio(numero):
    """Cle de la chaine : variable d'environnement d'abord, fichier ensuite.

    Le resultat est MEMORISE. `publish_next.zernio_call` lit la cle dans
    `ZERNIO_API_KEY`, que ce script reecrit a chaque chaine ; sans memorisation,
    l'appel suivant pour la cle 1 relisait cette meme variable et recuperait
    la cle de la chaine precedente. Symptome observe : "Compte TikTok
    'recipe_crave' introuvable dans Zernio" alors que la cle 1 est la sienne.
    """
    if numero in _CLES:
        return _CLES[numero]
    nom = "ZERNIO_API_KEY" if numero == 1 else "ZERNIO_API_KEY_%d" % numero
    v = os.environ.get(nom)
    if v:
        _CLES[numero] = v
        return v
    for base in (ICI, ICI.parent / "Project 1 TIKTOK"):
        f = base / ("zernio.env" if numero == 1 else "zernio%d.env" % numero)
        if f.exists():
            for l in f.read_text(encoding="utf-8").splitlines():
                if "=" in l and "KEY" in l.split("=")[0].upper():
                    _CLES[numero] = l.split("=", 1)[1].strip()
                    return _CLES[numero]
    raise RuntimeError("cle Zernio %d introuvable" % numero)


def cle_gemini_locale():
    """Sert uniquement au choix de la vignette.

    Sur un runner GitHub la variable vient du secret ; en local elle n'existe
    pas, et sans elle toutes les vignettes retombent a 2 s - c'est-a-dire sur
    un plan d'intro sans interet, precisement ce que le choix automatique
    avait corrige. On va donc la chercher dans le fichier du projet.
    """
    if os.environ.get("GEMINI_API_KEY"):
        return
    for base in (ICI, ICI.parent / "Project 1 TIKTOK"):
        for f in (base / "gemini.env", base / "channels" / "beauty" / ".env"):
            if f.exists():
                for l in f.read_text(encoding="utf-8").splitlines():
                    if l.strip().startswith("GEMINI_API_KEY="):
                        os.environ["GEMINI_API_KEY"] = l.split("=", 1)[1].strip()
                        return


def precharger_cles():
    """Resout TOUTES les cles avant que quiconque ne touche a ZERNIO_API_KEY.

    A APPELER EN PREMIER dans tout script qui reecrit cette variable pour
    changer de compte. Sans ca, la resolution de la cle 1 relit
    `ZERNIO_API_KEY` - qui contient alors la cle de la chaine precedente - et
    renvoie la mauvaise. Le 2026-09-06, `reconcilier_programmes.py` a ainsi lu
    les posts de recipe_crave et toprank avec la cle de love_kitchen : il n'a
    trouve aucun post pour leurs videos et a remis 26 entrees correctement
    programmees en `pending`.
    """
    for _, _, _, numero, _ in CHAINES:
        cle_zernio(numero)


def grille(depart, jours):
    """Creneaux successifs, en heure de Paris, espaces de ESPACEMENT_MIN.

    24 h font exactement 18 pas de 80 minutes : la grille se repete donc a
    l'identique chaque jour, et deux executions successives ne peuvent pas se
    decaler l'une par rapport a l'autre.

    L'addition d'un timedelta a une heure locale est une arithmetique de
    pendule : au passage a l'heure d'hiver (dernier dimanche d'octobre) un
    creneau peut glisser d'une heure. Sans consequence ici - l'horizon est de
    14 jours et les creneaux ne visent aucune heure precise - mais a savoir si
    la grille devient un jour un calendrier editorial.
    """
    t = depart.replace(hour=DEBUT[0], minute=DEBUT[1], second=0, microsecond=0)
    fin = depart + timedelta(days=jours)
    while t < fin:
        if t > depart:
            yield t
        t += timedelta(minutes=ESPACEMENT_MIN)


def rotation():
    """Ordre de passage des chaines, une entree par creneau et par jour.

    On intercale les chaines au lieu de les grouper : deux videos de la meme
    chaine ne doivent jamais se suivre, ni pour TikTok qui lit la cadence, ni
    pour l'abonne qui voit le compte.
    """
    restant = {c[0]: c[4] for c in CHAINES}
    ordre = []
    while any(restant.values()):
        for nom, _, _, _, _ in CHAINES:
            if restant[nom]:
                ordre.append(nom)
                restant[nom] -= 1
    return ordre


def eligible(v, ecrire):
    """Rejoue les barrieres de publish_next avant de deposer quoi que ce soit."""
    ok, detail, repare = pn.verifier_et_reparer(v)
    if repare:
        v["note"] = "Fichier repare automatiquement (%s)." % detail
        ecrire()
    if not ok:
        v["status"] = "failed"
        v["error"] = "Non conforme aux specs TikTok : %s" % detail
        ecrire()
        return "specs : %s" % detail
    if not v.get("controle_visuel") and pn.MARQUE_CONTROLE not in str(v.get("note", "")):
        v["status"] = "on_hold"
        v["error"] = "jamais passe par le controle visuel : publication refusee"
        ecrire()
        return "pas de controle visuel"
    detail = pn.verifier_tampon(v)
    if detail:
        v["status"] = "on_hold"
        v["error"] = detail
        ecrire()
        return detail
    return None


def deposer(v, cid, quand_paris):
    """Cree le post programme. Renvoie (identifiant, vignette_choisie).

    `vignette_choisie` est faux quand le choix automatique n'a pas abouti - le
    plus souvent parce que le quota Gemini du jour est epuise. La video part
    alors avec la vignette par defaut a 2 s, c'est-a-dire un plan d'intro. On
    le NOTE dans la file : `corriger_vignettes.py` repassera le lendemain, quota
    reinitialise, refaire la couverture avant la date de sortie. Sans cette
    trace l'information serait perdue - Zernio ne renvoie pas `tiktokSettings`
    dans ses reponses, la vignette d'un post depose est illisible apres coup.
    """
    chemin = pn.chemin_local(v["url"])
    cover = pn.VIGNETTE_DEFAUT_MS
    if chemin and os.path.exists(chemin):
        s = pn.specs_video(chemin)
        if s and s.get("duree"):
            cover = pn.choisir_vignette_ms(chemin, s["duree"])
    payload = {
        "content": v["caption"],
        "scheduledFor": quand_paris.strftime(FORMAT),
        "timezone": TZ,
        "platforms": [{"platform": "tiktok", "accountId": cid}],
        "mediaItems": [{"type": "video", "url": v["url"]}],
        "tiktokSettings": {
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "allow_comment": True,
            "allow_duet": True,
            "allow_stitch": True,
            "video_cover_timestamp_ms": cover,
            "content_preview_confirmed": True,
            "express_consent_given": True,
        },
    }
    r = pn.zernio_call("POST", "/posts", payload)
    return (r.get("post") or r).get("_id"), cover != pn.VIGNETTE_DEFAUT_MS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jours", type=int, default=14)
    ap.add_argument("--max", type=int, default=0, help="plafond global de depots")
    ap.add_argument("--chaine", default="", help="ne traiter que celle-ci")
    ap.add_argument("--essai", action="store_true", help="n'envoie rien")
    # Le choix de vignette appelle Gemini une fois par video. Enchaine sans
    # pause, il se fait limiter et toutes les vignettes retombent a 2 s -
    # c'est-a-dire sur un plan d'intro, ce que ce choix automatique existe
    # justement pour eviter. Dix secondes tiennent le rythme sous la limite.
    ap.add_argument("--pause", type=int, default=10, help="secondes entre 2 depots")
    a = ap.parse_args()

    precharger_cles()
    cle_gemini_locale()
    maintenant = datetime.now(timezone.utc).astimezone(FUSEAU)
    conf = {c[0]: c for c in CHAINES}
    files, ecrivains, restes = {}, {}, {}

    for nom, fichier, pseudo, numero, quota in CHAINES:
        chemin = ICI / fichier
        q = json.loads(chemin.read_text(encoding="utf-8"))
        files[nom] = q

        def fabrique(ch=chemin, qq=q):
            def ecrire():
                ch.write_text(json.dumps(qq, indent=2, ensure_ascii=False),
                              encoding="utf-8")
            return ecrire
        ecrivains[nom] = fabrique()
        restes[nom] = [v for v in q if v.get("status") == "pending"]

    # Les creneaux deja occupes par un depot precedent ne sont pas reutilises :
    # ce script doit pouvoir tourner tous les jours pour prolonger l'horizon
    # sans empiler deux videos sur la meme minute.
    occupes = set()
    for q in files.values():
        for v in q:
            if v.get("status") != "scheduled":
                continue
            if v.get("scheduledFor"):
                occupes.add(v["scheduledFor"])
            elif v.get("scheduledForUtc"):
                # Entree ecrite par le reconciliateur avant qu'il ne note aussi
                # l'heure locale : on la reconstitue, sans quoi le creneau
                # passerait pour libre et recevrait une deuxieme video.
                occupes.add(datetime
                            .fromisoformat(v["scheduledForUtc"].replace("Z", "+00:00"))
                            .astimezone(FUSEAU).strftime(FORMAT))

    ordre = rotation()
    pris = collections.defaultdict(int)
    # Ce qui est deja depose compte dans le quota du jour : sans ca, une
    # deuxieme execution rajouterait un quota complet par-dessus le premier.
    for nom, q in files.items():
        for v in q:
            if v.get("status") == "scheduled" and v.get("scheduledFor"):
                pris[(datetime.strptime(v["scheduledFor"], FORMAT).date(), nom)] += 1
    deposes, i = 0, 0
    cids = {}

    print("Grille : 1 creneau / %d min a partir de %s (%s)\n"
          % (ESPACEMENT_MIN, maintenant.strftime("%d/%m %H:%M"), TZ))

    for quand in grille(maintenant, a.jours):
        if a.max and deposes >= a.max:
            break
        if quand.strftime(FORMAT) in occupes:
            continue
        # Chaine du creneau : on avance dans la rotation jusqu'a en trouver une
        # qui ait encore du stock ET qui n'ait pas atteint son quota du jour.
        #
        # Le quota journalier n'est pas decoratif. Sans lui, des que les autres
        # chaines sont a sec la derniere prend TOUS les creneaux restants : le
        # premier depot a place 14 recipe_crave d'affilee sur la meme journee,
        # soit une publication toutes les 80 minutes pendant 19 heures sur un
        # seul compte. C'est un rythme que TikTok lit comme du spam, et ca vide
        # en deux jours un stock prevu pour cinq.
        jour = quand.date()
        choisie = None
        for _ in range(len(ordre)):
            cand = ordre[i % len(ordre)]
            i += 1
            if a.chaine and cand != a.chaine:
                continue
            if not restes.get(cand):
                continue
            if pris[(jour, cand)] >= conf[cand][4]:
                continue
            choisie = cand
            break
        if choisie is None:
            # Aucune chaine eligible a cette heure-ci : le creneau reste vide,
            # on ne force pas. S'il ne reste plus de stock nulle part, on sort.
            if not any(restes.values()):
                break
            continue

        v = restes[choisie].pop(0)
        pris[(jour, choisie)] += 1
        _, fichier, pseudo, numero, _ = conf[choisie]
        motif = eligible(v, ecrivains[choisie])
        if motif:
            print("  %-12s %-28s ECARTE : %s" % (choisie, v["id"], motif))
            continue

        quand_utc = quand.astimezone(timezone.utc)
        if a.essai:
            print("  %s  %-12s %-28s (%s UTC)"
                  % (quand.strftime("%d/%m %H:%M"), choisie, v["id"],
                     quand_utc.strftime("%d/%m %H:%M")))
            deposes += 1
            continue

        os.environ["ZERNIO_API_KEY"] = cle_zernio(numero)
        if choisie not in cids:
            cids[choisie] = pn.compte_id(pseudo)
        try:
            pid, vignette = deposer(v, cids[choisie], quand)
        except Exception as e:
            # 409 : un post en ECHEC portant la meme video traine encore chez
            # Zernio et rend la video indeposable. C'est le cas normal apres
            # une soiree de saturation. On degage, puis on retente une fois.
            if "409" in str(e) or "already scheduled" in str(e).lower():
                pn.purger_posts_bloquants(v["url"])
                try:
                    pid, vignette = deposer(v, cids[choisie], quand)
                except Exception as e2:
                    print("  %-12s %-28s ECHEC APRES PURGE : %s"
                          % (choisie, v["id"], str(e2)[:110]))
                    restes[choisie].insert(0, v)
                    continue
            else:
                print("  %-12s %-28s ECHEC DEPOT : %s" % (choisie, v["id"], str(e)[:130]))
                restes[choisie].insert(0, v)
                break
        v["status"] = "scheduled"
        v["postId"] = pid
        v["vignette_auto"] = vignette
        v["scheduledFor"] = quand.strftime(FORMAT)
        v["scheduledForUtc"] = quand_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        v.pop("error", None)
        ecrivains[choisie]()
        deposes += 1
        print("  %s  %-12s %-28s -> %s"
              % (quand.strftime("%d/%m %H:%M"), choisie, v["id"], pid))
        if a.pause:
            time.sleep(a.pause)

    print("\n%d video(s) %s" % (deposes, "a deposer" if a.essai else "deposee(s)"))
    for nom in restes:
        if restes[nom]:
            print("  reste %d en attente sur %s" % (len(restes[nom]), nom))


if __name__ == "__main__":
    main()
