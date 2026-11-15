# Übergabe: Stand und Arbeitsweise

Stand **2026-11-15** (Ende der Sitzung mit Image, Härtung und Wächter). Für alle, die (Menschen oder Coding-Agenten) hier weiterarbeiten. Das Dokument beschreibt,
was gebaut ist, was auf dem Pi läuft, wie Updates dorthin kommen und was noch offen ist.
**Zugangsdaten, Passwörter und Adressen stehen absichtlich nicht im Repository** und gehören auch nicht hinein.

## 1. Repositories und Branches

| Repo | Pfad lokal | Branch | Bemerkung |
|---|---|---|---|
| Backend (dieses) | `Hector9000/` | `feature/congress-webui` | origin = Fork `koljasagorski/Hector9000`, `upstream` = `H3c702` (nur lesen) |
| WebUI | `../Hector9000WebUI/` | `feature/congress-design` | origin = Fork `koljasagorski/Hector9000WebUI` |

* `master` ist in beiden Repos unverändert; die Feature-Branches sind **noch nicht** nach `master` gemergt.
* `gh` löst im WebUI-Ordner auf `H3c702` auf. Immer `--repo koljasagorski/…` angeben.
* Gepusht wird nur nach `origin` (den Fork). Nie Force-Push auf gemeinsame Branches.
* Das WebUI-Repo hat keine LICENSE-Datei. Doom (GPL-2.0) und Freedoom (BSD) liegen mit eigenen Lizenzdateien in `doom/`.
* Falle im WebUI: Die gitignore.io-Vorlage ignoriert `*.html` und `build/`. Deshalb stehen dort die Ausnahmen
  `!doom/index.html` und `!doom/build/`. Ein frischer Klon muss 15 Dateien unter `doom/` enthalten.

## 2. Was es gibt (Überblick)

```
WebUI (Chromium-Kiosk, 800x480 Touch)  --MQTT/WebSocket 9001-->  Mosquitto  <--MQTT 1883-->  Backend-Prozesse
                                                                                               HectorServer.py      Rezepte/Getränke (DB)
                                                                                               HectorController.py  Dosieren, Hardware, System, Events
                                                                                               LEDStripServer.py    WS2812-Animationen
                                                                                               StatsServer.py       Statistik + Admin + Wächter (neu)
```

| Baustein | Wo | Doku |
|---|---|---|
| Kongress-Design, LED-Modi, Neustart/Vollbild verlassen | WebUI + Backend | `README.md` (beide Repos) |
| LED-Animationen | `Hector9000/LEDStripServer.py`, `utils/LEDAnimations.py` | `README.md` |
| **Statistik-Seite und Admin-Dashboard** | `Hector9000/StatsServer.py`, `utils/Stats*.py`, `web/` | [`stats.md`](stats.md) |
| **Matrix-Chat-Bot** (Meldungen bei Fehlern; Einrichtung im Dashboard-Reiter „Chat“) | `utils/Notifier.py`, `utils/NotifySettings.py`, `utils/Watchdog.py` | [`matrix.md`](matrix.md) |
| **Ereignisse über MQTT** | `utils/Events.py`, `HectorController.py` | siehe unten |
| **Doom-Osterei** (Cocktail „Doom“ starten) | WebUI `doom/`, `js/hector9000.js` | WebUI `doom/README.md` |
| **Schlankes OS** (Raspberry Pi OS Lite, systemd, cage) | `deploy/` | [`../deploy/README.md`](../deploy/README.md), [`plans/2026-11-14-slim-os.md`](plans/2026-11-14-slim-os.md) |
| **Fertiges SD-Karten-Image** (bauen, prüfen, im Container und unter QEMU booten) | `deploy/image/` | [`../deploy/image/README.md`](../deploy/image/README.md) |
| **Firewall-Wächter für den alten Pi** (MQTT-Ports nur über `localhost`) | `deploy/sd-update/mqtt-guard.sh` | [`../deploy/sd-update/README.md`](../deploy/sd-update/README.md) |
| **CI** (Tests auf Python 3.9 und 3.11 mit Broker, ShellCheck, `visudo`, `node --check`) | `.github/workflows/ci.yml` | |
| **Kiosk über lokalen HTTP-Server** statt `file://` | WebUI `kiosk.sh` | WebUI `README.md` |

