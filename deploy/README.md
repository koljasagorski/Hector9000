# Hector9000 auf Raspberry Pi OS Lite (schlankes OS)

Setzt [`docs/plans/2026-11-14-slim-os.md`](../docs/plans/2026-11-14-slim-os.md) um:
kein Desktop, sondern nur mosquitto, die Hector-Dienste als systemd-Units und ein
Chromium-Kiosk in `cage` (Wayland-Kiosk-Compositor) auf tty1.

Es gibt zwei Wege zum gleichen Ergebnis:

1. **Fertiges Image** auf die SD-Karte schreiben (alles ist schon installiert, die Karte wächst beim ersten Start):
   [`deploy/image/README.md`](image/README.md). Der Weg für eine neue Karte.
2. **Auf einer frisch geflashten Karte provisionieren** (dieses Dokument, Ablauf ab Schritt 1). Der Weg für ein
   Update einer bestehenden Installation und für alles, was das Image nicht abdeckt.

> **Teststand:** Skripte, Units, sudoers und mosquitto-Konfiguration sind in einem
> `debian:bookworm`-Container (arm64) geprüft, siehe [Abschnitt „Teststand“](#teststand).
> **Nicht getestet** ist alles, was echte Hardware braucht: Ventile, Pumpe, Waage,
> LED-Streifen, der Kiosk auf dem Touchscreen und das Verhalten auf einem 32-Bit-Pi.
> Erster Lauf deshalb **auf einer zweiten SD-Karte**, nicht auf der Karte, die gerade im Hector steckt.

## Was installiert wird

| Baustein | Wo | Läuft als |
|---|---|---|
| mosquitto, **nur 127.0.0.1** (MQTT 1883, Websocket 9001) | `/etc/mosquitto/conf.d/hector.conf` | `mosquitto` |
| `hector-server` (Ventile, Pumpe, Waage, Arm) | `hector-server.service` | `hector` |
| `hector-controller` (Rezepte, Dosieren, Neustart/Kiosk-Exit) | `hector-controller.service` | `hector` |
| `hector-led` (WS2812 an GPIO 18) | `hector-led.service` | `root` (PWM/DMA braucht `/dev/mem`) |
| `hector-stats` (Statistikseite + Admin-Dashboard, **HTTP 0.0.0.0:8080**) | `hector-stats.service` | `hector-stats` (eigener Benutzer ohne Gruppen und ohne sudo, Sandbox) |
| `hector-kiosk` (cage + Chromium auf tty1) | `hector-kiosk.service` | `hector` |
| `hector-maint` (Textmenü auf tty1, wenn der Kiosk gestoppt ist) | `hector-maint.service` | `hector` |
| `hector-firstboot` (nur im Image: Datenpartition vergrößern, Ordner anlegen, Imager-Einstellungen anwenden) | `hector-firstboot.service` | `root` (einmalig) |
| `hector-overlay` (Schreibschutz der Systempartition an/aus) | `/usr/local/sbin/hector-overlay` | `root` (per sudo vom Wartungsmenü) |
| Ziel, das alles bündelt | `hector.target` | |

Pfade: Backend `/opt/hector9000/backend`, WebUI `/opt/hector9000/webui` (beide sind
Symlinks oder Checkouts), Python-venv `/opt/hector9000/venv`, Einstellungen `/etc/hector9000/hector.env`.

**Daten** liegen unter `/var/lib/hector9000` (im Image eine eigene Partition `HECTORDATA`), getrennt nach Benutzern:

| Ordner | Inhalt | Besitzer, Modus |
|---|---|---|
| `/var/lib/hector9000` selbst | nur Einhängepunkt | `root:root` 0755 (keiner der beiden Dienstbenutzer kann den Ordner darunter durch einen Link austauschen) |
| `hector/` | `h9k.db` (Rezepte, Zutaten, Ventilbelegung); Arbeitsverzeichnis von Server und Controller | `hector:hector` 0751, die Datei ist für alle lesbar (das Dashboard liest die Flaschennamen) |
| `stats/` | `stats.db`, `admin_password`, `notify.json` (Matrix) | `hector-stats:hector-stats` 0750 |

**Netzwerk:** MQTT ist absichtlich nur lokal erreichbar. Damit kann niemand im WLAN
Dosieren, Neustart oder „Vollbild verlassen“ per MQTT auslösen (die offene
Broker-Lücke des alten Aufbaus). Die Statistikseite ist dagegen ein eigener
HTTP-Dienst auf Port 8080 und aus dem Netz erreichbar; das Admin-Dashboard dort
ist durch ein Passwort geschützt (`HECTOR_ADMIN_PASSWORD`, siehe unten).

**Vertrauensgrenze:** Wer sich mit dem MQTT-Broker verbinden kann, darf alles, was die Oberfläche darf, also auch
„Uhr stellen“ (`system/setTime`, nur plausible Werte, der Controller führt dann `date` aus) und Neustart. Deshalb
lauscht der Broker nur auf `127.0.0.1`. MQTT hat im schlanken OS keine Benutzer und keine Rechte pro Thema; eine
Absender-Prüfung gibt es nicht. Das Admin-Dashboard zeigt im Reiter „System“ (und mit einem Banner), ob Port 1883
oder 9001 von außen erreichbar ist. Auf dem alten Pi (Mosquitto auf allen Schnittstellen) sperrt
`hector9000-mqtt-guard` die Ports, siehe [`sd-update/README.md`](sd-update/README.md).

## Ablauf

### 1. Karte flashen

(Mit dem fertigen Image entfällt dieser Schritt und der nächste: [`image/README.md`](image/README.md).)

Raspberry Pi Imager → **Raspberry Pi OS Lite (64-bit)**, **Debian 12 „bookworm“**. 32-Bit geht
auch (Skripte und Paketnamen sind gleich), ist aber ungetestet.

> **Achtung, Version:** Der Imager schlägt seit Herbst 2025 **Debian 13 „Trixie“** vor. Die Skripte und `requirements-bookworm.txt`
> sind für bookworm geschrieben und nur dort (im Container) geprüft; `provision.sh` warnt bei jeder anderen Version.
> Bookworm gibt es weiterhin als „Raspberry Pi OS (Legacy)“ (Imager: *Raspberry Pi OS (other)*, oder von der Download-Seite von
> raspberrypi.com). Trixie bringt Python 3.13 mit; ob die alten Hardware-Pakete (RPi.GPIO, HX711, Adafruit-PCA9685) dort
> laufen, ist offen (Spike, Schritt 3 im Plan). Welche Pi-Version steckt im Hector? 64-Bit gibt es nur ab Pi 3 (und Pi 2 v1.2),
> ältere brauchen die 32-Bit-Variante.
In den Imager-Einstellungen: Hostname (z. B. `hector`), einen eigenen Admin-Benutzer
mit Passwort (**nicht** `hector`, den legt das Skript selbst an), WLAN, SSH.

### 2. Provisionieren

Auf dem Pi (per SSH), Internet nötig:

```bash
sudo apt-get update && sudo apt-get install -y git
sudo mkdir -p /opt/hector9000
sudo git clone --branch feature/congress-webui https://github.com/koljasagorski/Hector9000.git /opt/hector9000/backend
sudo /opt/hector9000/backend/deploy/provision.sh --dry-run   # erst ansehen
sudo /opt/hector9000/backend/deploy/provision.sh
```

Das Skript ist idempotent (nach einem `git pull` einfach nochmal laufen lassen) und
macht: Pakete (`cage chromium mosquitto i2c-tools python3-venv build-essential` …),
I²C und SPI aktivieren, Benutzer `hector` (Gruppen `gpio i2c spi video render input
systemd-journal`) und `hector-stats` (keine Gruppen außer `video` für `vcgencmd`), venv mit `deploy/requirements-bookworm.txt`, Units, mosquitto-Konfiguration,
sudoers-Regeln (vorher mit `visudo -cf` geprüft), `/etc/hector9000/hector.env` aus der Vorlage.

Optionen: `--backend-dir`, `--webui-dir` (vorhandene Checkouts), `--webui-repo`/`--webui-branch`
(falls das WebUI geklont werden soll), `--requirements`, `--skip-apt`, `--skip-venv`, `--start`,
`--overlayfs`, `--image` (beim Bauen eines Images im chroot: Units nur aktivieren, nichts starten), `--no-systemd`, `--dry-run`. `provision.sh --help` zeigt alles.

Das WebUI wird von `koljasagorski/Hector9000WebUI` (Branch `feature/congress-design`) geklont,
wenn `/opt/hector9000/webui` fehlt. Alternativ vorher selbst klonen und `--webui-dir` angeben.

### 3. Daten vom alten Pi übernehmen

Auf dem **alten** Pi (im Backend-Ordner):

```bash
./deploy/migrate-data.sh export ~/Hector9000 /tmp/hector-data.tar.gz
```

(Das Skript liegt nur im neuen Branch; notfalls aus diesem Repo kopieren.)
Archiv auf den neuen Pi kopieren (`scp`) und dort:

```bash
sudo /opt/hector9000/backend/deploy/migrate-data.sh import hector-data.tar.gz
```

- **Datenbank** `h9k.db` (und `h9k.db.*`) landet in `/var/lib/hector9000/hector`, eine vorhandene wird
  als `h9k.db.bak-<Zeit>` gesichert. Die Dienste öffnen `h9k.db` relativ zu ihrem Arbeitsverzeichnis;
  `WorkingDirectory=/var/lib/hector9000/hector` in den Units sorgt dafür, dass sie dort liegt.
- **Kalibrierung und Ventilpositionen** stehen im Code: `hx711.ref` und `valvepositions` in
  `Hector9000/conf/HectorConfig.py`, die Rezepte in `Hector9000/conf/drinks.py`. Der Import legt die
  lokalen Änderungen des alten Pi unter `/var/lib/hector9000/hector/migrated/` ab (Patch, volle Kopien der
  geänderten Dateien, `info.txt` mit Commit) und prüft nur, ob der Patch passt (`git apply --check`).
  **Er ändert den Checkout nicht.** Übertrage die Werte von Hand (oder wende den Patch an) und starte neu:
  `sudo systemctl restart hector.target`. Siehe TODO „Konfiguration außerhalb des Repos“ unten.

### 4. Einstellungen

`sudo nano /etc/hector9000/hector.env` (Modus `0640 root:hector`, wird vom Skript nur einmal angelegt):

| Variable | Bedeutung |
|---|---|
| `HECTOR_ADMIN_PASSWORD` | Passwort des Admin-Dashboards. Leer: der Stats-Server erzeugt beim ersten Start eines und legt es in `/var/lib/hector9000/stats/admin_password` ab (anzeigen: Wartungsmenü Punkt 7 oder `sudo hector-maint --password`) |
| `HECTOR_ADMIN_REMOTE` | `0` = Dashboard nur vom Gerät selbst (z. B. per SSH-Tunnel); Standard: aus dem Netz erreichbar |
| `HECTOR_MATRIX_HOMESERVER`, `HECTOR_MATRIX_ROOM`, `HECTOR_MATRIX_TOKEN` | Matrix-Benachrichtigung bei Fehlern; leer = aus. Auch im Admin-Dashboard (Reiter „Chat“) einstellbar; das dort Gespeicherte (`/var/lib/hector9000/stats/notify.json`) hat Vorrang |

`HECTOR_STATE_DIR` und `HECTOR_DB` setzt die Unit selbst (`/var/lib/hector9000/stats`, `/var/lib/hector9000/hector/h9k.db`). Eine Zeile
in `hector.env` würde das überschreiben (`EnvironmentFile=` gewinnt gegen `Environment=`) und ist deshalb in der Vorlage nicht enthalten.

Echte Werte gehören nur in diese Datei auf dem Pi, **nie** ins Repository (`hector.env.example` ist nur die Vorlage).
Nach Änderungen: `sudo systemctl restart hector-stats`. Die Datei kann nur `root` und die Gruppe `hector` lesen; der Statistikdienst
(`hector-stats`) bekommt die Werte von systemd (`EnvironmentFile=`), nicht durch eigenes Lesen.

### 5. Starten

```bash
sudo reboot            # empfohlen; oder: sudo systemctl start hector.target
```

Der Kiosk wartet selbst, bis der MQTT-Websocket erreichbar ist (max. 120 s) und startet dann Chromium.

## Vollbild verlassen, Neustart, Wartungsmenü

- Der geheime Button (5× oben links) löst `Hector9000/system/kioskExit` aus. `HectorSystem`
  schreibt das Flag `/tmp/hector9000-kiosk/exit` und beendet Chromium (`pkill`). `kiosk.sh`
  startet Chromium daraufhin nicht neu, `cage` endet, `hector-kiosk-session` beendet sich mit Code 0,
  und systemd startet über `OnSuccess=` das **Wartungsmenü** auf tty1. `hector-kiosk` und
  `hector-maint` schließen sich per `Conflicts=` gegenseitig aus. Stürzt der Kiosk dauerhaft ab
  (5 Fehlstarts in 120 s), landet man über `OnFailure=` ebenfalls im Menü.
- Menü: Kiosk starten, IP-Adressen, Dienste-Status, Logs, Dienste neu starten, Neustart.
  Eine Konsole mit Login gibt es mit **Strg+Alt+F2** (USB-Tastatur, `cage -s` erlaubt VT-Wechsel) oder per SSH.
- Reboot-Knopf der WebUI: `sudo -n systemctl reboot` — erlaubt durch `/etc/sudoers.d/hector9000`.

**Welche Werte in `HectorConfig "system"` zu setzen sind:** keine. Die Standardbefehle aus
`utils/HectorSystem.py` passen bereits (`["sudo","-n","systemctl","reboot"]` und
`["pkill","-f","hector9000-kiosk-profile"]`, der Hector-Dienst und Chromium laufen beide als `hector`).
Wer den Kiosk lieber über systemd stoppen will, setzt in `HectorConfig.py`:

```python
"system": {
    "kiosk_exit": ["sudo", "-n", "systemctl", "stop", "hector-kiosk"],
},
```

(das Flag wird vorher trotzdem geschrieben; die sudoers-Regel dafür ist vorhanden.) Auch dann
übernimmt `OnSuccess=` das Wartungsmenü.

sudoers (`deploy/sudoers.d/hector9000`, vor dem Einspielen mit `visudo -cf` geprüft) erlaubt dem Benutzer `hector` **nur**:

| Regel | Zweck |
|---|---|
| `systemctl reboot`, `systemctl start\|stop\|restart hector-kiosk`, `systemctl restart` für `hector-server`, `-controller`, `-led`, `-stats` | Neustart und Dienste (bewusst ohne Wildcard `hector-*`: in sudoers passt `*` auch auf Leerzeichen, damit wäre `restart hector-x ssh.service` erlaubt) |
| `date -u -s @<genau 10 Ziffern, 1,7·10⁹ bis 1,99·10⁹>` | „Uhr stellen“ im Dashboard. Der Statistikdienst hat kein sudo; er schickt `system/setTime` über MQTT, der Controller prüft den Wert (ebenso 10 Ziffern, Bereich Nov 2023 bis Mai 2033, ein Test hält beides gleich) und ruft `date` auf. Der Pi hat keine Batterieuhr und startet offline mit der Zeit des letzten Herunterfahrens. **2033 muss der Bereich verschoben werden.** |
| `cat /var/lib/hector9000/stats/admin_password` **als `hector-stats`** (nicht als root) | Wartungsmenü Punkt 7. Als `hector-stats` gelesen, führt ein untergeschobener Link ins Leere: `root` folgt keinen Links in Ordnern, die ein Dienstbenutzer ändern kann |
| `/usr/local/sbin/hector-overlay status\|on\|off` | Wartungsmenü Punkt 8 (Schreibschutz) |

## Schreibschutz, Stromausfall und Datenpartition

Die Systempartition ist bei Stromausfall das Risiko (ext4 mit Journal übersteht das meist, aber nicht immer). Deshalb:

- **Trennung:** Alles, was zur Laufzeit geschrieben wird (Datenbanken, Statistik, Passwort, Matrix-Einstellungen), liegt auf der eigenen
  Partition `HECTORDATA` unter `/var/lib/hector9000`. Das Image legt sie an; sie wird beim ersten Start auf das Kartenende vergrößert.
  Eingehängt wird sie per **PARTUUID** (nicht per Label: ein zweiter Datenträger oder USB-Stick mit gleichem Label könnte sonst dazwischenfunken),
  mit `defaults,noatime,nosuid,nodev,noexec,nofail,x-systemd.device-timeout=15s`.
- **Schreibschutz der Systempartition** (`hector-overlay`, im Image **aus**): `sudo hector-overlay on` bzw. Wartungsmenü Punkt 8, wirkt nach
  dem nächsten Neustart. Dann ist `/` nur lesbar, jede Änderung landet im Arbeitsspeicher und ist nach dem Neustart weg. Nur `HECTORDATA` bleibt schreibbar.
  **Voraussetzungen**, die das Werkzeug prüft: `/var/lib/hector9000` ist ein eigener Mountpunkt, die Karte-Partition ist read-write
  eingetragen, mindestens etwa 512 MB RAM.
- **Falle:** `raspi-config nonint enable_overlayfs` schreibt `overlayroot=tmpfs` und das Paket `overlayroot` versteht das als `recurse=1`:
  es legt das Overlay über **alle** Dateisysteme aus der fstab, also auch über die Datenpartition. Die Daten wären bei jedem Neustart weg.
  `hector-overlay` schreibt deshalb immer `overlayroot=tmpfs:recurse=0` an den Anfang der `cmdline.txt`; `provision.sh --overlayfs` ruft es auf.
- **Folgen:** Änderungen an `/etc` (`hector.env`, WLAN), `apt` und `git pull` gehen bei eingeschaltetem Schreibschutz beim Neustart verloren: vorher
  `sudo hector-overlay off`, neu starten, ändern, `on`, neu starten. journald schreibt in den Arbeitsspeicher (Begrenzung 50 MB).
- **Ungetestet auf Hardware:** das Einschalten selbst (Kernel, initramfs, `overlayroot`-Hook). Im Image sind der Hook und das Overlay-Kernelmodul
  vorhanden, mehr lässt sich ohne Karte nicht prüfen.

Manuell (ohne Image) eine Datenpartition anlegen: Partition ans Kartenende (ext4, Label `HECTORDATA`), PARTUUID mit `sudo blkid` ablesen und eintragen:

```
PARTUUID=<aus blkid>  /var/lib/hector9000  ext4  defaults,noatime,nosuid,nodev,noexec,nofail,x-systemd.device-timeout=15s  0  2
```

Danach `sudo deploy/provision.sh --skip-apt --skip-venv` (legt `hector/` und `stats/` mit den richtigen Besitzern an) und die Daten übernehmen.

## Update und Rückweg

- **Update:** (bei eingeschaltetem Schreibschutz erst `sudo hector-overlay off`, neu starten, danach wieder `on`)
  `cd /opt/hector9000/backend && sudo git pull && sudo deploy/provision.sh --skip-apt` (WebUI analog mit
  `git pull` in `/opt/hector9000/webui`), danach `sudo systemctl restart hector.target`.
  Das Skript installiert Units, Skripte und Konfiguration nur neu, wenn sich der Inhalt geändert hat,
  und baut das venv nur neu, wenn sich die Requirements-Datei geändert hat.
- **Rückweg:** Die alte SD-Karte bleibt unverändert und ist der Fallback. Zum Zurückdrehen auf der neuen
  Karte: `sudo systemctl disable --now hector.target` (die Daten in `/var/lib/hector9000` bleiben).
- **Datenbank sichern:** `sudo cp /var/lib/hector9000/hector/h9k.db* /pfad/` (die Dienste dürfen laufen; SQLite).
- Im Image enthält das venv fertig gebaute Pakete, die Bauwerkzeuge (`build-essential`, `python3-dev`, `libffi-dev`) sind entfernt. Ändert ein
  Update die Requirements, vorher `sudo apt-get install build-essential python3-dev libffi-dev` und danach wieder entfernen.

## Fehlersuche

```bash
systemctl status hector.target hector-*                  # was läuft
journalctl -u hector-controller -f                       # Log eines Dienstes (-u hector-server / -led / -stats / -kiosk)
journalctl -u hector-kiosk -b                            # Kiosk seit dem Boot
ss -ltn | grep -E ':(1883|9001|8080) '                   # 1883/9001 nur auf 127.0.0.1, 8080 auf allen
mosquitto_sub -h 127.0.0.1 -t 'Hector9000/#' -v          # MQTT live mitlesen
sudo -u hector sudo -n -l                                # was darf hector als root
sudo i2cdetect -y 1                                      # PCA9685 sollte auf 0x40 (und 0x70) auftauchen
```

| Symptom | Ursache / Abhilfe |
|---|---|
| Schwarzer Bildschirm, im Log `cage`-Fehler | `journalctl -u hector-kiosk -b`. Fehlt GPU-Treiber (`libgl1-mesa-dri`), `video`/`render`-Gruppe, oder ist `dtoverlay=vc4-kms-v3d` in `config.txt` aus? Notfalls `HECTOR_KIOSK_FLAGS` in der Unit um `--disable-gpu` ergänzen. |
| Statt UI das Wartungsmenü | Kiosk wurde beendet oder ist 5× fehlgeschlagen: Menü → 1, oder `sudo systemctl start hector-kiosk`. |
| UI zeigt „Loading Drinks“ | Backend läuft nicht: `systemctl status hector-controller hector-server`, `journalctl -u hector-controller`. |
| Doom-Seite lädt nicht | Die UI wird von `kiosk.sh` über `http://127.0.0.1:8765` ausgeliefert: `journalctl -u hector-kiosk -b` auf „WARNING“ zum `file://`-Fallback prüfen (kein `python3`, alle Ports 8765–8774 belegt?). Fehlen `doom/freedoom1.wad` oder `doom/websockets-doom.wasm`, hat der WebUI-Klon die großen Dateien nicht bekommen. |
| `ModuleNotFoundError`/`RuntimeError: This module can only be run on a Raspberry Pi` im Server-Log | Läuft auf anderer Hardware, oder venv unvollständig: `sudo deploy/provision.sh --skip-apt`. |
| Ventile bewegen sich nicht | I²C aus? `ls /dev/i2c-1`, `i2cdetect -y 1`; Benutzer `hector` in Gruppe `i2c`? |
| LED-Streifen flackert/bleibt dunkel | PWM auf GPIO 18 kollidiert mit dem Onboard-Audio (`snd_bcm2835`). In `config.txt` `dtparam=audio=off` setzen (nur wenn kein 3,5-mm-Ton gebraucht wird). **Ungetestet.** Der Dienst läuft als root. |
| Reboot/Kiosk-Exit aus der UI tut nichts | `journalctl -u hector-controller`: `sudo -n` verweigert? Prüfe `/etc/sudoers.d/hector9000` (`sudo visudo -c`). |
| Statistikseite nicht erreichbar | `systemctl status hector-stats`; Datei `Hector9000/StatsServer.py` muss im Checkout vorhanden sein. |

## Teststand

Container `debian:bookworm` (arm64, Python 3.11.2, gcc 12.2), aufgesetzt per `docker run -i` mit
`tar`-Stream. Das Testskript prüft: `bash -n` und `shellcheck`, `provision.sh --dry-run` (verändert nichts),
`provision.sh --no-systemd` zweimal (Idempotenz), Benutzer/Gruppen/Rechte, sudoers-Regeln (erlaubt und verweigert),
`systemd-analyze verify`, mosquitto (nur Loopback, MQTT-Rundlauf, Websocket-Handshake, Größenlimit),
`hector-maint` und `hector-kiosk-session` (mit Attrappe für `cage`), `migrate-data.sh` (Export → Import),
das venv samt Importen und die hardwarefreien pytest-Läufe (`test_LEDAnimations`, `test_HectorSystem`).

**Ergebnis:** 61 Prüfungen, alle bestanden (die Optionsprüfungen `--nope`, fehlender Wert und `--overlayfs`
ohne Datenpartition wurden wegen eines Quoting-Fehlers im Testskript in einem Mini-Lauf nachgeholt). Der Test hat
vier echte Fehler der Skripte gefunden, die behoben sind (Chromium-Erkennung unter `pipefail`, Mode-Vergleich in
`install_file`, `trap` in `migrate-data.sh`, veraltete mosquitto-Option). Pakete im Container: 465 neue Pakete inkl.
Chromium 156 und cage 0.1.4. `systemd-analyze verify` meldet für alle Units nichts. Python-Spike: mit
`requirements-bookworm.txt` installiert alles; `import Hector9000.LEDStripServer` läuft ohne Fehler,
`Hector9000.HectorHardware` und `conf.hx711` brechen erwartungsgemäß mit
`RuntimeError: This module can only be run on a Raspberry Pi!` ab; `test_LEDAnimations` und `test_HectorSystem`:
24 passed. Mit der alten `requirements.txt` scheitert `RPi.GPIO==0.7.0` beim Bauen unter Python 3.11.

**Nicht getestet (braucht Hardware oder ein echtes System):** `systemctl`-Verhalten im echten Betrieb
(`OnSuccess`/`OnFailure`, `Conflicts` mit `getty@tty1`, PAM-Session), `cage` + Chromium auf dem Touchscreen,
Hardwarezugriff (PCA9685, HX711, GPIO, WS2812-PWM), 32-Bit-ARM (armv7l, Pi 3 mit 32-Bit-Image),
`raspi-config nonint enable_overlayfs`, Bootzeit und RAM-Bedarf auf dem Pi.

## Code-Haken (TODO für die Entwicklung)

Dieses Verzeichnis ändert bewusst keinen bestehenden Python-Code. Was noch fehlt oder sinnvoll wäre:

1. **Konfiguration außerhalb des Repos.** `HectorConfig.config` ist ein Dict im Code; Kalibrierung (`hx711.ref`),
   `valvepositions` und der Block `"system"` lassen sich nur durch Ändern einer git-getrackten Datei setzen
   (das war schon auf dem alten Pi der Update-Konflikt). Vorschlag: am Ende von `HectorConfig.py` eine
   JSON-Datei (`$HECTOR_CONFIG`, Standard `/etc/hector9000/config.json`) rekursiv in `config` mischen.
   Dann reicht dort z. B. `{"hx711": {"ref": 2145}, "system": {"kiosk_exit": [...]}}`.
2. **Datenbankpfad.** `conf/database.py` öffnet `./h9k.db`. Die Units lösen das über
   `WorkingDirectory=/var/lib/hector9000/hector`. Sauberer wäre eine Umgebungsvariable (der Stats-Server hat `HECTOR_DB`).
3. ~~**`Hector9000/StatsServer.py`**~~ erledigt: bindet auf `0.0.0.0:8080`, liest `HECTOR_STATE_DIR`, `HECTOR_DB`,
   `HECTOR_ADMIN_PASSWORD`, `HECTOR_MATRIX_*`, startet selbst keine Prozesse (siehe [`docs/stats.md`](../docs/stats.md)).
4. **`requirements.txt` und `setup.py`** sind veraltet (siehe Kopf von `requirements-bookworm.txt`):
   `RPi.GPIO==0.7.0` baut nicht mehr, `board==0.0.0.post0` ist ein Platzhalter und überdeckt Blinkas `board`,
   `numpy` fehlt, obwohl `conf/hx711.py` es importiert. Nach dem Umstieg die Dateien angleichen.
5. **`kiosk.sh`** liefert die UI über einen lokalen HTTP-Server (`127.0.0.1`, Port ab 8765) aus, auch im alten
   Desktop-Aufbau; das Chromium-Flag `--allow-file-access-from-files` wird nur noch im `file://`-Fallback gesetzt
   (kein `python3` oder alle Ports belegt, dann mit WARNING im Log). Beim Wechsel von `file://` auf `http://`
   werden Theme und LED-Modus im `localStorage` einmal zurückgesetzt (anderer Ursprung).
6. **`start.sh`** wird im schlanken Aufbau nicht benutzt (systemd ersetzt `nohup`); auf dem alten Pi bleibt es.
7. **Pi 5** (nicht Ziel): `RPi.GPIO` durch `rpi-lgpio` ersetzen, Blinka-Neopixel läuft dort über PIO.
8. **Härtung (Stand):** `hector-stats` ist mit `NoNewPrivileges`, `ProtectSystem=strict`, `CapabilityBoundingSet=` und
   Systemaufruf-Filter abgesichert (`systemd-analyze security`: 1,8 „OK“). `hector-controller` darf wegen `sudo` kein
   `NoNewPrivileges`/`PrivateTmp` haben. Server, Controller und LED-Dienst laufen mit `ProtectSystem=full`; strenger
   (`ProtectSystem=strict` + `ReadWritePaths=`) ist möglich, aber auf Hardware ungetestet.
9. ~~**Eine gemeinsame Vertrauensdomäne**~~ erledigt: Der Statistikdienst läuft als eigener Benutzer `hector-stats` (keine Gruppen
   außer `video`, kein sudo, nur er liest `hector.env` über systemd). „Uhr stellen“ läuft über MQTT beim Controller.
   **Restrisiko:** Wer MQTT erreicht (nur `127.0.0.1`), kann `system/setTime` mit einem plausiblen Wert senden. Im schlanken OS ist das
   nur ein Prozess auf dem Gerät selbst; MQTT-Zugriffsrechte pro Benutzer wären nur mit Zugangsdaten für jeden Teilnehmer möglich (Mosquitto kennt
   nur Erlaubnislisten), das wäre eine größere Umstellung (Browser-WebUI braucht dann ein Passwort im Klartext).
10. **`HECTOR_ADMIN_REMOTE=0`** in `/etc/hector9000/hector.env` setzen, wenn das Dashboard nur per SSH-Tunnel
    erreichbar sein soll (Standard: aus dem Netz erreichbar, Klartext-HTTP).
