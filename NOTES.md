# NOTES

Launcher in stile Spotlight per Windows 11 (Python 3.12 + PyQt6), con Everything come motore di ricerca invisibile.
Convenzioni: codice, commenti e commit in inglese; interfaccia in italiano.

## Stato

- [x] **Passo 0** – setup: Python 3.12 (winget, per utente), venv `.venv`, PyQt6, SDK Everything + `es.exe` in `vendor/`, repo git.
- [x] **Passo 1** – MVP minimo: config, ricerca Everything (SDK + fallback es.exe), finestra, hotkey Alt+Space.
  Testato con tasti reali iniettati (hotkey, focus in primo piano, digitazione, frecce, Esc, toggle, chiusura per perdita di focus) e 23 test pytest.
- [ ] Passo 2 – browser (Chrome + profili) e ricerca Google / prefisso `g`
- [ ] Passo 3 – tray, istanza unica (la nuova sostituisce la vecchia), avvio con Windows
- [ ] Passo 4 – tema chiaro/scuro di sistema, animazioni, scelta del monitor
- [ ] Passo 5 – pannello Impostazioni + wizard di primo avvio
- [ ] Passo 6 – doppio tocco del tasto Windows (hook WH_KEYBOARD_LL)
- [ ] Passo 7 – test pytest, README, pubblicazione su GitHub con release

## Decisioni

- **Python 3.12.10** installato con `winget --scope user`; venv in `.venv/` (ignorato da git).
- **Dipendenze**: solo `PyQt6`. Tutto il resto è ctypes/stdlib (niente pywin32, niente `keyboard`).
- **Everything**: v1.4.1.1032 in `C:\Program Files\Everything`. Il launcher usa `vendor/Everything64.dll`
  (SDK, MIT, firmato voidtools) e ripiega su `vendor/es.exe`. Non si modifica `Everything.ini`.
  L'SDK ha stato globale e non è thread-safe: tutte le query passano da un unico thread di lavoro.
- **Ordine di default dei risultati**: data di modifica decrescente (più recenti prima); ordini e filtri
  alternativi nelle impostazioni.
- **Browser**: default Chrome (richiesta esplicita, il browser di sistema è Edge); profilo scelto dall'utente
  (default il primo); link aperto come nuova scheda nella finestra esistente; se non parte → errore visibile.
  **Scoperta importante**: sulla macchina Everything gira solo come *servizio* (sessione 0). L'IPC (SDK ed `es.exe`)
  risponde solo se c'è anche il processo client nella sessione utente, quindi il launcher lo avvia da solo con
  `Everything.exe -first-instance -startup` (nessuna finestra; l'icona tray dipende dalle opzioni di Everything).
  Dopo l'avvio il database (~825k voci) impiega ~17 s a caricarsi: il launcher fa un *warm-up* all'avvio e, se si
  cerca prima che sia pronto, mostra «Everything si sta avviando…». A regime ogni query costa 20–100 ms.
- **Esclusioni di percorso**: `!"*<testo>*"` nella query SDK (la forma con `\"` finale veniva mal interpretata da es.exe).
  `es.exe` ignora del tutto i termini negati, quindi nel fallback le esclusioni si applicano lato client su un campione
  8× più grande. `es.exe` inoltre si rompe se un singolo argomento contiene spazi: la query va passata una parola per argomento.
- **Esclusioni di default**: ampie (`AppData\Local`, `ProgramData`, `Windows`, `.git`, `node_modules`, cache dei browser…)
  perché con l'ordine per data i file di sistema/cache dominerebbero i risultati. Modificabili dalle impostazioni.
- **Config**: JSON in `%APPDATA%\Spotlight\config.json`.
- **Istanza unica**: la nuova istanza chiede alla vecchia di chiudersi (socket locale), poi parte; se la vecchia
  non risponde viene terminata.
- **Attivazione**: Alt+Space (`RegisterHotKey`) come riserva; poi doppio tocco del tasto Win (solo Win, nessuna
  altra combinazione), tutto configurabile dal pannello Impostazioni.
- **Identità git**: configurata solo in locale al repo (la globale è una mail scolastica).

## Prossimi passi / idee future

- Prima della pubblicazione: riscrivere autore/email dei commit con l'indirizzo noreply di GitHub.
- Monitor "tutti gli schermi" (barra clonata su ogni monitor) – non incluso nel primo giro.
- Avvio delle app installate dal menu Start, calcolatrice inline, comandi con prefisso (yt, gh, maps),
  priorità ai file aperti di recente (run count di Everything).
- Effetto acrylic/mica via DWM.