### MQTT-Ereignisse (neu)

Der Controller veröffentlicht, der `StatsRecorder` schreibt mit:

* `Hector9000/event/dose`, `event/dose_done`, `event/error`, `event/doom` (`{"seconds":…, "ts":…}`; Doom kommt vom WebUI)
* `Hector9000/status/<dienst>` (retained, mit Last Will, damit ein toter Dienst als „offline“ sichtbar wird)

Vorhandene Topics (`doseDrink`, `system/reboot`, `system/kioskExit`, `LEDStrip/…`) sind unverändert. Neu: `system/setTime`
(Epoche als Text; „Uhr stellen“ des Dashboards läuft über den Controller, Antwort auf `…/setTime/return` mit `ok`/`error`).

### Umgebungsvariablen (alle optional)

`HECTOR_STATE_DIR`, `HECTOR_STATS_PORT` (8080), `HECTOR_STATS_BIND`, `HECTOR_ADMIN_PASSWORD`, `HECTOR_MQTT_HOST`,
`HECTOR_DB`, `HECTOR_EXPECT_PROCESSES`, `HECTOR_ENV_FILE` (`/etc/hector9000/hector.env`), `HECTOR_WEBUI_DIR`,
`HECTOR_MATRIX_{HOMESERVER,ROOM,TOKEN,ALLOW_HTTP,MSGTYPE}`, `HECTOR_NOTIFY_LEVEL`, `HECTOR_CONFIG` (JSON-Override
zu `conf/HectorConfig.py`). Beispiel: `deploy/hector.env.example` (ohne echte Werte).

## 3. Der Pi heute (Stand der letzten Prüfung)

* Raspbian 11 (Bullseye), 32-Bit, Pi 2/3, Benutzer `pi` mit passwortlosem `sudo`, Chromium 113, Python 3.9.
* Start über `start.sh` (`nohup`), kein systemd für die Backend-Prozesse. Das WebUI startet per Autostart-Eintrag und `kiosk.sh`.
* **Lokale, nicht committete Änderungen auf dem Pi** in `Hector9000/HectorServer.py`, `Hector9000/conf/database.py`,
  `Hector9000/conf/drinks.py` und `start.sh`.
  **Diese Dateien in Updates nie ändern.** Ein Konflikt mit dem Stash lässt das Update zurückrollen. Neue Dienste
  deshalb über **eigene** systemd-Units (z. B. `hector9000-stats.service`) statt über `start.sh`.
  `kiosk.sh` im WebUI darf geändert werden.
* Die Pi-Uhr geht ohne Netz falsch. Im Admin-Dashboard gibt es dafür „Uhr stellen“ (läuft über MQTT beim Controller, der als `pi` mit
  `NOPASSWD`-sudo läuft).
* Der MQTT-Broker des alten Pi ist **anonym** und lauscht auf allen Schnittstellen. Alle Hector-Programme sprechen nur mit `localhost`,
  deshalb sperrt der Wächter `hector9000-mqtt-guard` (Abschnitt 8 in `run.sh`) die Ports 1883/9001 für alles außer `lo`. Ohne den Wächter
  kann jeder im Netz Dosieren und Neustart auslösen. Im schlanken OS lauscht mosquitto nur auf `127.0.0.1`. Das Dashboard zeigt unter
  „System“, ob die Ports von außen erreichbar sind.

## 4. Wie Updates auf den Pi kommen (offline über die SD-Karte)

