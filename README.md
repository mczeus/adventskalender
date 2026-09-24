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
- Benutzernamen werden ohne Beachtung der Gross-/Kleinschreibung erkannt: `Alex`, `alex` und `ALEX` sind derselbe Benutzer.- `Jan` und `Kim` sind nicht mehr als Benutzer vorgegeben. Bereits vorhandene alte Gutscheine können weiterhin diese Besitzerbezeichnungen enthalten und im Admin-Bereich angepasst werden.
- Passwoerter werden serverseitig als PBKDF2-SHA256-Hash in SQLite gespeichert.
- Gutschein-Codes werden serverseitig geprueft und koennen nur einmal eingeloest werden.

## Admin-Bereich

1. Auf der Login-Seite auf **Admin** klicken.
2. Beim ersten Aufruf ein Admin-Passwort festlegen.
3. Im Admin-Bereich stehen zur Verfuegung:
   - Uebersicht aller vorhandenen Codes
   - Uebersicht aller eingeloesten Codes
   - Summen pro Benutzer
   - Besitzername eines Codes wird bei Auswahl sofort gespeichert
   - Betrag und Beschreibung vorhandener Codes bearbeiten
   - neue Codes mit einem angelegten Benutzernamen anlegen
   - Codes für **Alle Benutzer** anlegen; jeder solcher Code kann insgesamt nur einmal eingelöst werden
   - Codes als **immer gültig / mehrfach einlösbar** markieren
   - Änderungen über einen gemeinsamen Speichervorgang zuverlässig in die Datenbank schreiben
   - einzelne Codes vollständig löschen
   - alle Codes und die Einlösungshistorie vollständig löschen
   - Einloesungen rueckgaengig machen
   - erneute Synchronisierung mit ioBroker

Der eigentliche Code bleibt unverändert; Besitzername, Betrag, Beschreibung und die Einstellung **immer gültig / mehrfach einlösbar** können geändert werden. Standardmäßig bleibt jeder Code einmalig. Wird die Mehrfachverwendung aktiviert, kann der Code wiederholt eingelöst werden; jede Einlösung wird separat in der Historie gespeichert. Einzelne Codes können vollständig gelöscht werden; dabei wird auch eine zugehörige Einlösung gelöscht. Zusätzlich gibt es eine Sicherheitsabfrage zum Löschen aller Codes und der gesamten Einlösungshistorie. Für den Besitzer kann ein angelegter Benutzer oder **Alle Benutzer** gewählt werden. Ein Code für **Alle Benutzer** ist nur einmal insgesamt gültig.

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

Falls ioBroker deaktiviert oder nicht erreichbar ist, bleibt die Aenderung sicher in SQLite gespeichert. Der Synchronisationsstatus ist ausschließlich im Admin-Bereich sichtbar.

## Datenhaltung

Die Daten liegen in einem benannten Docker-Volume:

```text
gutscheine_data:/data
```

Die SQLite-Datei liegt im Container unter `/data/gutscheine.db` und bleibt bei Container-Neustarts erhalten.

## Wichtiger Hinweis beim Update

Beim normalen Redeploy das Volume `gutscheine_data` nicht loeschen. Das Löschen aller Codes im Admin-Bereich leert dagegen bewusst die gespeicherten Gutschein- und Einlösungsdaten. Darin befinden sich Passwoerter, eingelöste Codes und Admin-Einstellungen.

Wenn die Passwoerter komplett neu eingerichtet werden sollen, muss das Volume bewusst geloescht werden. Dadurch werden auch eingelöste Codes und Gutschein-Aenderungen geloescht.


## Standardcode und interne Bezeichnung

- Die geschützten Codes `FROH` und `GAME` bleiben beim Löschen aller Codes erhalten und können auch einzeln nicht gelöscht werden.
- Für jeden Code kann im Admin-Bereich eine interne Bezeichnung gepflegt werden. Sie wird weder in der Benutzeransicht noch in der öffentlichen Einlösungshistorie oder der ioBroker-Synchronisierung ausgegeben.
- Beim Upgrade bestehender Datenbanken wird die zusätzliche Spalte automatisch angelegt.


