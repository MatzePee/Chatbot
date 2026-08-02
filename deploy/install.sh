#!/usr/bin/env bash
# ============================================================================
#  Fanvue-Chatbot - Installation auf einem frischen Ubuntu-System
# ----------------------------------------------------------------------------
#  Richtet alles ein: Pakete, virtuelle Umgebung, .env, Autostart per systemd,
#  die eng begrenzte sudo-Regel fuer Neustart/Update und die Firewall.
#
#  Aufruf (auf dem Zielserver):
#      sudo bash deploy/install.sh
#
#  Das Skript ist wiederholbar - ein zweiter Lauf repariert eine unvollstaendige
#  Installation, ohne .env oder die Datenbank anzutasten.
# ============================================================================
set -euo pipefail

REPO_URL_DEFAULT="https://github.com/MatzePee/Chatbot.git"
INSTALL_DIR_DEFAULT="/srv/fanvue/Fanvue_Chatbot"
SERVICE="fanvue-chatbot"
PORT_DEFAULT="8000"

# --------------------------------------------------------------- Ausgabehilfen
if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; N=$'\033[0m';
else B=""; G=""; Y=""; R=""; N=""; fi
step()  { echo; echo "${B}==> $*${N}"; }
ok()    { echo "  ${G}✓${N} $*"; }
warn()  { echo "  ${Y}!${N} $*"; }
die()   { echo "  ${R}✗ $*${N}" >&2; exit 1; }
# Terminal verfuegbar? Einmal ECHT oeffnen, und zwar in einer Subshell: eine
# blosse Existenzpruefung genuegt nicht (/dev/tty kann da sein und trotzdem
# nicht nutzbar), und nur die Subshell faengt die Fehlermeldung sauber ab.
if (exec 3</dev/tty) 2>/dev/null; then HAS_TTY=1; else HAS_TTY=0; fi

# Fragt am Terminal, sonst auf der Standardeingabe.
ask()   {
  local p="$1" d="${2:-}" a=""
  if [ "$HAS_TTY" = "1" ]; then read -r -p "  $p${d:+ [$d]}: " a </dev/tty || true
  else read -r -p "  $p${d:+ [$d]}: " a || true
  fi
  echo "${a:-$d}"
}

# --------------------------------------------------------------- Vorbedingungen
step "Vorbedingungen prüfen"
[ "$(id -u)" -eq 0 ] || die "Bitte mit sudo starten:  sudo bash deploy/install.sh"
command -v apt-get >/dev/null || die "Kein apt-get gefunden - dieses Skript ist für Ubuntu/Debian."
command -v systemctl >/dev/null || die "Kein systemd gefunden."
. /etc/os-release 2>/dev/null || true
ok "System: ${PRETTY_NAME:-unbekannt}"

# Wer soll den Dienst ausfuehren? Nie root - der Bot braucht keine Rootrechte.
DEFAULT_USER="${SUDO_USER:-}"
if [ -z "$DEFAULT_USER" ] || [ "$DEFAULT_USER" = "root" ]; then
  DEFAULT_USER="fanvue"
fi
echo "  Mit Enter wird der vorhandene Benutzer übernommen (empfohlen)."
echo "  Ein anderer Name legt einen neuen Systembenutzer an."
SVC_USER="$(ask "Unter welchem Benutzer soll der Bot laufen?" "$DEFAULT_USER")"
[ "$SVC_USER" = "root" ] && die "Der Bot darf nicht als root laufen. Bitte anderen Benutzer wählen."
if ! id -u "$SVC_USER" >/dev/null 2>&1; then
  step "Benutzer '$SVC_USER' anlegen"
  # Streng nicht-interaktiv: adduser erbt sonst das Terminal als Standardeingabe
  # und bleibt bei einer Rueckfrage stehen, die im Skriptablauf niemand sieht -
  # die Installation wirkt dann eingefroren. </dev/null erzwingt den Abbruch
  # einer Rueckfrage statt endloses Warten.
  export DEBIAN_FRONTEND=noninteractive
  if ! adduser --system --group --disabled-password --gecos "" \
        --shell /bin/bash --home "/home/$SVC_USER" "$SVC_USER" </dev/null; then
    # Aeltere/neuere adduser-Fassungen kennen nicht alle Schalter - Rueckfall
    # auf die immer vorhandenen Basiswerkzeuge.
    warn "adduser fehlgeschlagen – versuche useradd"
    useradd --system --create-home --home-dir "/home/$SVC_USER" \
            --shell /bin/bash --user-group "$SVC_USER" </dev/null || true
  fi
  id -u "$SVC_USER" >/dev/null 2>&1 || die "Benutzer '$SVC_USER' konnte nicht angelegt werden."
  ok "Benutzer angelegt"
