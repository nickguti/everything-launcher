# NOTES

Launcher in stile Spotlight per Windows 11 (Python 3.12 + PyQt6), con Everything come motore di ricerca invisibile.
Convenzioni: codice, commenti e commit in inglese; interfaccia in italiano.

## Stato

- [x] **Passo 0** – setup: Python 3.12 (winget, per utente), venv `.venv`, PyQt6, SDK Everything + `es.exe` in `vendor/`, repo git.
- [ ] Passo 1 – MVP minimo: config, ricerca Everything, finestra, hotkey Alt+Space
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
