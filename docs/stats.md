# Statistik-Seite und Admin-Dashboard

Ein kleiner Dienst neben Server, Controller und LED-Server: `Hector9000/StatsServer.py`.
Er hört auf MQTT, schreibt alles in eine SQLite-Datei (`stats.db`) und liefert zwei Webseiten.

    MQTT  ->  StatsRecorder  ->  stats.db  ->  HTTP (Port 8080)
                                  |
                     Watchdog --> Notifier --> Matrix-Chat (optional, siehe matrix.md)

| Adresse | Zweck | Login |
|---|---|---|
| `http://<hector>:8080/` | **öffentliche Statistik**: Zahlen, Rekorde, Diagramme, Live-Anzeige | nein, nur lesen |
| `http://<hector>:8080/admin` | **Admin-Dashboard** | ja (HTTP Basic, Benutzer `admin`) |
| `/api/stats` | dieselben Daten wie die öffentliche Seite als JSON | nein |
| `/api/admin/overview`, `/api/admin/export?table=…` (CSV), `POST /api/admin/action` | Admin-API | ja |
| `/healthz` | `ok`, für Überwachung | nein |

Die öffentliche Seite zeigt **keine** Fehlertexte, IP-Adressen oder Systemdetails, nur Zähler.

## Starten

    python3 Hector9000/StatsServer.py            # echter Betrieb (braucht den MQTT-Broker)
    python3 Hector9000/StatsServer.py --demo     # Fake-Daten, kein MQTT, zum Ansehen der Seiten

Im schlanken OS startet `deploy/systemd/hector-stats.service` den Dienst. Auf dem alten Pi
(`start.sh` darf nicht angefasst werden) gibt es dafür eine eigene Unit, siehe
[HANDOVER.md](HANDOVER.md).

## Umgebung (alles optional)

| Variable | Standard | Bedeutung |
|---|---|---|
| `HECTOR_STATE_DIR` | `.` | hier liegen `stats.db` und `admin_password` |
| `HECTOR_STATS_PORT` / `HECTOR_STATS_BIND` | `8080` / `0.0.0.0` | Port und Adresse |
| `HECTOR_ADMIN_PASSWORD` | erzeugt | sonst wird einmalig ein Passwort erzeugt und in `$HECTOR_STATE_DIR/admin_password` gespeichert (Rechte 0600). Ein selbst gesetztes Passwort sollte mindestens 12 Zeichen haben (sonst Warnung im Log) |
| `HECTOR_ADMIN_REMOTE` | `1` | `1`: `/admin` ist aus dem Netz erreichbar. `0`: nur von diesem Gerät aus (`127.0.0.1`), z. B. per `ssh -L 8080:localhost:8080 hector@<pi>`. Die öffentliche Seite bleibt immer offen |
| `HECTOR_MQTT_HOST` | `localhost` | Broker |
| `HECTOR_DB` | `$HECTOR_STATE_DIR/h9k.db`, dann `./h9k.db` | Hardware-DB, wird nur gelesen (Flaschennamen) |
| `HECTOR_EXPECT_PROCESSES` | `server,controller,led` | Prozesse, die der Watchdog erwartet (`""` = nicht prüfen) |
| `HECTOR_ENV_FILE` | `/etc/hector9000/hector.env` | `KEY=VALUE`-Datei, ergänzt die Umgebung (bereits gesetzte Werte gewinnen) |
| `HECTOR_MATRIX_*`, `HECTOR_NOTIFY_LEVEL` | – | Chat-Bot, siehe [matrix.md](matrix.md) |

Das erzeugte Admin-Passwort steht in `admin_password`. Es gehört **nicht** ins Repository
(`.gitignore` schließt die Datei aus).

## Was aufgezeichnet wird

Der Controller sendet zu jedem Getränk Ereignisse (`Hector9000/event/dose`, `dose_done`,
`error`, `doom`) und einen Online-Status (`Hector9000/status/<dienst>`, retained, mit Last Will).
Der Recorder kennt auch den alten Controller (`doseDrink`, `progress end`). Daraus entstehen unter
anderem: Getränke gesamt/heute/gestern, Sessions, Rekorde, Dosierzeiten, Verbrauch je Zutat,
Verteilung nach Stunde/Wochentag (Heatmap), 60 Tage / 12 Wochen / 24 Stunden, Wartung
(Reinigen/Trocknen), Fehlerzähler, Doom-Spielzeit, Maschinenzustand.

