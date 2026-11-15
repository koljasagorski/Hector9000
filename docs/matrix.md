# Matrix-Chat-Bot (Benachrichtigungen bei Fehlern)

Hector schickt eine Nachricht in einen Matrix-Raum, wenn etwas kaputtgeht: leere Flasche, Dosierfehler,
ein Dienst fehlt, Pi zu heiß, SD-Karte voll, Unterspannung. Ohne Konfiguration schreibt der Notifier
die Nachrichten nur ins Log. Der Bot **sendet nur**; Befehle aus dem Chat sind vorbereitet, aber nicht gebaut
(siehe unten).

## Einrichten im Admin-Dashboard (empfohlen)

Seit dem Reiter **„Chat“** im Admin-Dashboard (`/admin#chat`) braucht es keine Datei und keinen Neustart mehr:

1. Bot-Konto, Raum und Token vorbereiten (Schritte 1 bis 3 unten).
2. Im Reiter „Chat“ **Homeserver**, **Raum-ID** (beginnt mit `!`) und **Token** eintragen, Mindest-Stufe und
   Nachrichtentyp wählen, **Speichern**. Die Änderung gilt sofort.
3. **„Raum beitreten“** nimmt die Einladung des Bots an (der Bot kann erst nach dem Beitritt schreiben), danach
   **„Testnachricht senden“**.

Gespeichert wird in `$HECTOR_STATE_DIR/notify.json` (Rechte `0600`, steht in `.gitignore`, **nie ins Repository**).
Diese Datei hat Vorrang vor den Variablen in `hector.env`; **„Gespeicherte Einstellungen löschen“** entfernt sie,
dann gilt wieder `hector.env`. Der Haken „Fehler in den Matrix-Chat senden“ schaltet den Bot aus, ohne die Werte zu verlieren.

* Der **Token ist nur schreibbar**: der Server schickt ihn nie zurück an den Browser, das Feld bleibt leer
  („gespeichert, leer = behalten“). Er steht auch nicht im Verlauf oder im Log.
* **Achtung Klartext:** Das Dashboard läuft über HTTP. Wer im selben WLAN mitliest, sieht den Token beim Eintragen
  (und das Admin-Passwort bei jedem Aufruf). Die Seite warnt dann. Besser: den Token über einen SSH-Tunnel eintragen
  (`ssh -L 8080:localhost:8080 <benutzer>@<pi>`, dann `http://localhost:8080/admin`) oder in `hector.env`.
* Der Homeserver muss `https://` sein. `http://` (nur für einen Server im eigenen LAN) lässt sich ausschließlich mit
  `HECTOR_MATRIX_ALLOW_HTTP=1` in `hector.env` freischalten, nicht im Dashboard. Weiterleitungen (Redirects) folgt der
  Bot nie, der Token geht also nicht an einen anderen Server.
* Wer sich im Dashboard anmelden kann, kann den Bot also auf einen **beliebigen https-Server** zeigen lassen. Das ist
  gewollt, aber ein Grund mehr für ein starkes Admin-Passwort.

## Einrichten über die Umgebung (Alternative, ohne Dashboard)

1. **Bot-Konto** auf einem Homeserver anlegen (eigenes Konto, nicht das private).
2. **Zugangstoken** holen, einmal an der Konsole (das Passwort nicht in die Shell-History schreiben):

       curl -s -X POST https://matrix.example.org/_matrix/client/v3/login \
         -H 'Content-Type: application/json' \
         -d '{"type":"m.login.password","identifier":{"type":"m.id.user","user":"hectorbot"},"password":"…"}'

   In der Antwort steht `access_token`.
3. **Raum** anlegen und den Bot einladen. Der Raum muss **unverschlüsselt** sein: Ende-zu-Ende-Verschlüsselung
   wird nicht unterstützt (der Bot hat keine Schlüssel). Der Bot muss dem Raum **beigetreten** sein
   (Einladung annehmen: im Dashboard **„Raum beitreten“**, sonst `POST /_matrix/client/v3/join/<raum-id>` mit dem Token).
