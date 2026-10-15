# NOTES

Launcher in stile Spotlight per Windows 11 (Python 3.12 + PyQt6), con Everything come motore di ricerca invisibile.
Convenzioni: codice, commenti e commit in inglese; interfaccia in italiano.

## Stato

- [x] **Passo 0** – setup: Python 3.12 (winget, per utente), venv `.venv`, PyQt6, SDK Everything + `es.exe` in `vendor/`, repo git.
- [x] **Passo 1** – MVP minimo: config, ricerca Everything (SDK + fallback es.exe), finestra, hotkey Alt+Space.
  Testato con tasti reali iniettati (hotkey, focus in primo piano, digitazione, frecce, Esc, toggle, chiusura per perdita di focus) e 23 test pytest.
- [x] **Passo 2** – browser (Chrome + profili) e ricerca Google / prefisso `g`. Verificato aprendo una vera scheda in Chrome
  (profilo `Default`) e con test dal vivo (riga Google sempre ultima, prefisso, Invio veloce, errore del browser); 41 test pytest.
- [x] **Passo 3** – tray (Apri, Impostazioni, Avvia con Windows, Riavvia, Esci), istanza unica con sostituzione, avvio con Windows.
  Verificato con processi reali (`pythonw main.py` due volte e «Riavvia»: resta una sola istanza, quella nuova); 48 test pytest.
  «Impostazioni» per ora apre il JSON nell'editor predefinito, il pannello arriva al passo 5.
- [x] **Passo 4** – tema chiaro/scuro che segue il sistema, animazioni, scelta del monitor (principale, N, cursore). 60 test pytest;
  animazioni verificate campionando opacità/posizione/altezza nel tempo. Monitor verificato solo con test unitari (qui c'è 1 solo schermo).
- [x] **Passo 5** – pannello Impostazioni (5 schede) + wizard di primo avvio (4 passi), entrambi costruiti dalle stesse sezioni.
  77 test pytest + test dal vivo con tasti reali (26 controlli: wizard, cattura scorciatoia, conflitto, salvataggio).
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
  Chrome si trova da App Paths (HKLM/HKCU, vista 64 e 32 bit) e poi dai percorsi standard; Edge su questa macchina sta in
  `Program Files (x86)\Microsoft\EdgeCore\<versione>\msedge.exe` (nessun App Paths), scelta la versione più recente.
  I profili Chromium si leggono da `User Data\Local State` (`profiles_order`); profilo vuoto = il primo. Si lancia
  `chrome.exe --profile-directory=<dir> <url>`, che apre una nuova scheda nella finestra già aperta. Se il browser esce subito
  con codice ≠ 0, non parte o non si trova, la barra mostra l'errore e resta aperta; non si ripiega mai sul browser di sistema.
- **Riga Google**: sempre l'ultima, anche con 0 file trovati (i file sono al massimo `max_results`, quindi fino a 9 righe).
  I motori a prefisso stanno in `browser.WEB_SEARCHES`: aggiungere `yt`, `gh`, `maps` = aggiungere una voce.
- **Invio durante la ricerca**: se si preme Invio prima che arrivino i risultati nuovi, l'azione viene rimandata ai risultati freschi
  invece di agire sulla lista vecchia.
- **Istanza unica**: il lucchetto è un *mutex Win32 con nome* (`Local\SpotlightLauncher-<utente>`), che sparisce da solo quando
  il processo muore. `QLocalServer` da solo NON basta: su Windows un secondo server può mettersi in ascolto sullo stesso nome
  (scoperto dai test). Il server locale serve solo a ricevere «chiuditi» e a comunicare il pid; se la vecchia istanza non
  esce entro 5 s viene terminata, ma solo se il suo pid è un interprete Python. La nuova istanza acquisisce il lucchetto
  *prima* di registrare la hotkey, così non entra in conflitto con la vecchia. «Riavvia» = lanciare un nuovo processo,
  che sostituisce il corrente.
- **Avvio con Windows**: valore `Spotlight` in `HKCU\...\Run` = `"pythonw.exe" "main.py"` (senza console). All'avvio il comando
  viene riscritto se già attivo, così segue lo spostamento della cartella. Lo stato vero è il registro, non il JSON.
- **Problemi all'avvio** (hotkey occupata, config rovinata) → notifica dalla tray (finestra di dialogo se la tray manca).
- **Tema**: `theme` = `system` (default, segue `QStyleHints.colorScheme` e reagisce al cambio mentre il launcher è in esecuzione),
  `light` o `dark`. L'evidenziazione della riga usa il colore d'accento di Windows (`QPalette.Accent`); il colore del testo sopra
  l'accento si sceglie in base alla luminosità.
- **Animazioni** (`ui.Motion`): comparsa con fade 140 ms + scivolamento di 12 px, chiusura con fade 100 ms, altezza della barra che si
  adatta in 150 ms, evidenziazione che scorre tra le righe in 120 ms. Si spengono con `animations: false` oppure quando Windows ha
  disattivato «Effetti di animazione». Durante la dissolvenza di chiusura la finestra è ancora visibile ma `is_open` è già falso,
  quindi la hotkey la riapre subito.
- **Monitor**: `monitor` = `primary` | `cursor` | `1`, `2`… (ordine di `QGuiApplication.screens()`, il principale è il primo; se lo
  schermo non esiste più si ricade sul principale). «Entrambi/tutti gli schermi» NON è implementato: servirebbero finestre clone.
- **Impostazioni e wizard** (`settings.py`): `Section` (Attivazione, Aspetto, Ricerca, Browser, Avanzate) con `load(config)` /
  `apply(config)` / `problem()`. Il pannello le mostra in schede e lavora su una *copia* della config; il wizard le mostra come
  pagine (Avanzate escluso). Il risultato passa da `App.apply_settings()`: copia nella config viva, ri-registra la hotkey se è
  cambiata (se fallisce rimette la precedente e avvisa, tenendo le altre modifiche), applica tema/monitor/animazioni, salva il JSON
  e aggiorna l'avvio con Windows. «Salta» o chiudere il wizard segna `setup_done` senza cambiare nulla.
- **Cattura della scorciatoia**: `HotkeyEdit` registra la prossima combinazione e, finché ha il focus, sospende la hotkey globale
  (altrimenti Alt+Space aprirebbe il launcher invece di essere registrata). Funziona anche con Alt+Space; le combinazioni con il
  tasto Windows non si possono catturare (le intercetta il sistema), ma si può comunque scriverle a mano nel JSON.
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
