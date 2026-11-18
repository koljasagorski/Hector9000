# Hector9000 als fertiges SD-Karten-Image

Ein Image, das man mit dem Raspberry Pi Imager auf eine Karte schreibt und das beim ersten Start fertig ist:
Raspberry Pi OS Lite (Debian 12 „bookworm“, 64-Bit) mit allem aus [`deploy/README.md`](../README.md) schon installiert
(Backend, WebUI, Python-venv, mosquitto nur lokal, Dienste, Kiosk, sudo-Regeln). Die Skripte hier bauen und prüfen es;
nichts davon braucht einen Raspberry Pi.

> **Stand:** gebaut und geprüft **im Container und unter QEMU**, **nicht auf echter Hardware**. Ob Ventile, Pumpe, Waage,
> LED-Streifen und der Touchscreen mit dem Image laufen, ist offen (der Pi war beim Bauen nicht greifbar). Erster Lauf
> deshalb **auf einer zweiten Karte**, die Karte im Hector bleibt der Rückweg.

## Was im Image ist, was nicht

| | |
|---|---|
| Basis | Raspberry Pi OS Lite arm64 (bookworm, 2026-10-06), Kernel 6.12 |
| Partitionen | `p1` Boot (FAT, 512 MiB, unverändert), `p2` System (ext4, fest 3584 MiB), `p3` Daten `HECTORDATA` (ext4, 512 MiB, **wächst beim ersten Start bis zum Kartenende**) |
| Größe | 4616 MiB (Karte mit **mindestens 8 GB** nehmen) |
| Code | `/opt/hector9000/backend` (Branch `feature/congress-webui`) und `/opt/hector9000/webui` (Branch `feature/congress-design`) als Git-Klone mit `origin` auf GitHub, venv `/opt/hector9000/venv` mit fertig gebauten Paketen |
| Dienste | `hector-server`, `-controller`, `-led`, `-stats`, `-kiosk`, `-maint`, `hector-firstboot`, mosquitto (nur `127.0.0.1`) |
| Schreibschutz | `hector-overlay` ist installiert, im Image **aus** (`sudo hector-overlay on`) |
| entfernt | Bauwerkzeuge, `modemmanager`, `triggerhappy`, `dphys-swapfile`, `man-db`, Handbücher, Dokumentation (Lizenztexte bleiben), fremde Sprachen außer `de`/`en`, das SysV-Skript `resize2fs_once` |
| **nicht** im Image | Passwörter, Zugangsdaten, WLAN, Schlüssel, SSH-Host-Schlüssel, `machine-id`, Logs, Shell-Verlauf, Matrix-Token (Ausnahme: die [Variante mit eingebautem WLAN/SSH](#variante-mit-eingebautem-wlan-und-ssh-login), die es nur auf ausdrücklichen Wunsch gibt) |

Der Platzhalter-Benutzer `pi` aus der Basis bleibt **ohne Passwort** (gesperrt) bestehen, weil der Imager den Benutzer mit
der UID 1000 beim Anpassen umbenennt. Der Dienstbenutzer `hector` hat bewusst eine andere UID. **Ohne Imager-Anpassung gibt es
keinen Login** (kein Standardpasswort). Das Wartungsmenü auf tty1 braucht keinen Login: geheimer Button der Oberfläche (5× oben links), siehe `deploy/README.md`.

**Kein Umbenennungs-Dialog.** Die Basis startet ohne `userconf.txt` einen interaktiven Dialog („Please enter new username“) auf VT8, schaltet die Konsole
dorthin und erreicht `multi-user.target` nie. Am Touchscreen ohne Tastatur wäre der Hector damit nicht benutzbar (der Dialog verdeckt den Kiosk). Das Image
schaltet das per Drop-in ab (`userconfig-hector.conf`): `userconfig.service` läuft nur, wenn `userconf` oder `userconf.txt` auf der Boot-Partition liegt
(Format `name:passwort-hash`, dann ohne Dialog). Gefunden hat das der QEMU-Test; der Container-Test hatte den Dienst übersprungen.

## Variante mit eingebautem WLAN und SSH-Login

Für ein Gerät, das ohne Imager-Anpassung im bekannten WLAN landen und per SSH erreichbar sein soll, kann `build-image.sh` beides fest einbauen:
`--access-file DATEI`. Ohne die Option ändert sich nichts (Standard: keine Zugangsdaten im Image).

**Das Repository ist öffentlich: die Zugangsdaten gehören nie hinein.** Die Datei liegt außerhalb des Repositorys (z. B. `~/.config/hector9000-image/access.env`,
`chmod 600`), Vorlage: [`access.env.example`](access.env.example). Sie wird nur gelesen (nicht ausgeführt); `deploy/image/access.env` und `*.nmconnection` stehen in der `.gitignore`.

| Schlüssel | Wirkung im Image |
|---|---|
| `WIFI_SSID`, `WIFI_PSK` | NetworkManager-Profil `/etc/NetworkManager/system-connections/hector-wifi.nmconnection` (0600 root, WPA-PSK, `autoconnect=true`): der Pi verbindet sich selbst, sobald das Netz in Reichweite ist |
| `WIFI_COUNTRY` (Standard `DE`) | `cfg80211.ieee80211_regdom=<Land>` in `cmdline.txt` (wie `raspi-config`); dazu `hector-wifi-unblock.service` (`rfkill unblock wifi` vor NetworkManager) |
| `SSH_USER`, `SSH_PASSWORD` | Benutzer (`useradd --badname`, weil Debian Großbuchstaben sonst ablehnt; Groß-/Kleinschreibung zählt beim Login), Passwort als Crypt-Hash in `/etc/shadow`, `ssh.service` aktiviert, Passwort-Login ausdrücklich an (`sshd_config.d/10-hector-access.conf`). Host-Schlüssel entstehen wie immer erst beim ersten Start (`regenerate_ssh_host_keys.service`) |
| `SSH_SUDO` (Standard `1`) | Der Benutzer ist in `sudo` (mit seinem Passwort, kein `NOPASSWD`), dazu `adm` und `netdev`; `0` = ohne |

`WIFI_*` und `SSH_*` sind unabhängig voneinander. Der Platzhalter `pi` (UID 1000, vom Imager umbenennbar) und der Dienstbenutzer `hector` bleiben unverändert; der Login bekommt die nächste freie UID.
`/etc/hector9000/image-version` hält fest, was eingebaut wurde (`access: preset`, SSID, Benutzername, **nie** ein Passwort); `check-image.sh`, `boot-check.sh` und der QEMU-Test
lesen das und prüfen dann das Gegenteil der Standardvariante (Profil vorhanden, `ssh` aktiv, Benutzer mit Hash). Mit `HECTOR_ACCESS_FILE=<Datei>` vergleicht `check-image.sh`
außerdem den Hash und den PSK mit den Soll-Werten.

Folgen, die man kennen muss: Wer das Image oder die Karte hat, hat die Zugangsdaten (SSID/PSK stehen im Klartext in der Verbindungsdatei). Mit einem kurzen, bekannten Passwort ist ein SSH-Login im
fremden WLAN angreifbar (Brute Force, auf einem Kongress-WLAN auch von anderen Teilnehmern); Passwort nach dem Aufbau ändern (`passwd`) oder auf Schlüssel umstellen. Das Image
**nicht veröffentlichen und nicht hochladen** (auch nicht als Release-Anhang); das Standard-Image ohne `--access-file` bleibt die Variante zum Weitergeben. Ob die Verbindung
zum WLAN auf echter Hardware klappt, lässt sich ohne Pi nicht prüfen (QEMU hat kein WLAN-Gerät): Profil, Land, Dienste und der Benutzer werden geprüft, der Verbindungsaufbau nicht.
Verwendet das Netz WPA3-SAE oder eine Anmeldeseite (Captive Portal), reicht das Profil nicht.

## Welcher Pi?

Das Image ist **arm64**: Pi 3, 3 B+, 3 A+, 4, 400, Zero 2 W, Compute Module 3/4 und Pi 2 **ab Revision 1.2** (BCM2837).
Ein Pi 2 v1.1, Pi 1 und Pi Zero laufen nur mit 32-Bit. Ein 32-Bit-Image ist **nicht gebaut** (auf Apple Silicon ginge das
nur mit `qemu-user`). Das Modell im Hector prüfen: `cat /proc/device-tree/model` auf dem alten Pi. Der Pi 5 ist nicht das Ziel
(`RPi.GPIO`).

## Auf die Karte schreiben

1. Raspberry Pi Imager → *Betriebssystem wählen* → ganz unten **Eigenes Image verwenden** → `hector9000-….img` (oder `.img.xz`).
2. *Einstellungen bearbeiten* (OS-Anpassung): Hostname, **eigener** Benutzer mit Passwort (nicht `hector`, nicht `pi`), WLAN, SSH nach Bedarf.
   Der Imager legt dafür eine `custom.toml` auf die Boot-Partition; `hector-firstboot` wendet sie beim ersten Start an und löscht sie danach
   (sie kann einen Passwort-Hash enthalten).
3. Karte in den Pi. **Der erste Start dauert länger**: `hector-firstboot` vergrößert die Datenpartition auf das Kartenende, legt die Datenordner
   an und wendet die Imager-Einstellungen an. Die Systempartition behält ihre Größe.
4. Admin-Passwort des Dashboards: erzeugt der Statistikdienst beim ersten Start. Anzeigen am Gerät im Wartungsmenü (Punkt 7) oder
   `sudo hector-maint --password`; Adresse `http://<ip>:8080/admin`, Benutzer `admin`.

Nicht benutzen: die Imager-Einstellung „Benutzer `hector`“ (kollidiert mit dem Dienstbenutzer).

## Bauen

Auf einem arm64-Rechner mit Docker (getestet: Apple Silicon, colima, 4 CPUs, 6 GB RAM, mindestens 15 GB freier Platz in der VM).

```bash
docker build -t hector-image-builder -f deploy/image/Dockerfile.builder deploy/image
docker volume create hector-img-work
# Basis: Raspberry Pi OS Lite arm64 (bookworm) als .img nach /work/base.img im Volume entpacken (Prüfsumme der Download-Seite vergleichen)
# Quellen als Git-Bundles nach /work/src/{backend,webui}.bundle
git bundle create backend.bundle feature/congress-webui           # im Backend-Repo
git bundle create webui.bundle feature/congress-design            # im WebUI-Repo (Bundles per tar | docker run -i ins Volume)

docker run --rm --privileged -v hector-img-work:/work hector-image-builder bash -c "$(cat deploy/image/build-image.sh)" _ \
    --base /work/base.img --out /work/out/hector9000.img \
    --backend /work/src/backend.bundle --webui /work/src/webui.bundle
# Variante mit eingebautem WLAN/SSH (siehe oben): die Datei per tar | docker run -i ins Volume schicken, dann zusätzlich
#   --access-file /work/access.env      (Image dann unter anderem Namen ablegen, z. B. /work/out/hector9000-ACCESS.img, und nicht weitergeben)
```

`build-image.sh --help` zeigt die Optionen (Größen, Branches, `origin`-URLs, Hostname, `--keep-build-tools`). Der Ablauf steht im Kopf
des Skripts: Partitionen anlegen → mounten → chroot → `provision.sh --image` → `overlayroot` installieren → abspecken → Geheimnisse und
Maschinenmerkmale entfernen → fstab/cmdline.txt → `fstrim`, `e2fsck`, Prüfsumme.

Hinweise aus der Praxis:

- Bind-Mounts aus `/private/tmp` in die colima-VM funktionieren nicht: Daten per `tar | docker run -i …` ins benannte Volume schicken.
- Die Loop-Geräte legt das Skript selbst an (`mknod`), Container sehen später erzeugte Geräte nicht. Hängen nach einem Abbruch noch Loops, hilft
  `losetup -D` im privilegierten Container.
- Die VM-Platte läuft bei 4,6-GB-Images schnell voll (Basis + Ergebnis + entpacktes Root-Dateisystem): vorher mit `colima ssh -- df -h /` nachsehen.
- Ausgeliefert wird `xz -T0 hector9000.img` samt Prüfsumme (`.sha256`). Das Image gehört **nicht** ins Repository.

## Prüfen (alles ohne Pi)

| Skript | Was es prüft | Dauer |
|---|---|---|
| `check-image.sh IMAGE` | **Statisch, ohne Boot:** Partitionstabelle (Disk-ID, Ausrichtung), `cmdline.txt` (keine `init=…/firstboot`, `root=PARTUUID` passt zur Disk-ID, kein `overlayroot`), `config.txt` (I²C, SPI), Kernel/initramfs vorhanden, **initramfs enthält** den `overlayroot`-Hook, `fsck` und das Overlay-Modul, fstab (PARTUUID, `nosuid,nodev,noexec,nofail`), Benutzer, Rechte, sudoers, Units gleich dem Repo, Pakete, Git-Klone, **Suche nach Geheimnissen** (private Schlüssel, Token-Muster, Passwörter in `hector.env`; ein frei wählbarer Zusatz-Suchausdruck lässt sich mit `HECTOR_CHECK_FORBIDDEN` übergeben) | 1 Minute |
| `boot-test.sh all` | **Das System des Images läuft im Container** (eigenes systemd in einem chroot, Hardware-Server durch den Simulator ersetzt, `CAP_SYS_TIME` entzogen): Dienste und ihre Benutzer, MQTT nur auf `127.0.0.1`, Dashboard-Login (200/401), Datei- und Ordnerrechte, Mount-Optionen der Datenpartition, sudo-Regeln (erlaubt/verboten), Sandbox des Dashboards, `systemd-analyze verify`, MQTT-Rundlauf bis zum `sudo date`-Aufruf von „Uhr stellen“ | 2 Minuten |
| `qemu-test.sh` | **Der Boot mit echtem Raspberry-Pi-Kernel und -initramfs** in QEMU (Pi 3B) auf einer 8-GiB-Karte, mit einer `custom.toml` wie vom Imager: Root per PARTUUID, fstab-Mounts, `hector-firstboot` vergrößert die Datenpartition und wendet die `custom.toml` an (Benutzer umbenannt, Hostname, Datei gelöscht, kein Umbenennungs-Dialog, `getty@tty1` bleibt aus), Dienste; dann `hector-overlay on` → Neustart (Root ist ein Overlay, Daten bleiben) → `off` → Neustart | rund 3 Minuten (Software-Emulation, gemessen auf Apple Silicon) |

`boot-test.sh` und `qemu-test.sh` arbeiten mit einem Image im Volume `hector-img-work` (Standardpfad `/work/out/hector9000-test.img`, anderer Pfad als Argument).
`boot-test.sh prepare` entpackt dafür die Partitionen (rund 2,3 GB zusätzlich), `qemu-test.sh` legt eine 8-GiB-Kopie an (rund 2,3 GB auf der Platte).

**Ergebnis des letzten Laufs** (Image aus Backend-Commit `57712c1`, WebUI `c830fc5`): `check-image.sh` 109 bestanden, 0 fehlgeschlagen (1 Hinweis: kein
Zusatzmuster in `HECTOR_CHECK_FORBIDDEN`; mit einem Zusatzmuster 110 bestanden, 0 Hinweise), `boot-test.sh all` 66 bestanden, 0 fehlgeschlagen, `qemu-test.sh` alle drei Phasen bestanden (33 + 14 + 7 Prüfungen).
Ohne Hardware erwartet fehlgeschlagen sind im QEMU-Boot `hector-server`, `hector-led`, der Kiosk (kein Display) und `rpi-eeprom-update`.
Der initramfs meldet „Warning: fsck not present, so skipping root file system“; das ist dort normal, die Prüfung übernimmt danach `systemd-fsck-root`
(im Boot-Log zu sehen, ebenso `systemd-fsck@` für Boot- und Datenpartition; bei eingeschaltetem Schreibschutz wird das Root-Dateisystem nicht geprüft).

**Was diese Tests nicht ersetzen:** Raspberry-Pi-Hardware (Ventile, Pumpe, HX711-Waage, PCA9685, WS2812-PWM auf GPIO 18, Touchscreen/GPU),
Wi-Fi, Bluetooth, Timing und Speicherbedarf auf einem echten Pi, echte SD-Karten (Stromausfall, Verschleiß). Auch ungetestet: dass `cage` und Chromium auf dem
Touchscreen starten (QEMU hat kein Display; der Kiosk wird dort nur gestartet und scheitert erwartungsgemäß) und der Imager selbst (die `custom.toml` im Test ist
von Hand nach seinem Format geschrieben, ein echter Imager-Lauf fehlt).

## Wartung und Updates am laufenden Gerät

- **Update des Codes:** `cd /opt/hector9000/backend && sudo git pull && sudo deploy/provision.sh --skip-apt` (WebUI: `git pull` in `/opt/hector9000/webui`),
  danach `sudo systemctl restart hector.target`. Baut ein Update das venv neu, vorher `sudo apt-get install build-essential python3-dev libffi-dev`
  (im Image entfernt) und danach wieder entfernen. Bei eingeschaltetem Schreibschutz erst `sudo hector-overlay off` und neu starten.
- **Schreibschutz gegen Stromausfall:** siehe [`deploy/README.md`](../README.md#schreibschutz-stromausfall-und-datenpartition). Die Datenpartition bleibt
  dabei beschreibbar; `hector-overlay` verhindert die Falle von `raspi-config enable_overlayfs` (`recurse=1` würde die Daten mit überlagern).
- **Daten übernehmen** vom alten Pi: `deploy/migrate-data.sh` (siehe `deploy/README.md`, Schritt 3).

## Grenzen und offene Punkte

- Auf Hardware ungetestet (siehe oben); das gilt auch für die Mengenangaben (Bootzeit, RAM).
- Der Imager schlägt inzwischen **Debian 13 „Trixie“** vor. Das Image baut auf **bookworm**; Trixie ist nicht geprüft (Python 3.13, ob die
  Hardware-Pakete dort laufen, ist offen).
- Das WebUI und das Backend werden beim Bauen von GitHub-Zweigen übernommen. Solange `feature/*` nicht nach `master` gemergt ist, zeigen die
  `origin`-Klone im Image auf diese Zweige (Standard der Skripte).
- Das Admin-Dashboard läuft über Klartext-HTTP und ist im Netz erreichbar (`HECTOR_ADMIN_REMOTE=0` in `/etc/hector9000/hector.env` für nur lokal).
- 32-Bit-Variante (alter Pi ohne 64-Bit): nicht gebaut.