4. Die Raum-ID (`!abc…:example.org`) und den Token in `/etc/hector9000/hector.env` eintragen
   (Datei mit Rechten `0600`, **nie ins Repository**; `deploy/hector.env.example` zeigt das Format):

       HECTOR_MATRIX_HOMESERVER=https://matrix.example.org
       HECTOR_MATRIX_ROOM=!abc123:example.org
       HECTOR_MATRIX_TOKEN=syt_…
       HECTOR_NOTIFY_LEVEL=warning

5. Stats-Dienst neu starten und im Admin-Dashboard (Reiter „Chat“) **„Testnachricht senden“** auslösen. Beim ersten
   Speichern im Dashboard wird der Token aus `hector.env` übernommen, man muss ihn nicht neu eintippen.

| Variable | Standard | Bedeutung |
|---|---|---|
| `HECTOR_MATRIX_HOMESERVER` | – | Adresse; `https://` ist Pflicht (`HECTOR_MATRIX_ALLOW_HTTP=1` erlaubt `http://`, nur fürs lokale Testen) |
| `HECTOR_MATRIX_ROOM` | – | Raum-ID |
| `HECTOR_MATRIX_TOKEN` | – | Zugangstoken des Bots |
| `HECTOR_NOTIFY_LEVEL` | `warning` | `info`, `warning`, `error`, `critical`: alles darunter wird nicht gesendet |
| `HECTOR_MATRIX_MSGTYPE` | `m.text` | `m.notice` ist für Bots üblich und löst bei vielen Clients keine Benachrichtigung aus |

Der Token wird weder geloggt noch im Admin-Dashboard angezeigt (`Notifier.status()` enthält ihn nie).

## Verhalten

* `notify()` blockiert nie und wirft nie: ein ausgefallenes Netz darf keinen Cocktail aufhalten.
* **Gleiche Meldungen** werden höchstens einmal pro Stunde gesendet (Dedup), hängt eine Meldung am Zustand
  (z. B. „Flasche leer“), gibt es danach eine „wieder in Ordnung“-Nachricht, aber nur, wenn die erste
  wirklich rausging.
* **Fehlermeldungen, die aus MQTT stammen** (Dosierfehler, Dienst offline, hängende Dosierung) sind „nicht
  vertrauenswürdig“, weil jeder im Netz sie senden kann: sie werden auf 300 Zeichen gekürzt und auf höchstens 20 pro
  Stunde begrenzt. Danach kommt eine einzige Meldung „weitere werden unterdrückt“, und sobald wieder Meldungen
  durchgehen, eine Zusammenfassung mit Anzahl und Quellen. Echte Controller-Fehler lassen sich von gefälschten nicht
  unterscheiden, solange der Broker anonym ist; im Admin-Dashboard stehen deshalb immer **alle** Fehler. Eigene Prüfungen des Dienstes
  (CPU-Temperatur, Stromversorgung, leere Flaschen) sind davon ausgenommen.
* **Offline?** Nachrichten warten in einer Warteschlange (max. 50, max. 6 Stunden alt) und werden mit
  Backoff wiederholt; HTTP 429 wird mit `retry_after` beachtet. Ein falscher Token oder Raum gilt als
  dauerhafter Fehler und wird im Dashboard angezeigt.

## Erweiterungspfad: Befehle aus dem Chat

Nicht gebaut. Ein späterer eigener Dienst (`MatrixBot`) könnte per `/sync` mitlesen und nur Nachrichten von
einer **festen Liste erlaubter Matrix-IDs** in MQTT-Aktionen übersetzen (`status`, `flasche aufgefüllt`,
`fehler quittieren`). Nie Aktionen ohne Absenderprüfung, nie Dosieren/Neustart aus dem Chat ohne Bestätigung.
Weitere Ausgabewege (E-Mail, ntfy, …) brauchen nur ein Objekt mit `send(text, level, txn)`.