* Die **Alkohol-Schätzung** beruht auf einer groben ABV-Tabelle und ist nur eine Schätzung.
* **Flaschenfüllstände** sind erst nach „Aufgefüllt“ im Admin-Bereich bekannt (Standard 700 ml);
  vorher zeigt das Dashboard keine Zahl, die es nicht wissen kann.
* Die Erfolgsquote zählt nur beendete Dosierungen. Hängende Dosierungen werden nach 150 s abgebrochen.

## Admin-Dashboard

Reiter: Übersicht, Fehler, Flaschen, Aktionen, System, Verlauf. Aktionen: Neustart, Kiosk beenden,
Reinigen, Trocknen, Ventile öffnen/schließen, LED-Modus, Flasche aufgefüllt/Kapazität,
Fehler quittieren, Test-Nachricht an den Chat und **Uhr stellen** (der Pi hat keine Echtzeituhr;
ohne Netz geht die Uhr falsch). Gefährliches (Neustart, Reinigen, …) braucht einen Doppeltipp und
wird **während einer Dosierung abgelehnt** (Ventile schließen geht immer).

`Uhr stellen` ruft `sudo -n date -u -s @<epoche>` auf. Dafür braucht der Dienstbenutzer eine
sudoers-Regel (`deploy/sudoers.d/hector9000`, genau 10 Ziffern, ohne Wildcard); auf dem alten Pi
ist `NOPASSWD` ohnehin gesetzt.

## Sicherheit

* **HTTP ist unverschlüsselt**, das Passwort geht im Klartext (Basic) durchs Netz. Nur in einem
  vertrauenswürdigen Netz betreiben, sonst einen Reverse-Proxy mit TLS davorsetzen oder
  `HECTOR_STATS_BIND=127.0.0.1` und nur lokal nutzen.
* 5 Fehlversuche pro Minute und IP sperren den Login kurz; zusätzlich gilt ein Gesamtlimit von 20 Fehlversuchen pro
  Minute für alle Adressen zusammen (gegen Raten aus vielen Adressen). Das Gesamtlimit sperrt **Fremde**: ein Gerät,
  das sich in der letzten Stunde erfolgreich angemeldet hat, und dieses Gerät selbst (`127.0.0.1`, SSH-Tunnel) sind
  davon ausgenommen, der echte Admin wird also nicht ausgesperrt. Ein neues Gerät muss dann eine Minute warten.
* **Auf einem fremden Netz (Kongress-WLAN)** `HECTOR_ADMIN_REMOTE=0` setzen und den Tunnel per SSH nutzen. Wer im
  selben WLAN mitliest, sieht sonst das Passwort bei jedem Abruf und kann damit Neustart, Ventile und Uhr bedienen.
* Verbindungen sind begrenzt (32 gleichzeitig, danach `503`; eine Anfrage darf höchstens 30 s dauern, ein negatives
  oder zu großes `Content-Length` wird mit `400`/`413` abgelehnt). Die systemd-Unit setzt zusätzlich `MemoryMax`,
  `TasksMax` und `LimitNOFILE`.
* **Obergrenzen gegen Fluten über MQTT** (der Broker ist auf dem alten Pi anonym): Pro Minute werden höchstens 40
  Dosierungen, 60 Fehler und 120 sonstige Ereignisse aufgezeichnet. Die Tabellen behalten höchstens die neuesten
  20 000 Dosierungen, 100 000 Zutatenzeilen, 5 000 Fehler und 5 000 Ereignisse (stündliches Aufräumen); bei normaler
  Nutzung wird das nie erreicht. Die Statistikseite zeigt dann die letzten 20 000 Drinks.
* `POST` verlangt den Header `X-Hector-Admin: 1`, JSON als Content-Type und einen passenden
  `Origin` (gegen fremde Webseiten, die Aktionen auslösen wollen).
* Statische Dateien kommen aus einer Whitelist (`/static/…`), kein Pfad nach oben, keine Punktdateien.
  CSV-Export schützt gegen Formel-Einschleusung. Antworten haben CSP- und Sicherheits-Header.
* **Der MQTT-Broker ist auf dem alten Pi anonym**: wer das Netz erreicht, kann auch ohne Admin-Login
  Neustart und Dosierung auslösen. Im schlanken OS lauscht mosquitto nur auf `127.0.0.1`.

## Tests

    python -m pytest tests/test_Stats.py tests/test_StatsServer.py tests/test_Notifier.py \
        tests/test_Watchdog.py tests/test_ControllerEvents.py tests/test_HectorConfig.py

(Kein Broker nötig; die älteren Tests benötigen einen lokalen MQTT-Broker.)