else
  ok "Benutzer '$SVC_USER' existiert"
fi
SVC_GROUP="$(id -gn "$SVC_USER" 2>/dev/null || echo "$SVC_USER")"

INSTALL_DIR="$(ask "Installationsverzeichnis" "$INSTALL_DIR_DEFAULT")"
PORT="$(ask "Port für die Weboberfläche" "$PORT_DEFAULT")"

# --------------------------------------------------------------------- Pakete
step "Systempakete installieren"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git sqlite3 ca-certificates >/dev/null
ok "python3 $(python3 -V 2>&1 | awk '{print $2}'), git, sqlite3"

# ------------------------------------------------------------------- Quellcode
step "Programmcode bereitstellen"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(dirname "$SCRIPT_DIR")"

if [ -f "$SRC_DIR/requirements.txt" ] && [ -d "$SRC_DIR/app" ]; then
  # Skript liegt bereits im (geklonten) Projekt
  if [ "$SRC_DIR" != "$INSTALL_DIR" ]; then
    mkdir -p "$(dirname "$INSTALL_DIR")"
    if [ -d "$INSTALL_DIR" ]; then
      warn "$INSTALL_DIR existiert bereits - Code wird nicht überschrieben"
    else
      cp -a "$SRC_DIR" "$INSTALL_DIR"
      ok "Kopiert nach $INSTALL_DIR"
    fi
  else
    ok "Läuft bereits aus $INSTALL_DIR"
  fi
elif [ -d "$INSTALL_DIR/.git" ]; then
  ok "Vorhandenes Git-Repository unter $INSTALL_DIR"
else
  REPO_URL="$(ask "Repository-URL" "$REPO_URL_DEFAULT")"
  mkdir -p "$(dirname "$INSTALL_DIR")"
  echo "  Klone $REPO_URL ..."
  sudo -u "$SVC_USER" git clone --quiet "$REPO_URL" "$INSTALL_DIR" \
    || die "Klonen fehlgeschlagen. Bei einem privaten Repository zuerst den Deploy-Key einrichten (siehe WEITERGABE.md)."
  ok "Geklont nach $INSTALL_DIR"
fi
chown -R "$SVC_USER:$SVC_GROUP" "$INSTALL_DIR"
cd "$INSTALL_DIR"

# ------------------------------------------------------------ Virtuelle Umgebung
step "Virtuelle Python-Umgebung einrichten"
if [ ! -x ".venv/bin/python" ]; then
  sudo -u "$SVC_USER" python3 -m venv .venv
  ok "Umgebung erstellt"
fi
sudo -u "$SVC_USER" .venv/bin/pip install --quiet --upgrade pip
sudo -u "$SVC_USER" .venv/bin/pip install --quiet -r requirements.txt
ok "Abhängigkeiten installiert ($(wc -l < requirements.txt) Pakete)"

# ---------------------------------------------------------------------- .env
step "Konfiguration (.env)"
if [ -f .env ]; then
  ok ".env existiert bereits - bleibt unverändert"
else
  SERVER_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  SERVER_IP="${SERVER_IP:-127.0.0.1}"
  SECRET="$(openssl rand -hex 32 2>/dev/null || head -c32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  echo "  Die Schlüssel lassen sich auch später in der Oberfläche unter"
  echo "  „Einstellungen\" eintragen. Jetzt einfach mit Enter überspringen."
  FV_ID="$(ask "Fanvue Client-ID (optional)" "")"
  FV_SECRET="$(ask "Fanvue Client-Secret (optional)" "")"
  OR_KEY="$(ask "OpenRouter API-Key (optional)" "")"
  cat > .env <<EOF
# Erzeugt von deploy/install.sh am $(date +%Y-%m-%d)
HOST=0.0.0.0
PORT=$PORT
SECRET_KEY=$SECRET

FANVUE_CLIENT_ID=$FV_ID
FANVUE_CLIENT_SECRET=$FV_SECRET
FANVUE_REDIRECT_URI=http://$SERVER_IP:$PORT/oauth/callback

