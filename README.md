# Weihnachts-Gutscheine als Docker-Container

## Start

1. Docker und Docker Compose installieren.
2. In `docker-compose.yml` den Wert `SESSION_SECRET` durch einen langen zufaelligen Wert ersetzen.
3. Optional die `IOBROKER_URL` anpassen.
4. Im Projektordner starten:

```bash
docker compose up -d --build
```

Die Anwendung ist danach unter `http://SERVER-IP:8080` erreichbar.

## Erste Anmeldung

- `Jan` oder `Kim` auswählen.
- Beim ersten Login ein Passwort vergeben und bestätigen.
- Das Passwort wird serverseitig als PBKDF2-SHA256-Hash in SQLite gespeichert.
- Das Passwort kann nicht über die Webseite ausgelesen werden.

## Datenhaltung

Die Daten liegen in `./data/gutscheine.db`. Das Verzeichnis ist als Docker-Volume eingebunden und bleibt bei Container-Neustarts erhalten.

## ioBroker

Nach jeder erfolgreichen Einlösung werden zentral synchronisiert:

- `javascript.0.Adventskalender.gutscheine`
- `javascript.0.Adventskalender.janBetrag`
- `javascript.0.Adventskalender.kimBetrag`

Falls ioBroker nicht erreichbar ist, bleibt die Einlösung trotzdem sicher in SQLite gespeichert. Die Weboberfläche zeigt den Synchronisationsstatus an.

## Zuruecksetzen

Wenn die Passwoerter komplett neu eingerichtet werden sollen, Container stoppen und die Datei `data/gutscheine.db` sichern bzw. entfernen. Dadurch werden auch die eingelösten Codes gelöscht.
