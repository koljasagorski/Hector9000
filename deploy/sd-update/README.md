# Offline-Update per SD-Karte (für Pi ohne SSH-Zugang)

Der alte Pi (Raspbian 11, 32 Bit) war nicht verlässlich per SSH erreichbar. Deshalb läuft ein Update **einmalig beim
nächsten Boot** von der SD-Karte: Die FAT-Boot-Partition kann jeder Rechner lesen und beschreiben.

## Ablauf

1. **Payload bauen** (auf dem Entwicklungsrechner, beide Repos gepusht):

       deploy/sd-update/build-payload.sh ~/Documents/Hector9000-SD-Lauf3

   Es entsteht `hector-update/` mit `run.sh`, `Hector9000.bundle`, `Hector9000WebUI.bundle` (Git-Bundles der beiden
   Branches, volle Historie) und `VERSION` (`backend-<sha>-webui-<sha>`; derselbe Payload läuft nie zweimal).
2. Karte in den Rechner stecken. **Zuerst `hector-update/update.log` sichern** (Ergebnis des vorigen Laufs).
3. Den Ordner `hector-update` auf der Karte ersetzen. Empfohlen: vorher die originale `cmdline.txt` der Karte als
   `hector-update/cmdline.txt.orig` ablegen (ohne sie bereinigt `run.sh` die Zeile per `sed`).
4. An `cmdline.txt` (**eine Zeile**, Leerzeichen davor, kein Zeilenumbruch) anhängen:

       systemd.run=/boot/hector-update/run.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target
5. Karte in den Pi. Er startet einmal im Update-Modus (1 bis 3 Minuten), startet neu und bootet normal.
6. Karte wieder an den Rechner: `update.log` prüfen (am Ende `backend updated: 1  webui updated: 1`), die Datei
   `hector-update/admin-password.txt` lesen und **danach löschen**.

## Was `run.sh` tut

* Stellt als Erstes `cmdline.txt` wieder her, damit der nächste Boot normal ist, egal was danach passiert.
* Sucht die Repos (`HectorServer.py` + `setup.py`; `Main.html` + `js/hector9000.js`), sichert jedes per tar im Home des
  Besitzers, stasht lokale Änderungen, holt den Branch aus dem Bundle und wendet die Änderungen wieder an. Bei einem
  Konflikt wird zurückgerollt. **Die lokalen Änderungen des Pi (`HectorServer.py`, `conf/database.py`, `conf/drinks.py`,
  `start.sh`) werden nie überschrieben.** Das WebUI wird nur zusammen mit dem Backend aktualisiert.
* Installiert den Kiosk-Autostart (`install.sh` des WebUI) und, falls nichts anderes das Backend startet,
  `hector9000.service` (führt `start.sh` aus).
* **Abschnitt 7:** legt `/var/lib/hector9000` und die eigene Unit `hector9000-stats.service` an (Statistik +
  Admin, Port 8080), setzt `HECTOR_EXPECT_PROCESSES` je nach `start.sh` (LED-Server auskommentiert → nur
  `server,controller`) und erzeugt das Admin-Passwort. Eine Kopie landet als `hector-update/admin-password.txt` auf der
  Karte, damit man es ohne SSH lesen kann; im `update.log` steht es nicht. Die FAT-Partition ist für jeden lesbar, der die
  Karte hat: Datei nach dem Lesen löschen.
* Schreibt alles nach `hector-update/update.log` und merkt sich die `VERSION` in `/var/lib/hector-update.done`.

## Rückweg

Backups liegen auf dem Pi unter `~/hector-backup-<Datum>-<Repo>.tar.gz`. Den Statistikdienst abschalten:
`sudo systemctl disable --now hector9000-stats.service`. Alte Commits bleiben auf dem vorherigen Branch erreichbar.

## Getestet

Im Container (`debian:bullseye`, Python 3.9.2) als Kette „Lauf 2 → Lauf 3“ auf einem nachgebauten Pi-Stand mit lokalen
Änderungen: Repos aktualisiert, lokale Änderungen erhalten, Unit und Passwortdatei angelegt (Passwort nicht im Log),
Statistikdienst startet als Benutzer `pi` (Login 401/200), zweiter Lauf wirkungslos. **Auf dem echten Pi noch nicht
ausgeführt.**
