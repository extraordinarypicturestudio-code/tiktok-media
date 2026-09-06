#!/usr/bin/env python3
"""Relance les publications que TikTok a refusees pour cause de SATURATION.

Pourquoi ce script existe
-------------------------
Zernio accepte la programmation, la confirme `scheduled`, puis TikTok refuse au
moment du declenchement avec :

    TikTok direct posting is at capacity right now. Use tiktokSettings.draft:
    true to deliver via Creator Inbox, or try again in a few hours.

La video ne sort jamais et rien ne le signale : le statut racine dit seulement
"failed" et `publishAttempts` vaut 0 a la racine du post. Le motif reel est dans
`platforms[].errorMessage` — PAS `platforms[].error`, qui reste vide. Ce piege a
fait conclure deux fois a tort a "aucun message d'erreur" alors que
l'information etait la.

Deux sorties argile ont ete perdues comme ca (2026-08-31 et 2026-09-02) alors
que le compte etait sain et que les autres chaines publiaient normalement.

Ce que fait ce script
---------------------
Il balaye les cles Zernio, retrouve les posts en echec POUR CE MOTIF UNIQUEMENT,
et les reprogramme plus tard en reutilisant le media deja televerse (pas de
re-upload : le fichier n'existe pas sur un runner GitHub). Lance par cron GitHub
Actions, donc sans machine allumee et sans tache Claude.

Garde-fous
----------
- Seuls les echecs de SATURATION sont repris. Un refus de contenu, une legende
  invalide ou un compte deconnecte doivent rester VISIBLES, pas etre relances en
  boucle : le script les affiche et passe son chemin.
- Au-dela de RELANCES_MAX tentatives, bascule en BROUILLON (Creator Inbox).
  ATTENTION : le brouillon n'est PAS une publication. La video attend dans
  l'application TikTok que l'utilisateur appuie dessus. C'est un dernier recours,
  et le reglage doit donc laisser plusieurs heures d'insistance avant d'y venir.
- Au-dela de AGE_MAX_H, abandon. Republier une video prevue il y a deux jours
  desorganise le calendrier plus qu'elle ne le rattrape.
- Le compteur de relances vit dans `relances_saturation.json`, commite par le
  workflow. Sans lui, chaque execution repartirait de zero et relancerait
  indefiniment.
"""

import json
import os
import pathlib
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

API = "https://zernio.com/api/v1"
TZ = "Europe/Paris"
FUSEAU = ZoneInfo(TZ)      # voir _quand() : le champ scheduledFor est LU dans ce fuseau
ICI = pathlib.Path(__file__).resolve().parent.parent
REGISTRE = ICI / "relances_saturation.json"

# Le mode brouillon N'EST PAS une reussite : la video atterrit dans le Creator
# Inbox et n'est PAS publiee tant que l'utilisateur n'appuie pas dessus dans
# l'application. C'est donc un dernier recours, pas une porte de sortie
# confortable.
#
# Premier reglage (2026-09-04) : 3 tentatives toutes les 45 min, soit 1h30
# d'essais avant de basculer. Beaucoup trop court. Les deux sorties argile du
# 04/09 (18h et 22h) ont epuise leurs tentatives dans la soiree et sont parties
# en brouillon a 01h37 et 06h18 : rien n'est sorti publiquement, et
# l'utilisateur a du decouvrir le lendemain que deux videos l'attendaient dans
# son telephone. L'automatisation avait abandonne au moment ou il fallait
# insister.
#
# Nouveau reglage : 20 minutes entre deux essais et 12 tentatives, soit environ
# 4 HEURES d'insistance avant le brouillon. La saturation TikTok se resorbe
# generalement en quelques heures ; il faut couvrir cette fenetre.
# Une seule reprogrammation par execution, TOUTES CLES CONFONDUES. Le cron
# tourne toutes les 30 minutes : ca suffit a rattraper une file en retard sans
# jamais deposer deux videos coup sur coup sur le meme compte. Le 2026-09-06,
# trois relances love_kitchen sont parties dans la meme minute — la cause
# premiere etait le fuseau (voir `_quand`), mais rien n'empechait la rafale.
PAR_EXECUTION_MAX = 1

RELANCES_MAX = 12       # ~8h d'essais avant de basculer en brouillon
AGE_MAX_H = 36.0        # au-dela : abandon

# 40 minutes, et non 20, depuis le 2026-09-06. Les sorties sont desormais
# deposees a l'avance sur une grille espacee de 80 minutes
# (`programmer_avance.py`), parce que la mesure montre qu'une tentative isolee
# de plus de 30 minutes reussit a 98 % alors qu'une tentative rapprochee echoue
# souvent. Une relance a +20 min retombait a 20 minutes de l'envoi qu'elle
# rattrape : elle reproduisait exactement la rafale qu'on cherche a supprimer.
# A +40 min elle tombe au MILIEU de l'intervalle, a 40 minutes de l'essai
# precedent comme du creneau suivant.
DELAI_MIN = 40          # minutes avant la nouvelle tentative

MOTIF = "at capacity"   # signature du refus de saturation

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")



def _quand(maintenant_utc):
    """Horodatage de la nouvelle tentative, EXPRIME DANS `TZ`.

    Le champ `scheduledFor` part sans decalage (`%Y-%m-%dT%H:%M:%S`) et Zernio
    le lit dans le fuseau donne par `timezone`. Ecrire une heure UTC en la
    declarant "Europe/Paris" la recule donc de deux heures en ete : la
    reprogrammation tombe DANS LE PASSE et Zernio publie immediatement.

    Constate le 2026-09-06 sur love_kitchen : trois relances creees a 11h18 UTC
    portaient toutes `scheduledFor 09:37:57` et sont sorties dans la meme
    minute, au lieu d'etre etalees de 20 minutes. Trois jours de stock brules
    d'un coup sur la meilleure chaine du projet, et un rythme de publication
    que TikTok lit comme du spam.
    """
    return (maintenant_utc.astimezone(FUSEAU)
            + timedelta(minutes=DELAI_MIN)).strftime("%Y-%m-%dT%H:%M:%S")

