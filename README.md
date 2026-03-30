# Vokabeltrainer im Browser

Dieses Projekt ist eine Web-Version deines bisherigen Konsolenprogramms.

- Datenquelle: `data/vokabeln.csv` (lokal auf dem Server)
- Browser-Zugriff: `http://SERVER-IP:8090`
- Fortschritt pro Frage (`richtig` / `falsch`) wird direkt in der CSV gespeichert.

## Funktionen

- Kartei-Modus
- Abschreib-Modus
- Deklinations-Modus
- Block-Modus (Blockgröße, Blockauswahl, Wiederholungen)
- Fehler-Modus (höchste Fehler zuerst)
- Browser-Formular zum Hinzufügen neuer Vokabeln
- "Doch als richtig werten"-Button nach einer falschen Antwort
- "Zur Auswahl"-Button auf Lern-/Feedback-/Ergebnis-Seiten
- OpenAI Text-to-Speech mit Audio-Cache (pro Wort nur einmal generieren)

## Projektstruktur

- `app.py`: Flask-Webanwendung
- `wsgi.py`: Startpunkt für gunicorn
- `templates/`: HTML-Seiten
- `static/styles.css`: Design
- `scripts/`: Start-, Setup- und Update-Skripte
- `deploy/vokabeltrainer.service`: systemd-Service-Datei

## 1) GitHub-Repository erstellen (auf deinem Windows-PC)

Im Projektordner (`ubuntuVokabeln`) ausführen:

```powershell
git init
git add .
git commit -m "Initial browser version of vocab trainer"
```

Dann auf GitHub ein neues Repository erstellen, z. B. `ubuntuVokabeln`.

Danach:

```powershell
git remote add origin https://github.com/DEIN-USERNAME/DEIN-REPO.git
git branch -M main
git push -u origin main
```

Wenn du lieber SSH nutzt:

```powershell
git remote add origin git@github.com:DEIN-USERNAME/DEIN-REPO.git
```

## 2) Auf Ubuntu-Server holen

Per SSH auf den Server:

```bash
ssh ubuntu@DEINE_SERVER_IP
```

