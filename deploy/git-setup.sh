#!/usr/bin/env bash
# ============================================================================
#  Ersteinrichtung des Git-Repositorys und erster Upload zu GitHub
# ----------------------------------------------------------------------------
#  WICHTIG: Auf dem SERVER ausfuehren, nicht ueber den SMB-Share vom Mac.
#  Samba blockiert das Loeschen von Git-Sperrdateien; git bricht dann mit
#  "unable to index file" oder "index.lock: File exists" ab.
#
#      ssh matze@192.168.20.16
#      cd /srv/fanvue/Fanvue_Chatbot
#      bash deploy/git-setup.sh
#
#  Das Skript prueft VOR dem Commit, dass keine Zugangsdaten und keine
#  Datenbank mitgehen, und bricht im Zweifel ab. Gepusht wird erst nach
#  ausdruecklicher Bestaetigung.
# ============================================================================
set -euo pipefail

REMOTE_DEFAULT="https://github.com/MatzePee/Chatbot.git"
BRANCH="main"

if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; N=$'\033[0m';
else B=""; G=""; Y=""; R=""; N=""; fi
step() { echo; echo "${B}==> $*${N}"; }
ok()   { echo "  ${G}✓${N} $*"; }
warn() { echo "  ${Y}!${N} $*"; }
die()  { echo "  ${R}✗ $*${N}" >&2; exit 1; }
# Terminal verfuegbar? Einmal ECHT oeffnen - eine blosse Existenzpruefung
# genuegt nicht, /dev/tty kann vorhanden und trotzdem nicht nutzbar sein.
if { : </dev/tty; } 2>/dev/null; then HAS_TTY=1; else HAS_TTY=0; fi

# Fragt am Terminal, sonst auf der Standardeingabe. Ohne diesen Rueckfall
# gaelte stillschweigend die Vorgabe - beim Hochladen also ungewollt "ja".
ask()  {
  local p="$1" d="${2:-}" a=""
  if [ "$HAS_TTY" = "1" ]; then read -r -p "  $p${d:+ [$d]}: " a </dev/tty || true
  else read -r -p "  $p${d:+ [$d]}: " a || true
  fi
  echo "${a:-$d}"
}

cd "$(dirname "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)")"
REPO="$PWD"
step "Projektverzeichnis: $REPO"
[ -f requirements.txt ] && [ -d app ] || die "Hier liegt kein Fanvue-Chatbot-Projekt."

# Warnen, wenn wir doch auf einem Netzlaufwerk stehen
case "$(df -PT . 2>/dev/null | awk 'NR==2{print $2}')" in
  cifs|smbfs|nfs|nfs4|fuse*)
    warn "Dieses Verzeichnis liegt auf einem Netzlaufwerk."
    warn "Git arbeitet dort unzuverlässig - besser direkt auf dem Server ausführen."
    [ "$(ask "Trotzdem fortfahren? (ja/nein)" "nein")" = "ja" ] || exit 1 ;;
esac

command -v git >/dev/null || die "git ist nicht installiert:  sudo apt install git"

# ------------------------------------------------------------ .gitignore prüfen
step ".gitignore prüfen"
[ -f .gitignore ] || die "Keine .gitignore vorhanden - Abbruch, das wäre zu riskant."
for pattern in ".env" "data/"; do
  grep -qx -- "$pattern" .gitignore || die ".gitignore enthält '$pattern' nicht - Abbruch."
done
ok ".env und data/ sind ausgeschlossen"

# ------------------------------------------------------------------ Repository
step "Repository vorbereiten"
if [ -d .git ]; then
  ok "Git-Repository existiert bereits"
  rm -f .git/index.lock 2>/dev/null || true
else
  git init -q
  ok "Repository angelegt"
fi
git symbolic-ref HEAD "refs/heads/$BRANCH" 2>/dev/null || true

git config user.name  >/dev/null 2>&1 || git config user.name  "$(ask 'Dein Name für Commits' "$(whoami)")"
git config user.email >/dev/null 2>&1 || git config user.email "$(ask 'Deine E-Mail für Commits' '')"

git add -A
ok "$(git diff --cached --name-only | wc -l) Dateien vorgemerkt"

# =====================  SICHERHEITSPRÜFUNG  =====================
step "Sicherheitsprüfung - was würde hochgeladen?"
FAIL=0

for f in .env data/bot.db; do
  if git check-ignore -q "$f" 2>/dev/null || [ ! -e "$f" ]; then
    ok "$f wird nicht hochgeladen"
  else
    echo "  ${R}✗ $f WÜRDE HOCHGELADEN${N}"; FAIL=1
  fi
