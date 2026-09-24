# Weihnachts-Gutscheine als Docker-Container

## Start in Portainer

Der Stack verwendet ein benanntes Docker-Volume. Dadurch ist kein lokaler `./data`-Ordner erforderlich und der Bind-Mount-Fehler in Portainer wird vermieden.

1. In `docker-compose.yml` den Wert `SESSION_SECRET` durch einen langen eigenen Zufallswert ersetzen.
2. `IOBROKER_URL` anpassen, falls ioBroker unter einer anderen Adresse erreichbar ist.
3. Den Stack in Portainer aus dem Git-Repository deployen oder aktualisieren.
4. Die Anwendung ist danach unter `http://SERVER-IP:8080` erreichbar.

Alternativ auf dem Docker-Host:

```bash
docker compose up -d --build
```

## Benutzer-Login

- Beim ersten Login wird ein frei waehlbarer Benutzername eingegeben und ein Passwort selbst festgelegt.
- Passwoerter werden serverseitig als PBKDF2-SHA256-Hash in SQLite gespeichert.
- Gutschein-Codes werden serverseitig geprueft und koennen nur einmal eingeloest werden.

## Admin-Bereich

1. Auf der Login-Seite auf **Admin** klicken.
2. Beim ersten Aufruf ein Admin-Passwort festlegen.
3. Im Admin-Bereich stehen zur Verfuegung:
   - Uebersicht aller vorhandenen Codes
   - Uebersicht aller eingeloesten Codes
   - Summen pro Benutzer
   - Besitzername, Betrag und Beschreibung vorhandener Codes bearbeiten
   - neue Codes mit frei wählbarem Benutzernamen anlegen
   - Einloesungen rueckgaengig machen
   - erneute Synchronisierung mit ioBroker

Der eigentliche Code bleibt unverändert; Besitzername, Betrag und Beschreibung können geändert werden. Neue Codes können mit einem frei wählbaren Besitzernamen angelegt werden.

## ioBroker

Die Kommunikation ist optional. In `docker-compose.yml` steuert diese Variable den Betrieb:

```yaml
IOBROKER_ENABLED: "false"
```

Für die Aktivierung auf `"true"` setzen und `IOBROKER_URL` prüfen. Wenn sie deaktiviert ist, bleiben alle Daten vollständig lokal in SQLite; es werden keine ioBroker-Aufrufe ausgeführt.

Der Gutscheinbestand wird als JSON-Text an den Datenpunkt gesendet. Der REST-Aufruf verwendet ausdruecklich `type=string`, damit ioBroker die Liste nicht als Objekt behandelt.

Synchronisierte Datenpunkte:

- `javascript.0.Adventskalender.gutscheine` - JSON-String mit allen eingelösten Gutscheinen
- `javascript.0.Adventskalender.betraege` - JSON-String mit der Summe je frei gewähltem Benutzer

Beispielinhalt von `betraege`:

```json
{"Alex":1.2,"Mia":4.5,"Chris":0}
```

Beide Datenpunkte müssen in ioBroker vom Datentyp **String** sein. In einem ioBroker-JavaScript können sie so gelesen werden:

```javascript
const liste = JSON.parse(
    String(getState('javascript.0.Adventskalender.gutscheine').val || '[]')
);

const betraege = JSON.parse(
    String(getState('javascript.0.Adventskalender.betraege').val || '{}')
);
```

Falls ioBroker deaktiviert oder nicht erreichbar ist, bleibt die Aenderung sicher in SQLite gespeichert. Die Anwendung zeigt den Synchronisationsstatus an.

## Datenhaltung

Die Daten liegen in einem benannten Docker-Volume:

```text
gutscheine_data:/data
```

Die SQLite-Datei liegt im Container unter `/data/gutscheine.db` und bleibt bei Container-Neustarts erhalten.

## Wichtiger Hinweis beim Update

Beim normalen Redeploy das Volume `gutscheine_data` nicht loeschen. Darin befinden sich Passwoerter, eingelöste Codes und Admin-Einstellungen.

Wenn die Passwoerter komplett neu eingerichtet werden sollen, muss das Volume bewusst geloescht werden. Dadurch werden auch eingelöste Codes und Gutschein-Aenderungen geloescht.