Dann installieren (falls noch nicht da):

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
```

Repository klonen:

```bash
git clone https://github.com/DEIN-USERNAME/DEIN-REPO.git ubuntuVokabeln
cd ubuntuVokabeln
```

Skripte ausführbar machen:

```bash
chmod +x scripts/*.sh
```

Setup ausführen:

```bash
./scripts/setup_server.sh
```

OpenAI API Key setzen (`.env`):

```bash
echo "OPENAI_API_KEY=dein_key" >> .env
```

Alternativ kannst du den Key auf der Startseite im Browser speichern.
Dann liegt er serverseitig in `data/runtime_secrets.json` (nicht im Git-Repo).

Optionale Drosselung fuer den Audio-Build (`.env`):

```bash
echo "TTS_DELAY_SECONDS=0.8" >> .env
echo "TTS_MAX_NEW_PER_RUN=50" >> .env
```

## 3) Testweise starten

```bash
./scripts/run_gunicorn.sh
```

Dann im Browser öffnen:

- `http://DEINE_SERVER_IP:8090`

Auf der Startseite einmal `Audio-Dateien jetzt erzeugen` klicken, damit die Audios vorab erstellt werden.
Danach werden die gespeicherten Dateien genutzt und es fallen keine erneuten TTS-Kosten für bestehende Wörter an.
Im Lernmodus werden fehlende Audios nicht mehr on-the-fly erzeugt, sondern nur bereits gecachte Dateien abgespielt.

Abbrechen mit `Ctrl+C`.

## 4) Als dauerhaften Service einrichten (systemd)

Service-Datei kopieren:

```bash
sudo cp deploy/vokabeltrainer.service /etc/systemd/system/vokabeltrainer.service
```

Wichtig: Prüfe Benutzer und Pfad in der Datei:

- `User=ubuntu`
- `Group=ubuntu`
- `WorkingDirectory=/home/ubuntu/ubuntuVokabeln`
- `EnvironmentFile=/home/ubuntu/ubuntuVokabeln/.env`
- `ExecStart=/home/ubuntu/ubuntuVokabeln/.venv/bin/gunicorn ...`

Wenn dein Benutzer oder Pfad anders ist, dort anpassen.

Dann aktivieren:

```bash
sudo systemctl daemon-reload
sudo systemctl enable vokabeltrainer
sudo systemctl start vokabeltrainer
sudo systemctl status vokabeltrainer --no-pager
```

## 5) Firewall / Port freigeben

Wenn `ufw` aktiv ist:

```bash
sudo ufw allow 8090/tcp
sudo ufw status
```

Danach wieder im Browser:

- `http://DEINE_SERVER_IP:8090`

## 6) Updates von GitHub einspielen

Auf Server im Projektordner:

```bash
./scripts/update_and_restart.sh
```

Das macht:

- `git pull`
- `pip install -r requirements.txt`
- `systemctl restart vokabeltrainer`

### Wenn `git pull` wegen lokaler Aenderungen blockiert

Einmalig auf dem Server ausfuehren:

```bash
cd ~/ubuntuVokabeln
mkdir -p data
[ -f data/vokabeln.csv ] || cp vokabeln.csv data/vokabeln.csv
echo "VOKABEL_DATEI=data/vokabeln.csv" >> .env
git restore --staged vokabeln.csv 2>/dev/null || true
git restore vokabeln.csv 2>/dev/null || true
./scripts/update_and_restart.sh
```

Danach schreibt die App in `data/vokabeln.csv` und `git pull` kollidiert nicht mehr mit deinem Lernstand.

## 7) Automatische Updates aktivieren

Wenn du willst, dass der Server automatisch neue GitHub-Commits einspielt:

```bash
cd ~/ubuntuVokabeln
chmod +x scripts/*.sh
./scripts/install_autoupdate_cron.sh
```

Das richtet einen Cronjob ein, der jede Minute prüft, ob `origin/main` neuer ist.
Bei einem neuen Commit wird automatisch:

- `git pull`
- `pip install -r requirements.txt`
- App-Neustart (`systemd`, sonst Gunicorn-Neustart)

Logs:

```bash
tail -f ~/ubuntuVokabeln/.autoupdate.log
```

### Ohne Cron: Auto-Update-Loop

Wenn `crontab` in deiner Umgebung nicht funktioniert (z. B. manche Docker-Container):

```bash
cd ~/ubuntuVokabeln
chmod +x scripts/*.sh
./scripts/start_autoupdate_loop.sh
```

Status:

```bash
./scripts/status_autoupdate_loop.sh
```

Stoppen:

```bash
./scripts/stop_autoupdate_loop.sh
```

Loop-Log:

```bash
tail -f ~/ubuntuVokabeln/.autoupdate_loop.log
```

## Optional: Domain + HTTPS (Nginx + Let's Encrypt)

Wenn du willst, kann man davor Nginx als Reverse Proxy setzen und HTTPS aktivieren.
Dann läuft der Trainer über eine Domain statt IP.

## Fehlerbehebung

Logs prüfen:

```bash
sudo journalctl -u vokabeltrainer -n 100 --no-pager
```

Service neu starten:

```bash
sudo systemctl restart vokabeltrainer
```

Prüfen, ob Datei da ist:

```bash
ls -lah data/vokabeln.csv
```

Wenn `data/vokabeln.csv` fehlt oder leer ist, zeigt die Startseite eine Fehlmeldung.

## Hinweis: Wenn `vokabeltrainer.service` nicht gefunden wird

In manchen Umgebungen (z. B. Docker-Container ohne systemd) funktioniert `systemctl` nicht.
Dann ist folgende Meldung normal:

- `Unit vokabeltrainer.service not found`

In dem Fall:

```bash
cd ~/ubuntuVokabeln
git pull
source .venv/bin/activate
pip install -r requirements.txt
./scripts/run_gunicorn.sh
```

Die App laeuft dann direkt im aktuellen Terminal auf `PORT` (Standard: `8090`).
