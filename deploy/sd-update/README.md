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
* **Abschnitt 8:** installiert `mqtt-guard.sh` als `/usr/local/sbin/hector9000-mqtt-guard` und die Unit
  `hector9000-mqtt-guard.service` (läuft vor dem Netzwerk und vor Mosquitto). Sie lässt auf den Ports 1883 und 9001 nur
  Verbindungen über `lo` durch (alle Hector-Programme sprechen mit `localhost`) und verwirft den Rest. Genutzt wird
  `nft`, sonst `iptables`/`ip6tables`; fehlt beides, steht im `update.log` eine Warnung und die Ports bleiben offen.
  Mosquitto selbst wird nicht angefasst. Wirksam ab dem nächsten Boot; Prüfen: `sudo hector9000-mqtt-guard status`
  (das Admin-Dashboard zeigt es im Reiter „System“ ebenfalls an). Die Sperre gilt nur für das Netzwerk: wer
  den Broker von einem anderen Rechner aus nutzen will (z. B. MQTT Explorer), muss sie vorher abschalten.
* Schreibt alles nach `hector-update/update.log` und merkt sich die `VERSION` in `/var/lib/hector-update.done`.

## Rückweg

Backups liegen auf dem Pi unter `~/hector-backup-<Datum>-<Repo>.tar.gz`. Den Statistikdienst abschalten:
`sudo systemctl disable --now hector9000-stats.service`. Alte Commits bleiben auf dem vorherigen Branch erreichbar.

## Getestet

Im Container (`debian:bullseye`, Python 3.9.2) als Kette „Lauf 2 → Lauf 3“ auf einem nachgebauten Pi-Stand mit lokalen
Änderungen: Repos aktualisiert, lokale Änderungen erhalten, Unit und Passwortdatei angelegt (Passwort nicht im Log),
Statistikdienst startet als Benutzer `pi` (Login 401/200), zweiter Lauf wirkungslos. **Auf dem echten Pi noch nicht
ausgeführt.**
Der Wächter ist separat getestet (`deploy/sd-update/test-mqtt-guard.sh`): zwei Docker-Container auf `debian:bullseye`,
einmal mit `nft`, einmal mit `iptables`. Von außen sind 1883/9001 danach gesperrt, 8080 bleibt offen, `localhost` und die
eigene LAN-Adresse funktionieren, doppeltes Starten legt keine Regeln doppelt an, `stop` hebt die Sperre auf, ohne
Werkzeug gibt es eine Warnung mit Exit-Code 1. IPv6 und die Reihenfolge beim Booten (Unit vor Mosquitto) sind nicht
getestet.

## Diagnose ohne Zugriff aufs Gerät (`diagnose.sh`)

Für eine Karte, an deren Pi sich nichts anschließen lässt (das Journal ist `volatile`, nach dem Ausschalten ist es weg): `diagnose.sh` stellt als Erstes die `cmdline.txt` wieder her
(normaler Boot danach, egal was passiert), wartet, bis das System 150 s läuft, und schreibt Zustand der Dienste, Journal-Auszüge, Netzwerk, `dmesg`, einen Handstart des Controllers und
einen Import-Test nach `hector-diag/diag.txt` auf die Boot-Partition (FAT, an jedem Rechner lesbar). Vorbereiten am Rechner (Boot-Partition eingehängt): Ordner `hector-diag` anlegen,
`diagnose.sh` als `run.sh` hinein, die unveränderte `cmdline.txt` als `cmdline.txt.orig` daneben, an die **eine** Zeile der `cmdline.txt` ` systemd.run=/boot/firmware/hector-diag/run.sh`
anhängen. Karte in den Pi, rund 4 Minuten laufen lassen, Strom trennen, `diag.txt` lesen. Der Handstart legt `h9k.db` an wie ein normaler Start.