## Benutzer-Eingabeprotokoll

Im Admin-Bereich gibt es einen separaten, dauerhaft gespeicherten Bereich **Benutzereingaben**. Dort werden angemeldete Gutscheincode-Eingaben mit Zeitpunkt, Benutzer, Eingabe, Ergebnis und Hinweis protokolliert. Erfasst werden erfolgreiche Einlösungen ebenso wie ungültige Codes, falsche Benutzer, ungültige Formate und bereits eingelöste Codes. Das Protokoll wird beim Löschen aller Codes nicht gelöscht und wird nicht an ioBroker übertragen.


## Geschützte Codes FROH und GAME

`FROH` ist dauerhaft vorhanden, kostet 0,00 EUR und ist als mehrfach einlösbarer Code konfiguriert. Er kann von Benutzern wiederholt eingelöst werden und bleibt auch nach dem Löschen aller anderen Codes erhalten.

`GAME` ist ebenfalls dauerhaft vorhanden, kostet 0,00 EUR und kann mehrfach eingelöst werden. Die öffentliche Beschreibung lautet exakt **„Hast du mal das rote Geschenk gecheckt?“**. Der Code kann weder einzeln noch über **„Alle Codes löschen“** entfernt werden. Im Admin-Bereich werden `FROH` und `GAME` als geschützte Codes angezeigt.


## IoBroker-Konfiguration im Admin-Bereich

Die ioBroker-Synchronisierung kann nach der Anmeldung direkt im Admin-Bereich aktiviert oder deaktiviert werden. Dort lassen sich auch die ioBroker-Adresse sowie die beiden Ziel-Datenpunkte ändern. Die Einstellungen werden dauerhaft in SQLite gespeichert und bleiben bei Container-Neustarts erhalten.

- **Eingelöste Gutscheine:** JSON-Text mit den Einlösungen
- **Benutzersummen:** JSON-Text mit den Summen pro Benutzer
- **Verbindung testen:** prüft die eingetragene Adresse
- **Jetzt synchronisieren:** überträgt den aktuellen lokalen Datenstand

Die Werte werden weiterhin über die ioBroker-HTTP-API als JSON-Text mit `type=string` übertragen. Die interne Code-Bezeichnung und das Benutzer-Eingabeprotokoll werden nicht synchronisiert.


## Zeitzone

Die Anwendung verwendet standardmäßig die Zeitzone **Europe/Berlin**. Sie ist in `docker-compose.yml` über `TZ: "Europe/Berlin"` gesetzt. Neue Zeitstempel für Einlösungen, Benutzereingaben und Synchronisierungen werden mit dem korrekten lokalen Offset gespeichert, einschließlich der automatischen Umstellung zwischen Normalzeit und Sommerzeit.


## Zeitformat

Zeitpunkte werden in der Anwendung, im Admin-Bereich, im Benutzer-Eingabeprotokoll und in den an ioBroker übertragenen JSON-Daten als `TT.MM.JJJJ HH:MM:SS` ausgegeben, zum Beispiel `24.09.2026 23:15:57`. Eine Zeitzonenkennung wird dabei nicht angezeigt und nicht übertragen.


## Easter Egg auf der Admin-Anmeldeseite

Auf der Admin-Anmeldeseite ist das rote Geschenk links unter dem Weihnachtsbaum eine unsichtbare Klickfläche. Ein Klick öffnet das kleine Spiel **Geschenk-Tetris**. Das Spiel unterstützt Pfeiltasten, Leertaste, Escape, Touch-Buttons, Punktestand und einen Highscore für die aktuelle Browser-Sitzung. Das Easter Egg benötigt keine Datenbank und wird nicht protokolliert.
