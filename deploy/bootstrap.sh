#!/usr/bin/env bash
# ============================================================================
#  Fanvue-Chatbot - Einzeiler-Installation auf einem frischen Ubuntu
# ----------------------------------------------------------------------------
#      curl -fsSL https://raw.githubusercontent.com/MatzePee/Chatbot/main/deploy/bootstrap.sh | sudo bash
#
#  Holt das Repository, waehlt die neueste veroeffentlichte Version und
#  uebergibt an deploy/install.sh, das den Rest erledigt (Benutzer anlegen,
#  virtuelle Umgebung, .env, Autostart, sudo-Regel, Firewall).
#
#  WICHTIG - "curl | bash": Das Skript kommt selbst ueber die Standardeingabe.
#  Ein `read` von stdin wuerde daher den REST DES SKRIPTS auffressen. Deshalb
#  fragt hier nichts ueber stdin; alle Rueckfragen laufen in install.sh ueber
#  /dev/tty, das auch bei einer Pipe auf das Terminal zeigt.
#
#  Anpassbar ueber Umgebungsvariablen, z.B.:
#      curl -fsSL .../bootstrap.sh | sudo BRANCH=main REF=v1.0.2 bash
# ============================================================================
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/MatzePee/Chatbot.git}"
BRANCH="${BRANCH:-main}"
REF="${REF:-}"          # leer = neuestes Versions-Tag, sonst z.B. v1.0.2

if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; N=$'\033[0m';
else B=""; G=""; Y=""; R=""; N=""; fi
step() { echo; echo "${B}==> $*${N}"; }
ok()   { echo "  ${G}✓${N} $*"; }
warn() { echo "  ${Y}!${N} $*"; }
die()  { echo "  ${R}✗ $*${N}" >&2; exit 1; }

step "Fanvue-Chatbot – Installation"
[ "$(id -u)" -eq 0 ] || die "Bitte mit sudo starten:
    curl -fsSL ${REPO_URL%.git}/raw/$BRANCH/deploy/bootstrap.sh | sudo bash"
command -v apt-get >/dev/null || die "Kein apt-get – dieses Skript ist für Ubuntu/Debian."

step "Grundpakete sicherstellen"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git ca-certificates curl >/dev/null
ok "git $(git --version | awk '{print $3}')"

step "Programmcode holen"
TMP="$(mktemp -d /tmp/fanvue-install-XXXXXX)"
# Egal wie das Skript endet: das temporaere Verzeichnis wieder aufraeumen.
trap 'rm -rf "$TMP"' EXIT
git clone --quiet --branch "$BRANCH" "$REPO_URL" "$TMP/src" \
  || die "Klonen von $REPO_URL fehlgeschlagen. Erreichbar? Richtige URL?"
cd "$TMP/src"

if [ -z "$REF" ]; then
  REF="$(git tag -l 'v*' --sort=-v:refname | head -n1 || true)"
fi
if [ -n "$REF" ]; then
  git checkout --quiet "$REF" 2>/dev/null && ok "Version $REF" \
    || warn "Version '$REF' nicht gefunden – nehme $BRANCH"
else
  warn "Noch keine Version markiert – nehme den aktuellen Stand von $BRANCH"
fi

[ -f deploy/install.sh ] || die "deploy/install.sh fehlt im Repository."

step "Installation starten"
echo "  Ab hier fragt das Installationsskript nach Benutzer, Verzeichnis und Port."
echo
# install.sh als echte Datei ausfuehren (nicht ueber die Pipe) und stdin vom
# Terminal nehmen, damit Rueckfragen auch bei `curl | bash` funktionieren.
# Bewusst KEIN exec: sonst wuerde die Shell ersetzt, der EXIT-trap liefe nie
# und das temporaere Verzeichnis bliebe liegen.
if (exec 3</dev/tty) 2>/dev/null; then
  bash deploy/install.sh </dev/tty
else
  warn "Kein Terminal erkannt – es gelten überall die Vorgaben."
  bash deploy/install.sh </dev/null
fi
