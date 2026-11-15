# Matrix-Chat-Bot (Benachrichtigungen bei Fehlern)

Hector schickt eine Nachricht in einen Matrix-Raum, wenn etwas kaputtgeht: leere Flasche, Dosierfehler,
ein Dienst fehlt, Pi zu heiß, SD-Karte voll, Unterspannung. Ohne Konfiguration schreibt der Notifier
die Nachrichten nur ins Log. Der Bot **sendet nur**; Befehle aus dem Chat sind vorbereitet, aber nicht gebaut
(siehe unten).

## Einrichten

1. **Bot-Konto** auf einem Homeserver anlegen (eigenes Konto, nicht das private).
2. **Zugangstoken** holen, einmal an der Konsole (das Passwort nicht in die Shell-History schreiben):

       curl -s -X POST https://matrix.example.org/_matrix/client/v3/login \
         -H 'Content-Type: application/json' \
         -d '{"type":"m.login.password","identifier":{"type":"m.id.user","user":"hectorbot"},"password":"…"}'

   In der Antwort steht `access_token`.
3. **Raum** anlegen und den Bot einladen. Der Raum muss **unverschlüsselt** sein: Ende-zu-Ende-Verschlüsselung
   wird nicht unterstützt (der Bot hat keine Schlüssel). Der Bot muss dem Raum **beigetreten** sein
   (Einladung annehmen, z. B. `POST /_matrix/client/v3/join/<raum-id>` mit dem Token).
4. Die Raum-ID (`!abc…:example.org`) und den Token in `/etc/hector9000/hector.env` eintragen
   (Datei mit Rechten `0600`, **nie ins Repository**; `deploy/hector.env.example` zeigt das Format):

       HECTOR_MATRIX_HOMESERVER=https://matrix.example.org
       HECTOR_MATRIX_ROOM=!abc123:example.org
       HECTOR_MATRIX_TOKEN=syt_…
       HECTOR_NOTIFY_LEVEL=warning

5. Stats-Dienst neu starten und im Admin-Dashboard **„Test-Nachricht“** auslösen.

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
  Stunde begrenzt. Danach kommt eine einzige Meldung „weitere werden unterdrückt“. Eigene Prüfungen des Dienstes
  (CPU-Temperatur, Stromversorgung, leere Flaschen) sind davon ausgenommen.
* **Offline?** Nachrichten warten in einer Warteschlange (max. 50, max. 6 Stunden alt) und werden mit
  Backoff wiederholt; HTTP 429 wird mit `retry_after` beachtet. Ein falscher Token oder Raum gilt als
  dauerhafter Fehler und wird im Dashboard angezeigt.

## Erweiterungspfad: Befehle aus dem Chat

Nicht gebaut. Ein späterer eigener Dienst (`MatrixBot`) könnte per `/sync` mitlesen und nur Nachrichten von
einer **festen Liste erlaubter Matrix-IDs** in MQTT-Aktionen übersetzen (`status`, `flasche aufgefüllt`,
`fehler quittieren`). Nie Aktionen ohne Absenderprüfung, nie Dosieren/Neustart aus dem Chat ohne Bestätigung.
Weitere Ausgabewege (E-Mail, ntfy, …) brauchen nur ein Objekt mit `send(text, level, txn)`.