Der Pi war nicht verlässlich per SSH erreichbar, deshalb läuft das Update **einmalig beim nächsten Boot** von der SD-Karte.
Skript, Bauanleitung und Test stehen in [`../deploy/sd-update/`](../deploy/sd-update/README.md) (`run.sh`, `build-payload.sh`).
Kurzfassung:

1. Karte in einen Rechner stecken; die FAT-Boot-Partition (`/boot`) ist überall lesbar und beschreibbar.
2. Ordner `hector-update/` dorthin legen: `run.sh`, `Hector9000.bundle`, `Hector9000WebUI.bundle` (Git-Bundles der
   beiden Branches), `VERSION` (z. B. `backend-<sha>-webui-<sha>`), `cmdline.txt.orig` (Sicherung). Bauen mit
   `deploy/sd-update/build-payload.sh <Ausgabeordner>`.
3. In `cmdline.txt` (eine einzige Zeile!) `systemd.run=…` anhängen, das `run.sh` einmal als root startet.
4. `run.sh` stellt als Erstes `cmdline.txt` aus `cmdline.txt.orig` wieder her (nächster Boot ist normal, egal was
   danach passiert), sichert per tar-Backup, stasht lokale Änderungen, holt die Bundles, legt Dienste an und rollt
   bei Fehlern zurück. Es schreibt alles nach `hector-update/update.log` auf der Boot-Partition. Dieselbe
   `VERSION` läuft nie zweimal (`/var/lib/hector-update.done`).
5. Karte zurück in den Pi. Danach `update.log` prüfen.

Läufe bisher: **Lauf 1** (Kongress-UI, Kiosk-Autostart) erfolgreich. **Lauf 2** (LED-Animationen) war scharf gesetzt;
sein Ergebnis steht im `update.log` auf der Karte und ist noch nicht ausgewertet. **Lauf 3** (Doom, Statistik,
Ereignisse, `kiosk.sh` mit lokalem HTTP-Server, Wächter für die MQTT-Ports) ist gebaut und im Container (Debian Bullseye, Python 3.9) als Kette
Lauf 2 → Lauf 3 geprüft, aber noch nicht auf dem echten Pi ausgeführt. Was Lauf 3 zusätzlich zu den Repos tut
(Abschnitte 7 und 8 in `deploy/sd-update/run.sh`):

* legt `/var/lib/hector9000` an und eine **eigene** Unit `hector9000-stats.service` (Benutzer = Besitzer des Repos,
  Port 8080, `HECTOR_DB=<Repo>/h9k.db`); `start.sh` wird nicht angefasst;
* setzt `HECTOR_EXPECT_PROCESSES` je nach `start.sh` (ist der LED-Server dort auskommentiert, erwartet der Wächter nur
  `server,controller`);
* erzeugt das Admin-Passwort und legt eine Kopie als `hector-update/admin-password.txt` auf die Boot-Partition, damit
  man es ohne SSH lesen kann. **Die Datei nach dem Lesen löschen** (die FAT-Partition ist für jeden lesbar, der die Karte
  hat); im `update.log` steht das Passwort nicht. Auf dem Pi liegt es in `/var/lib/hector9000/admin_password` (0600);
* **Abschnitt 8:** installiert `/usr/local/sbin/hector9000-mqtt-guard` und die Unit `hector9000-mqtt-guard.service` (vor Netzwerk und Mosquitto;
  per Symlink aktiviert wie Abschnitt 7). Fehlen `nft` und `iptables`, steht im `update.log` eine Warnung (Ports bleiben offen). Wirksam ab dem
  nächsten Boot; Test der Sperre in Docker: `deploy/sd-update/test-mqtt-guard.sh`;
* braucht etwa 15 MB (Bundles) plus den Platz im Repo (Freedoom-WAD 29 MB, Wasm); die Boot-Partition muss die Bundles
  fassen, die Repos liegen auf der Root-Partition. Der zuletzt gebaute Payload liegt auf dem Rechner des Maintainers
  (`~/Documents/Hector9000-SD-Lauf3/`, mit `ANLEITUNG.txt`); er ist nicht im Repo, weil er Bundles enthält.

