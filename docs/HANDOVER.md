# Übergabe: Stand und Arbeitsweise

Stand **2026-11-15**. Für alle, die (Menschen oder Coding-Agenten) hier weiterarbeiten. Das Dokument beschreibt,
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
| **Matrix-Chat-Bot** (Meldungen bei Fehlern, Vorbereitung) | `utils/Notifier.py`, `utils/Watchdog.py` | [`matrix.md`](matrix.md) |
| **Ereignisse über MQTT** | `utils/Events.py`, `HectorController.py` | siehe unten |
| **Doom-Osterei** (Cocktail „Doom“ starten) | WebUI `doom/`, `js/hector9000.js` | WebUI `doom/README.md` |
| **Schlankes OS** (Raspberry Pi OS Lite, systemd, cage) | `deploy/` | [`../deploy/README.md`](../deploy/README.md), [`plans/2026-11-14-slim-os.md`](plans/2026-11-14-slim-os.md) |
| **Kiosk über lokalen HTTP-Server** statt `file://` | WebUI `kiosk.sh` | WebUI `README.md` |

### MQTT-Ereignisse (neu)

Der Controller veröffentlicht, der `StatsRecorder` schreibt mit:

* `Hector9000/event/dose`, `event/dose_done`, `event/error`, `event/doom` (`{"seconds":…, "ts":…}`; Doom kommt vom WebUI)
* `Hector9000/status/<dienst>` (retained, mit Last Will, damit ein toter Dienst als „offline“ sichtbar wird)

Vorhandene Topics (`doseDrink`, `system/reboot`, `system/kioskExit`, `LEDStrip/…`) sind unverändert.

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
* Die Pi-Uhr geht ohne Netz falsch. Im Admin-Dashboard gibt es dafür „Uhr stellen“.
* Der MQTT-Broker des alten Pi ist **anonym**. Wer das Netz erreicht, kann Dosieren und Neustart auslösen,
  auch ohne Admin-Login. Im schlanken OS lauscht mosquitto nur auf `127.0.0.1`.

## 4. Wie Updates auf den Pi kommen (offline über die SD-Karte)

Der Pi war nicht verlässlich per SSH erreichbar, deshalb läuft das Update **einmalig beim nächsten Boot** von der SD-Karte:

1. Karte in einen Rechner stecken; die FAT-Boot-Partition (`/boot`) ist überall lesbar und beschreibbar.
2. Ordner `hector-update/` dorthin legen: `run.sh`, `Hector9000.bundle`, `Hector9000WebUI.bundle` (Git-Bundles der
   beiden Branches), `VERSION` (z. B. `backend-<sha>-webui-<sha>`), `cmdline.txt.orig` (Sicherung).
3. In `cmdline.txt` (eine einzige Zeile!) `systemd.run=…` anhängen, das `run.sh` einmal als root startet.
4. `run.sh` stellt als Erstes `cmdline.txt` aus `cmdline.txt.orig` wieder her (nächster Boot ist normal, egal was
   danach passiert), sichert per tar-Backup, stasht lokale Änderungen, holt die Bundles, legt Dienste an und rollt
   bei Fehlern zurück. Es schreibt alles nach `hector-update/update.log` auf der Boot-Partition. Dieselbe
   `VERSION` läuft nie zweimal (`/var/lib/hector-update.done`).
5. Karte zurück in den Pi. Danach `update.log` prüfen.

Läufe bisher: **Lauf 1** (Kongress-UI, Kiosk-Autostart) erfolgreich. **Lauf 2** (LED-Animationen) war scharf gesetzt,
sein Ergebnis steht im `update.log` auf der Karte und ist noch nicht ausgewertet. **Lauf 3** (Doom, Statistik,
Ereignisse, `kiosk.sh` mit lokalem HTTP-Server) ist vorbereitet, aber noch nicht ausgeführt: `run.sh` muss dafür
eine eigene `hector9000-stats.service` anlegen (Arbeitsverzeichnis = Repo, `HECTOR_DB=./h9k.db`,
`HECTOR_STATE_DIR=/var/lib/hector9000`, Benutzer `pi`) und der freie Platz der Boot-Partition reicht für
Freedoom-WAD (29 MB), Wasm und Bundles prüfen. Hinweis: Die Karte wurde zeitweise an einem anderen PC bearbeitet.

Das **schlanke OS** ist der andere Weg (frisch flashen, `deploy/provision.sh`). Es ist bisher nur im Container
(`debian:bookworm`, arm64) getestet, nicht auf echter Hardware.

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
  Verbindungslimit samt Gesamtfrist pro Anfrage, globales Login-Limit, Uhr-Bereich in der sudoers-Regel, Schutz in
  `migrate-data.sh` gegen Symlinks im Archiv, kein Secret mehr in der Kiosk-Umgebung. **Offen** (siehe unten):
  Klartext-HTTP (`HECTOR_ADMIN_REMOTE=0` im Fremdnetz) und die gemeinsame Vertrauensdomäne des Benutzers `hector`.
* **Benachrichtigungen:** Dedup 3600 s, Warteschlange 50, Backoff, Nachrichten älter als 6 h werden verworfen.
* **Sprache:** Docs im Backend (`docs/`, `deploy/`) auf Deutsch, READMEs im WebUI und das Top-Level-README auf Englisch.

## 6. Tests

Backend (mit lokalem MQTT-Broker für die älteren Tests; ohne Broker laufen nur die neuen):

    python -m pytest tests -q                       # Stand: 154 bestanden
    python -m pytest tests/test_Stats.py tests/test_StatsServer.py tests/test_Notifier.py \
        tests/test_Watchdog.py tests/test_ControllerEvents.py tests/test_HectorConfig.py   # ohne Broker

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
7. Matrix-Bot: Befehle aus dem Chat sind nur vorbereitet (`matrix.md`, Erweiterungspfad). Es gibt noch keinen Matrix-Zugang
   und keinen echten Sendetest gegen einen Homeserver.
8. **Sicherheit, aus dem Review noch offen:** Das Admin-Dashboard läuft über Klartext-HTTP und ist standardmäßig aus dem
   Netz erreichbar (`HECTOR_ADMIN_REMOTE=0` + SSH-Tunnel oder TLS-Proxy davor). Statistikdienst, Chromium und Controller teilen
   sich im schlanken OS den Benutzer `hector` (Vorschlag: eigener Benutzer `hector-stats`, siehe `deploy/README.md`, Punkt 9).
9. Linear (Team KOL) wurde in dieser Arbeit nicht gepflegt (die Verbindung brauchte eine Anmeldung).