done

if git diff --cached --name-only | grep -qE '^(\.env$|data/|\.venv/|exports/)'; then
  echo "  ${R}✗ Vorgemerkt sind Dateien aus .env / data / .venv / exports:${N}"
  git diff --cached --name-only | grep -E '^(\.env$|data/|\.venv/|exports/)' | sed 's/^/      /'
  FAIL=1
else
  ok "Keine Zugangsdaten- oder Datenbankdateien vorgemerkt"
fi

# Nach echten Schluesseln im INHALT suchen - Muster echter Anbieter-Tokens,
# damit Platzhalter in .env.example keinen Fehlalarm ausloesen.
PATTERNS='sk-or-v1-[A-Za-z0-9]{24}|sk-[A-Za-z0-9]{32,}|[0-9]{8,10}:AA[A-Za-z0-9_-]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|ghp_[A-Za-z0-9]{30,}'
HITS="$(git diff --cached --name-only -z | xargs -0 -r grep -lE "$PATTERNS" 2>/dev/null || true)"
if [ -n "$HITS" ]; then
  echo "  ${R}✗ Mögliche echte Schlüssel gefunden in:${N}"
  echo "$HITS" | sed 's/^/      /'
  FAIL=1
else
  ok "Keine echten API-Keys oder privaten Schlüssel im Inhalt"
fi

BIG="$(git diff --cached --name-only -z | xargs -0 -r du -k 2>/dev/null | awk '$1>2000{print $2" ("int($1/1024)" MB)"}' || true)"
[ -n "$BIG" ] && { warn "Große Dateien:"; echo "$BIG" | sed 's/^/      /'; }

if [ "$FAIL" -ne 0 ]; then
  echo
  echo "  ${R}Prüfung fehlgeschlagen - es wurde NICHTS hochgeladen.${N}"
  echo "  Je nach Meldung oben:"
  echo "    · fehlender Eintrag  -> .gitignore ergänzen"
  echo "    · Datei vorgemerkt   -> git rm --cached <datei>"
  echo "    · Schlüssel im Code  -> Wert aus der Datei entfernen und in .env legen"
  exit 1
fi
echo
echo "  ${B}Diese Dateien gehen hoch:${N}"
git diff --cached --name-only | sed 's/^/    /'

# ---------------------------------------------------------------------- Commit
step "Commit"
if git rev-parse HEAD >/dev/null 2>&1; then
  if git diff --cached --quiet; then
    ok "Keine Änderungen - nichts zu committen"
  else
    git commit -qm "$(ask 'Commit-Nachricht' 'Aktueller Stand')"
    ok "Commit erstellt"
  fi
else
  git commit -qm "$(ask 'Commit-Nachricht' 'Erste Fassung')"
  ok "Erster Commit erstellt"
fi

# ------------------------------------------------------------------- Version
step "Version markieren"
LAST="$(git tag -l 'v*' --sort=-v:refname | head -n1)"
[ -n "$LAST" ] && echo "  Bisher höchste Version: $LAST"
TAG="$(ask 'Versions-Tag (leer = überspringen)' "${LAST:-v1.0.0}")"
if [ -n "$TAG" ]; then
  if git rev-parse "$TAG" >/dev/null 2>&1; then
    warn "Tag $TAG existiert bereits - übersprungen"
  else
    git tag -a "$TAG" -m "$TAG"
    ok "Tag $TAG gesetzt"
  fi
fi

# ------------------------------------------------------------------- Hochladen
step "Zu GitHub hochladen"
if git remote get-url origin >/dev/null 2>&1; then
  ok "Remote: $(git remote get-url origin)"
else
  git remote add origin "$(ask 'Repository-URL' "$REMOTE_DEFAULT")"
  ok "Remote eingetragen"
fi

cat <<EOF

  ${B}Bereit zum Hochladen.${N} Bei HTTPS fragt GitHub nach Benutzername und
  Passwort - als Passwort ein ${B}Personal Access Token${N} verwenden
  (github.com → Settings → Developer settings → Tokens, Rechte: repo).

EOF
if [ "$(ask 'Jetzt hochladen? (ja/nein)' 'ja')" = "ja" ]; then
  git push -u origin "$BRANCH"
  git push --tags
  echo
  ok "Hochgeladen. Fertig."
else
  echo
  echo "  Später manuell mit:"
  echo "      cd $REPO && git push -u origin $BRANCH && git push --tags"
fi