Nach dem Boot: `http://<ip>:8080/` (öffentlich) und `http://<ip>:8080/admin` (Login `admin`). Der Cocktail „Doom“ ist
im WebUI immer da (virtueller Eintrag, falls das Backend keinen liefert). Der Matrix-Bot wird im Dashboard-Reiter „Chat“
eingerichtet (Speicherort `/var/lib/hector9000/notify.json`, gehört dem Dienstbenutzer, siehe [`matrix.md`](matrix.md)).

Stand des Payloads: neu gebaut am 2026-11-15 mit Backend `96b376d` (Reiter „Chat“, MQTT-Wächter `mqtt-guard.sh`) und WebUI `c830fc5`,
`VERSION` = `backend-96b376d-webui-c830fc5`. Der Erledigt-Marker ist versioniert; ein älterer, schon ausgeführter Payload
läuft mit der neuen `VERSION` also noch einmal.

Das **schlanke OS** ist der andere Weg (frisch flashen, `deploy/provision.sh`, oder gleich das fertige Image aus `deploy/image/`). Es ist bisher
nur im Container (`debian:bookworm`, arm64), als System des Images im Container und unter QEMU getestet, **nicht auf echter Hardware**.

## 4a. Das schlanke OS und das Image (Stand)

* **Benutzer:** `hector` (Server, Controller, Kiosk, LED als root), `hector-stats` (Dashboard, keine Gruppen außer `video`, kein sudo, Sandbox), `mosquitto`.
* **Daten:** `/var/lib/hector9000` gehört `root` (nur Einhängepunkt); darunter `hector/` (`h9k.db`, Arbeitsverzeichnis, `hector` 0751) und `stats/`
  (`stats.db`, `admin_password`, `notify.json`, `hector-stats` 0750). Im Image ist das die eigene Partition `HECTORDATA`, eingehängt per **PARTUUID** mit
  `nosuid,nodev,noexec,nofail`. Sie wächst beim ersten Start (`hector-firstboot`) bis zum Kartenende.
* **Schreibschutz** der Systempartition: `hector-overlay on|off|status` (Wartungsmenü Punkt 8), im Image aus. Falle: `raspi-config enable_overlayfs`
  überlagert auch die Datenpartition; das Werkzeug schreibt deshalb `overlayroot=tmpfs:recurse=0`.
* **Image bauen und prüfen:** [`../deploy/image/README.md`](../deploy/image/README.md): `build-image.sh`, `check-image.sh` (statisch), `boot-test.sh` (System im
  Container), `qemu-test.sh` (Boot mit Raspberry-Pi-Kernel in QEMU).
* **Rename-Dialog der Basis abgeschaltet:** ohne `userconf.txt` blockiert `userconfig.service` (Dialog auf VT8) den Boot und verdeckt den Kiosk. Drop-in
  `deploy/image/userconfig-hector.conf` (installiert von `provision.sh --image`); `hector-firstboot` macht nach der Imager-`custom.toml` das `getty@tty1` von
  `cancel-rename` rückgängig (tty1 gehört dem Kiosk). Stand der Image-Tests: Container 66 ok, statisch 109 ok, QEMU 3 Phasen ok (54), siehe `deploy/image/README.md`.
* **Sudo:** nur Neustart, Kiosk/Dienste neu starten, `date -u -s @<10 Ziffern>` (Uhr stellen über den Controller), Passwort anzeigen (als `hector-stats`),
  `hector-overlay status|on|off`. Ein Test (`tests/test_DeployLayout.py`) hält Skripte, Units, sudoers und Pfade im Gleichlauf.
* **Restrisiko, bewusst nicht behoben:** MQTT hat keine Absender-Prüfung. Wer den Broker erreicht (im schlanken OS nur lokale Prozesse), kann „Uhr stellen“ mit
  plausiblem Wert und Neustart auslösen. Zugriffsrechte pro Benutzer bräuchten Zugangsdaten auch im Browser (Websocket 9001).

