# Pipeline TikTok — regles obligatoires

Ce fichier est lu automatiquement a chaque session. **Tout ce qui est ici est
non negociable** : ce sont des regles que l'utilisateur a deja eu a repeter
plusieurs fois parce qu'elles n'etaient ecrites nulle part.

## Les 3 chaines

| Chaine | Compte TikTok | File | Dossier clips | Rythme |
|---|---|---|---|---|
| TopRankTV | `toprank.tv1` | `queue-toprank.json` | `clips-toprank/` | 4/jour |
| Recipe Crave | `recipe_crave` | `queue-recipecrave.json` | `clips/` | 4/jour |
| NextLevelPlays | `nextlevelplays88` | `queue-nextlevelplays.json` | `clips-nextlevelplays/` | 3/jour |

Depot : `extraordinarypicturestudio-code/tiktok-media` (heberge code, clips,
files, historique ; les cles sont des secrets GitHub Actions). Tout tourne en
cron GitHub Actions, sans machine allumee.

## Chaine de traitement — AUCUNE etape ne se saute

Dans cet ordre, pour chaque clip candidat :

1. **Verification musique** — `tiktok_pipeline.py auto` appelle
   `verifier_musique()` sur l'URL **source** (avant tout telechargement).
   Rejette tout ce qui n'est pas un "son original" attribue au createur
   lui-meme. C'est la seule etape ou les metadonnees TikTok (`track`,
   `artists`) existent encore : apres telechargement elles sont perdues.
2. **OCR / watermark** — meme commande. Rejette les watermarks permanents,
   coupe les legendes temporaires, recadre les logos de coin.
3. **Normalisation 1080x1920** — obligatoire. Le recadrage de logo laisse des
   resolutions batardes (734x1536, 392x818). En dessous de ~576px de large,
   la remise a l'echelle est trop degradee : ecarter le clip.
4. **CONTROLE — `controle_publication.py`** — voir section dediee.
   **ETAPE CRUCIALE, JAMAIS SAUTEE.**
5. **OUTRO**, seulement si le controle passe. **Sur les 3 chaines,
   systematiquement.** Fichiers dans `outros/`.
6. Mise en file (`status: "pending"`), commit, push.

**L'ordre 4 puis 5 n'est pas negociable** : l'outro contient volontairement
un pseudo et du texte. Controler apres l'avoir ajoute ferait refuser 100% des
clips, le controle voyant notre propre marque comme un watermark.

### En pratique : une seule commande

```bash
python preparer_publication.py --chaine recipecrave --dossier lot/PROPRES -o PRETS
```

`preparer_publication.py` enchaine normalisation -> controle -> outro et
n'ecrit dans `PRETS/` que ce qui passe. **L'utiliser plutot que d'enchainer
les etapes a la main** : c'est precisement l'oubli manuel de l'outro (06/08 et
09/08/2026) et du controle (09/08/2026) qui a pose probleme.

## L'etape 5 en detail : `controle_publication.py`

```bash
python controle_publication.py --profil cuisine <video.mp4>
python controle_publication.py --profil sport --dossier clips-toprank/
```

Elle existe parce que **l'OCR de `tiktok_pipeline.py` n'est pas fiable**. Le
2026-08-09, il a declare "propre" un clip (`slide-fail-31`) qui portait un
watermark `@MillaChats` repete sur toute l'image plus la marque WATER CIRCUS.
D'autres clips sont passes avec des sous-titres incrustes complets et des
logos de marques. L'OCR echoue sur : texte semi-transparent, police stylisee,
texte blanc sur fond clair, logo graphique sans lettres nettes.

Un modele de vision voit tout ca. **Il a le dernier mot, pas l'OCR.**

Ce qui bloque, par profil :

| Critere | `cuisine` | `sport` | Pourquoi |
|---|---|---|---|
| watermark (@pseudo, URL, filigrane) | bloque | bloque | copyright + TikTok marque en repost |
| texte incruste (sous-titres, POV, legendes) | bloque | bloque | idem |
| logo de source (media, agregateur, parc, salle) | bloque | bloque | idem |
| cadrage centre sur le corps | bloque | bloque | regle de contenu |
| mineur visible | bloque | bloque | regle de contenu |
| visage identifiable | **bloque** | signale seulement | format "mains uniquement" propre a recipe_crave |
| pas de piste audio / silencieuse | bloque | bloque | sortirait muet sur TikTok |
| resolution != 1080x1920, FPS hors 23-60, duree < 8s, > 100 MB | bloque | bloque | contraintes TikTok + GitHub |

Un logo **de produit** manipule dans la scene (paquet de pates, tablette de
chocolat) ne bloque PAS : c'est un objet filme, pas une signature de source.
Une video de cuisine en montre forcement.

Si le controle visuel echoue techniquement (quota API, reseau), le verdict est
REJET. **Un controle qui n'a pas pu tourner n'autorise pas la publication.**

## Outro

Les fichiers d'outro vivent dans `outros/` a la racine de ce projet, un par
chaine. Ils DOIVENT y rester : entre le 2026-08-06 et le 2026-08-09 ils
n'existaient que dans des sessions Claude successives, jamais sauvegardes, et
ont ete perdus — obligeant l'utilisateur a redemander l'outro a chaque fois.

## Regles de contenu par chaine

- **recipe_crave** : une seule recette complete par video, toutes les etapes,
  **mains uniquement, jamais de visage**. Varier les types de plats — ne pas
  enchainer des semaines de viande/cuisson exterieure comme debut aout 2026.
- **toprank.tv1** : fails/humour. Pas de cadrage sexualise, pas de mineur.
- **nextlevelplays88** : sport **amateur** reellement impressionnant. Exclure
  les images de diffusion professionnelle et le contenu officiel de
  federations (risque de reclamation). Une chute n'est pas un exploit : elle
  va sur toprank.tv1.

## Monetisation

Creator Rewards exige **plus de 60 secondes**. Le noter dans `note` quand un
clip passe ou rate ce seuil.

## Pieges techniques

- Cloner le depot **sans `--depth 1`** : un clone shallow fait echouer le push
  avec un message trompeur (`non-fast-forward`).
- Limite GitHub : **100 MB par fichier**. Un clip de 6 min en CRF 20 sort a
  ~209 MB ; `-crf 30 -maxrate 1500k` le ramene a ~66 MB.
- Les workflows poussent sur le meme depot pendant qu'on travaille. Avant de
  reappliquer des ajouts locaux : `git checkout HEAD -- queue-*.json` puis
  regenerer, sinon on ecrase les statuts `published` qu'ils viennent d'ecrire.
- PowerShell : `Copy-Item` **sans `-LiteralPath`** echoue silencieusement sur
  les fichiers du pipeline (crochets dans le nom interpretes comme jokers).
- Gemini tier gratuit : quota journalier atteignable en un gros lot (erreur
  429). Etaler les controles ou basculer sur un modele payant.

## Taux de rejet a anticiper

Environ **7 candidats sources pour 1 clip publiable** (mesure : 27 valides sur
150). Le filtre musique elimine 70-80% a lui seul. Annoncer cette echelle
AVANT de lancer un gros rechargement, pas apres.

Rendement par niche observe :
- outdoor / montagne / kayak : ~50% (meilleure piste pour nextlevelplays88)
- cuisine maison (dips, salades, plats mijotes) : ~30%
- desserts, boissons, calisthenics, agregateurs de fails : proche de 0%
