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
| **nicht** im Image | Passwörter, Zugangsdaten, WLAN, Schlüssel, SSH-Host-Schlüssel, `machine-id`, Logs, Shell-Verlauf, Matrix-Token |

Der Platzhalter-Benutzer `pi` aus der Basis bleibt **ohne Passwort** (gesperrt) bestehen, weil der Imager den Benutzer mit
der UID 1000 beim Anpassen umbenennt. Der Dienstbenutzer `hector` hat bewusst eine andere UID. **Ohne Imager-Anpassung gibt es
keinen Login** (kein Standardpasswort). Das Wartungsmenü auf tty1 braucht keinen Login: geheimer Button der Oberfläche (5× oben links), siehe `deploy/README.md`.

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
| `qemu-test.sh` | **Der Boot mit echtem Raspberry-Pi-Kernel und -initramfs** in QEMU (Pi 3B) auf einer 8-GiB-Karte: Root per PARTUUID, fstab-Mounts, `hector-firstboot` vergrößert die Datenpartition, Dienste; dann `hector-overlay on` → Neustart (Root ist ein Overlay, Daten bleiben) → `off` → Neustart | 15–30 Minuten (Software-Emulation) |

`boot-test.sh` und `qemu-test.sh` arbeiten mit einem Image im Volume `hector-img-work` (Standardpfad `/work/out/hector9000-test.img`, anderer Pfad als Argument).
`boot-test.sh prepare` entpackt dafür die Partitionen (rund 2,3 GB zusätzlich), `qemu-test.sh` legt eine 8-GiB-Kopie an (rund 2,3 GB auf der Platte).

**Was diese Tests nicht ersetzen:** Raspberry-Pi-Hardware (Ventile, Pumpe, HX711-Waage, PCA9685, WS2812-PWM auf GPIO 18, Touchscreen/GPU),
Wi-Fi, Bluetooth, Timing und Speicherbedarf auf einem echten Pi, echte SD-Karten (Stromausfall, Verschleiß).

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
