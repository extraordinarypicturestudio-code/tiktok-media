#!/usr/bin/env bash
#
# Enregistre et pousse les files. SEUL endroit du depot ou l'on commite.
#
# Usage :
#   scripts/enregistrer.sh "message de commit" chemin [chemin...]
#
# Exemple :
#   scripts/enregistrer.sh "Mise a jour love_kitchen" \
#       queue-lovekitchen.json used-clips-hashes.json clips-lovekitchen
#
# POURQUOI CE SCRIPT EXISTE
# -------------------------
# Sur les 400 runs du 2026-08-24 au 2026-09-10, les 11 echecs viennent tous
# de l'etape qui commite les files - aucun d'un script Python, de Zernio ou
# d'une video. Ce bloc etait recopie dans quatorze workflows en trois
# variantes ; corriger une copie laissait les autres armees, et chaque
# nouveau workflow reintroduisait le defaut.
#
# Les deux formes d'echec, toutes deux traitees ici :
#
#   A. `git add -- clips-argile/` sur un dossier absent sort en 128, et
#      l'etape tourne sous `bash -e` : le job meurt AVANT le commit, donc le
#      travail du script est perdu et la file se met a mentir. Huit echecs.
#      Les dossiers de clips se vident quand leurs videos sont publiees :
#      c'est l'etat NORMAL, pas une anomalie. -> on ne passe a `git add` que
#      les chemins qui existent.
#
#   B. Un conflit de rebase sur une file JSON laissait le depot en etat
#      "unmerged" ; les quatre tentatives suivantes echouaient toutes sur
#      "Pulling is not possible because you have unmerged files". Trois
#      echecs. -> on resout le conflit en gardant NOTRE version, qui vient
#      d'etre recalculee sur l'etat reel des comptes, la ou celle du depot a
#      une minute de retard. Pendant un rebase, "theirs" designe le commit
#      rejoue : c'est bien le notre.
#
# Deux files JSON ne se fusionnent pas ligne a ligne, et il n'y a rien a
# preserver dans la version distante : `reconcilier_programmes.py` repassera
# de toute facon sur ce que Zernio dit.
#
# Code de sortie : 0 si le depot est a jour (y compris quand il n'y avait
# rien a committer), 1 seulement si cinq tentatives de push ont echoue -
# la, c'est une vraie anomalie et l'email se justifie.

set -u

MESSAGE="${1:-Mise a jour des files [skip ci]}"
shift || true

if [ "$#" -eq 0 ]; then
  echo "::error::enregistrer.sh : aucun chemin donne"
  exit 1
fi

# --- 1. Ne garder que les chemins qui existent -------------------------------
# Un glob non resolu par le shell appelant (`queue-*.json` sans correspondance)
# arrive ici tel quel : il n'existe pas, donc il tombe de lui-meme.
CHEMINS=()
for c in "$@"; do
  if [ -e "$c" ]; then
    CHEMINS+=("$c")
  fi
done

if [ "${#CHEMINS[@]}" -eq 0 ]; then
  echo "enregistrer : aucun des chemins demandes n'existe, rien a faire."
  exit 0
fi

# --- 2. Identite -------------------------------------------------------------
git config user.name  "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

# --- 3. Indexer --------------------------------------------------------------
if ! git add -A -- "${CHEMINS[@]}"; then
  echo "::error::enregistrer : git add a echoue sur ${CHEMINS[*]}"
  exit 1
fi

if git diff --cached --quiet; then
  echo "enregistrer : rien de nouveau, depot deja a jour."
  exit 0
fi

git commit -m "$MESSAGE" || {
  echo "::error::enregistrer : le commit a echoue"
  exit 1
}

# --- 4. Pousser, en resolvant les conflits en notre faveur --------------------
for essai in 1 2 3 4 5; do
  # --autostash est indispensable : un run touche souvent des fichiers hors
  # de la liste demandee (relances_saturation.json ecrit par le relanceur,
  # un clip telecharge...). Sans lui, `git pull --rebase` refuse avec
  # "cannot pull with rebase: You have unstaged changes" et les CINQ essais
  # echouent d'affilee. Constate en test le 2026-09-10 ; c'est le seul point
  # que relancer-saturation.yml faisait bien, et c'est pour ca que ce
  # workflow-la n'a jamais echoue de la fenetre (37 runs, 0 echec).
  if git pull --rebase --autostash origin main && git push; then
    echo "enregistrer : pousse a l'essai $essai."
    exit 0
  fi

  conflits="$(git diff --name-only --diff-filter=U)"
  if [ -n "$conflits" ]; then
    echo "enregistrer : conflit sur $conflits, on garde notre version."
    git checkout --theirs -- $conflits 2>/dev/null || true
    git add -- $conflits
    GIT_EDITOR=true git rebase --continue || git rebase --abort || true
  else
    # Push refuse sans conflit : une autre execution a pousse entre le pull
    # et le push. Sortir d'un eventuel rebase en cours et reessayer.
    git rebase --abort 2>/dev/null || true
  fi

  echo "enregistrer : essai $essai/5 refuse, nouvelle tentative..."
  sleep $((5 * essai))
done

echo "::error::enregistrer : impossible de pousser apres 5 essais"
exit 1