## 5. Regeln und Entscheidungen

* **GitHub in beide Richtungen aktuell halten:** zu Beginn `git fetch --prune`, Upstream vergleichen, fremde oder
  uncommittete Arbeit nicht überschreiben; fertige Änderungen in nachvollziehbaren Commits speichern und sofort pushen;
  zum Schluss fetchen und Gleichstand prüfen.
* **Keine Secrets im Repo:** `hector.env`, `admin_password`, `*.db` und `/stats.db*` stehen in `.gitignore`. Die Beispiel-Datei
  `deploy/hector.env.example` hat nur Platzhalter.
* **Kiosk ohne `--allow-file-access-from-files`:** `kiosk.sh` startet einen lokalen Webserver (nur `127.0.0.1`, Port ab 8765)
  und öffnet die UI von dort. Das Flag gibt es nur noch im `file://`-Fallback (kein `python3`, alle Ports belegt), mit WARNING im Log.
  Grund: Mit dem Flag kann jedes Skript, das je im Kiosk läuft, lokale Dateien lesen. Passend dazu werden Getränke- und
  Zutatennamen im WebUI mit `escapeHtml()` ausgegeben (kein rohes `innerHTML`).
* **Doom:** Engine `cloudflare/doom-wasm` (Commit `65e0d3ae…`, GPL-2.0, Emscripten 3.1.64) mit Freedoom Phase 1.
  `startup_delay` muss 2000 bleiben (bei 0 laufen die Tics nie). `-warp` nicht benutzen (startet ein kaputtes Spiel);
  stattdessen läuft nach dem Start ein Auto-Menü. Wasm/WAD/Config lädt `doom.js` selbst per XHR (Emscripten 3.1.64 hat im
  `file://`-Zweig vertauschte Promise-Parameter). Ein virtueller „Doom“-Button erscheint immer, wenn das Backend keinen
  Doom-Drink liefert (`?nodoom` schaltet ihn ab). Genaue Prüfsummen und Bauanleitung: WebUI `doom/README.md`.
* **Statistik/Admin:** Basic-Auth mit Benutzer `admin`; Passwort aus Umgebung oder Datei. Gefährliche Aktionen
  (Neustart, Ventile, Uhr stellen …) werden **während einer Dosierung abgelehnt**. Der Alkohol-Wert ist nur eine Schätzung.
  Ein Sicherheitsreview (2026-11-15) hat zu Korrekturen geführt: negatives `Content-Length` (Speicher-DoS ohne Login),
  Verbindungslimit samt Gesamtfrist pro Anfrage, globales Login-Limit (nimmt bekannte Geräte aus), Zeilen-Obergrenzen
  je Tabelle, Obergrenze für Chat-Meldungen aus MQTT, Uhr-Bereich in der sudoers-Regel, Schutz in `migrate-data.sh`
  gegen Symlinks im Archiv und im Statusverzeichnis, kein Secret mehr in der Kiosk-Umgebung. Danach: eigener Benutzer `hector-stats`
  (Punkt 9 des Reviews erledigt), `root` folgt keinen Links in Ordnern der Dienstbenutzer, Datenpartition per PARTUUID. **Offen** (siehe unten):
  Klartext-HTTP (`HECTOR_ADMIN_REMOTE=0` im Fremdnetz).
* **Benachrichtigungen:** Dedup 3600 s, Warteschlange 50, Backoff, Nachrichten älter als 6 h werden verworfen.
* **Sprache:** Docs im Backend (`docs/`, `deploy/`) auf Deutsch, READMEs im WebUI und das Top-Level-README auf Englisch.

## 6. Tests