def cles():
    """Cles Zernio disponibles : variables d'environnement, sinon fichiers locaux."""
    trouvees = []
    noms = ("ZERNIO_API_KEY", "ZERNIO_API_KEY_2", "ZERNIO_API_KEY_3", "ZERNIO_API_KEY_4")
    for i, nom in enumerate(noms, start=1):
        v = os.environ.get(nom)
        if not v:
            f = ICI / ("zernio.env" if i == 1 else "zernio%d.env" % i)
            if f.exists():
                for l in f.read_text(encoding="utf-8").splitlines():
                    if l.startswith("ZERNIO_API_KEY="):
                        v = l.split("=", 1)[1].strip()
        if v:
            trouvees.append((nom, v))
    return trouvees


def appel(methode, url, cle, data=None):
    h = {"Authorization": "Bearer " + cle, "User-Agent": "Mozilla/5.0"}
    if data is not None:
        data = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
        h["x-request-id"] = str(uuid.uuid4())
    req = urllib.request.Request(url, data=data, method=methode, headers=h)
    with urllib.request.urlopen(req, timeout=120) as r:
        corps = r.read()
        return json.loads(corps) if corps else None


def charger():
    if REGISTRE.exists():
        try:
            return json.loads(REGISTRE.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {}


def sature(pf):
    return MOTIF in (pf.get("errorMessage") or pf.get("error") or "")


def main():
    reg = charger()
    now = datetime.now(timezone.utc)
    repris = abandonnes = brouillons = ignores = 0

    for nom, cle in cles():
        if repris + brouillons >= PAR_EXECUTION_MAX:
            break
        try:
            d = appel("GET", API + "/posts?limit=100", cle)
        except Exception as e:
            print("%s : lecture impossible (%s)" % (nom, e))
            continue

        for p in d.get("posts", d.get("data", [])) or []:
            if repris + brouillons >= PAR_EXECUTION_MAX:
                break
            if p.get("status") != "failed":
                continue
            pfs = p.get("platforms") or []
            if not any(sature(pf) for pf in pfs):
                motif = next((pf.get("errorMessage") or pf.get("error") for pf in pfs
                              if pf.get("errorMessage") or pf.get("error")), None)
                if motif:
                    ignores += 1
                    print("  IGNORE (pas une saturation) %s : %s" % (p["_id"], motif[:110]))
                continue

            pid = p["_id"]
            try:
                t0 = datetime.fromisoformat((p.get("scheduledFor") or "").replace("Z", "+00:00"))
            except ValueError:
                t0 = now
            age_h = (now - t0).total_seconds() / 3600
            etat = reg.get(pid, {"relances": 0})

            if age_h > AGE_MAX_H:
                if not etat.get("abandonne"):
                    print("  ABANDON %s : prevue il y a %.0fh, trop vieille pour etre "
                          "republiee sans desorganiser le calendrier" % (pid, age_h))
                etat["abandonne"] = True
                reg[pid] = etat
                abandonnes += 1
                continue

            media = p.get("mediaItems") or []
            pf0 = pfs[0]
            acc = pf0.get("accountId")
            acc_id = acc.get("_id") if isinstance(acc, dict) else acc
            if not media or not acc_id:
                print("  IGNORE %s : media ou compte introuvable dans le post" % pid)
                continue

            brouillon = etat["relances"] >= RELANCES_MAX
            quand = _quand(now)

            corps = {
                "content": p.get("content", ""),
                "mediaItems": [{"type": m.get("type", "video"), "url": m["url"]}
                               for m in media],
                "platforms": [{"platform": pf0.get("platform", "tiktok"),
                               "accountId": acc_id}],
                "tiktokSettings": {
                    "privacy_level": "PUBLIC_TO_EVERYONE", "allow_comment": True,
                    "allow_duet": True, "allow_stitch": True,
                    "content_preview_confirmed": True, "express_consent_given": True},
                "scheduledFor": quand, "timezone": TZ,
            }
            if brouillon:
                # draft + publishNow fait echouer Zernio en HTTP 500 (Cast to
                # date failed) : le mode brouillon EXIGE un scheduledFor.
                corps["tiktokSettings"]["draft"] = True

            try:
                nouveau = appel("POST", API + "/posts", cle, corps)
            except urllib.error.HTTPError as e:
                print("  ECHEC reprogrammation %s : HTTP %s %s"
                      % (pid, e.code, e.read().decode(errors="replace")[:200]))
                continue

            npid = (nouveau.get("post") or nouveau).get("_id")
            try:
                appel("DELETE", "%s/posts/%s" % (API, pid), cle)
            except Exception:
                print("  (l'ancien post %s n'a pas pu etre supprime)" % pid)

            etat["relances"] += 1
            reg.pop(pid, None)
            reg[npid] = etat
            if brouillon:
                brouillons += 1
                print("  BROUILLON %s -> %s pour %s (tentative %d, Creator Inbox)"
                      % (pid, npid, quand, etat["relances"]))
            else:
                repris += 1
                print("  RELANCE %s -> %s pour %s (tentative %d/%d)"
                      % (pid, npid, quand, etat["relances"], RELANCES_MAX))

    REGISTRE.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n%d relancee(s), %d en brouillon, %d abandonnee(s), %d ignoree(s)"
          % (repris, brouillons, abandonnes, ignores))


if __name__ == "__main__":
    main()