OPENROUTER_API_KEY=$OR_KEY
OPENROUTER_MODEL=openai/gpt-4o-mini
EOF
  chown "$SVC_USER:$SVC_GROUP" .env
  chmod 600 .env          # enthaelt Zugangsdaten - nur der Dienst-Benutzer
  ok ".env angelegt (Rechte 600), SECRET_KEY zufällig erzeugt"
  warn "Redirect-URI in der Fanvue-App muss lauten: http://$SERVER_IP:$PORT/oauth/callback"
fi

mkdir -p data && chown "$SVC_USER:$SVC_GROUP" data

# ------------------------------------------------------- Root-Helferskript
step "System-Knöpfe (Neustart / Update) einrichten"
sed -e "s|^REPO=.*|REPO=\"$INSTALL_DIR\"|" \
    -e "s|^SVC_USER=.*|SVC_USER=\"$SVC_USER\"|" \
    -e "s|^SERVICE=.*|SERVICE=\"$SERVICE\"|" \
    "$INSTALL_DIR/deploy/fanvue-admin" > /usr/local/bin/fanvue-admin
chown root:root /usr/local/bin/fanvue-admin
chmod 755 /usr/local/bin/fanvue-admin
ok "/usr/local/bin/fanvue-admin installiert"

SUDOERS=/etc/sudoers.d/fanvue-admin
cat > "$SUDOERS" <<EOF
# Erlaubt '$SVC_USER' GENAU diese drei Aktionen ohne Passwort. Sonst nichts.
$SVC_USER ALL=(root) NOPASSWD: /usr/local/bin/fanvue-admin restart-service, /usr/local/bin/fanvue-admin reboot, /usr/local/bin/fanvue-admin update
EOF
chmod 440 "$SUDOERS"
# Kaputte sudoers-Datei kann den Server unbrauchbar machen -> sofort pruefen
if visudo -c -q -f "$SUDOERS"; then
  ok "sudo-Regel installiert und geprüft"
else
  rm -f "$SUDOERS"
  die "sudo-Regel fehlerhaft - wurde wieder entfernt."
fi

# --------------------------------------------------------------------- systemd
step "Autostart einrichten"
cat > "/etc/systemd/system/$SERVICE.service" <<EOF
[Unit]
Description=Fanvue Chatbot (FastAPI/uvicorn)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SVC_USER
Group=$SVC_GROUP
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$INSTALL_DIR/.env
ExecStart=$INSTALL_DIR/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --quiet "$SERVICE"
systemctl restart "$SERVICE"
ok "Dienst aktiviert und gestartet"

# -------------------------------------------------------------------- Firewall
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "^Status: active"; then
  ufw allow "$PORT/tcp" >/dev/null 2>&1 && ok "Firewall: Port $PORT freigegeben"
fi

# ------------------------------------------------------------------- Kontrolle
step "Funktionsprüfung"
for i in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    ok "Anwendung antwortet auf Port $PORT"; break
  fi
  [ "$i" -eq 20 ] && { warn "Keine Antwort. Protokoll ansehen mit:  journalctl -u $SERVICE -n 50"; break; }
  sleep 1
done
sudo -u "$SVC_USER" sudo -n /usr/local/bin/fanvue-admin 2>&1 | grep -q usage \
  && ok "sudo-Regel funktioniert (Update-Knopf einsatzbereit)" \
  || warn "sudo-Regel greift noch nicht - Neustart-/Update-Knöpfe prüfen"

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
cat <<EOF

${B}Installation abgeschlossen.${N}

  Oberfläche:   ${B}http://${IP:-127.0.0.1}:$PORT${N}
  Verzeichnis:  $INSTALL_DIR
  Dienst:       systemctl status $SERVICE
  Protokoll:    journalctl -u $SERVICE -f

${B}Nächste Schritte:${N}
  1. Oberfläche öffnen, unter „Einstellungen" die Fanvue- und OpenRouter-Daten
     eintragen (falls beim Installieren übersprungen) und speichern.
  2. In der Fanvue-App die Redirect-URI hinterlegen:
     http://${IP:-127.0.0.1}:$PORT/oauth/callback
  3. Auf „Mit Fanvue verbinden" klicken.

${Y}Hinweis:${N} Die Oberfläche hat keine Anmeldung. Nur im eigenen Netz betreiben
oder einen Reverse-Proxy mit Passwort und HTTPS davorsetzen.
EOF