Backend (mit lokalem MQTT-Broker für die älteren Tests; ohne Broker laufen nur die neuen):

    python -m pytest tests -q                       # alle; die Broker-Tests brauchen Mosquitto auf localhost:1883
    python -m pytest tests/test_Stats.py tests/test_StatsServer.py tests/test_Notifier.py tests/test_NotifySettings.py \
        tests/test_Watchdog.py tests/test_ControllerEvents.py tests/test_HectorConfig.py \
        tests/test_HectorSystem.py tests/test_DeployLayout.py       # ohne Broker (198 bestanden)

Die CI (`.github/workflows/ci.yml`) führt beides auf Python 3.9 und 3.11 aus (Mosquitto wird dort installiert) und prüft Shell-Skripte
(`bash -n`, ShellCheck), die sudoers-Datei (`visudo -cf`) und die JavaScript-Dateien des Dashboards (`node --check`). Das ältere
`python-app.yml` stammt vom Upstream und ist unverändert.

WebUI: Beim Öffnen von `Main.html?testing` läuft die UI ohne MQTT mit Testdaten. Doom und die HTML-Escapes wurden mit
Playwright (Chrome, Touch) über `http://127.0.0.1` ohne Chromium-Flag geprüft. Das `kiosk.sh` wurde mit einer Chromium-Attrappe
geprüft (normaler Start und Exit-Flag, belegter Port, alle Ports belegt, SIGTERM, Browser-Absturz mit Neustart).

Bash-Tests dürfen den Marker `hector9000-kiosk-profile` nicht wörtlich in der eigenen Kommandozeile haben, sonst trifft
das `pkill -f` des Kiosk-Skripts den Test selbst.

## 7. Offen

1. **Hardware-Erprobung**: schlankes OS auf echtem Pi (PCA9685, HX711, GPIO, WS2812, `cage` + Chromium, Bootzeit, RAM).
2. **Doom auf dem Pi (32-Bit-ARM, Chromium 113)**: bisher nur im Desktop-Chrome geprüft (auch bei 6-facher CPU-Drosselung).
   Ton ist aus (`-nosound`; `?sound` schaltet ihn ein). Kosmetik: FRAGS statt ARMS in der Statusleiste und kurz
   „Player 4 left the game“.
3. **Lauf 2 auswerten, Lauf 3 ausführen** (siehe Abschnitt 4).
4. **Merge nach `master`** in beiden Repos ist offen.
5. **Anonymer MQTT-Broker** auf dem alten Pi (Abschnitt 3).
6. `requirements.txt` und `setup.py` sind veraltet (siehe `deploy/requirements-bookworm.txt`); die WebUI-Klon-URL im
   Provisionierungsskript zeigt auf `koljasagorski/Hector9000WebUI`, Branch `feature/congress-design`. Ist das Repo privat,
   schlägt der Klon fehl.
7. Matrix-Bot: Die Einstellungen (Homeserver, Raum, Token, Stufe, Typ) und „Raum beitreten“ gibt es im Dashboard-Reiter
   „Chat“, gespeichert in `notify.json` im Zustandsordner (Vorrang vor `hector.env`). Befehle aus dem Chat sind nur
   vorbereitet (`matrix.md`, Erweiterungspfad). Es gibt noch keinen Matrix-Zugang und keinen echten Sendetest gegen einen
   Homeserver (die Tests prüfen Anfrageform und Fehlerfälle mit einem Ersatz für das Netz).
8. **Sicherheit, noch offen:** Das Admin-Dashboard läuft über Klartext-HTTP und ist standardmäßig aus dem Netz erreichbar
   (`HECTOR_ADMIN_REMOTE=0` + SSH-Tunnel oder TLS-Proxy davor). Die Sperre der MQTT-Ports auf dem alten Pi greift erst mit Lauf 3 (nächster Boot).
   Ein Pi mit eigenem Benutzer pro Dienst (Image) ist nur im Container/QEMU geprüft.
9. Linear (Team KOL) wurde in dieser Arbeit nicht gepflegt (die Verbindung brauchte eine Anmeldung).
