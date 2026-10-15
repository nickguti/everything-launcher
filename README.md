# Everything Launcher

*A Spotlight-style launcher for Windows 11 (Python + PyQt6) that uses [Everything](https://www.voidtools.com/) as an invisible search engine.*

Un launcher veloce in stile Spotlight di macOS per Windows 11: una barra centrata in alto, scrivi e trovi file e cartelle
all'istante, oppure cerchi sul web. La ricerca di Windows è lenta e inaffidabile; [Everything](https://www.voidtools.com/) no,
ma aprirlo ogni volta è scomodo. Qui Everything lavora solo in background e l'unica interfaccia è questa barra.

> Progetto indipendente, non affiliato a voidtools né ad Apple. Everything è un programma di voidtools.

## Funzioni

- **Ricerca istantanea** di file e cartelle con Everything (SDK via `ctypes`, con `es.exe` come ripiego). Risultati mentre scrivi
  (attesa di 80 ms), in un thread separato: l'interfaccia non si blocca mai. Icona, nome e percorso; di default i più recenti per primi.
- **Ricerca sul web**: l'ultima riga è sempre «Cerca su Google: …»; con il prefisso `g testo` mostra solo quella. Si apre come
  nuova scheda in **Chrome** (o Edge, Firefox, un altro browser), mai nel browser predefinito di Windows. Profilo Chrome a scelta.
- **Attivazione**: doppio tocco del tasto Windows (un tocco singolo apre il menu Start come sempre) e/o una scorciatoia
  (di default `Alt+Space`), entrambe configurabili.
- **Aspetto**: tema chiaro/scuro che segue il sistema, colore d'accento di Windows, animazioni pulite (si spengono se Windows ha gli
  effetti disattivati), scelta dello schermo (principale, N, dove c'è il cursore).
- **Sistema**: icona nella tray (Apri, Impostazioni, Avvia con Windows, Riavvia, Esci), una sola istanza alla volta (una nuova
  istanza sostituisce la vecchia), pannello Impostazioni e configurazione guidata al primo avvio.

## Uso

| Tasti | Azione |
|---|---|
| doppio tocco di **Win** / `Alt+Space` | apre o chiude la barra |
| `↑` `↓` | scorre i risultati |
| `Invio` | apre il file, la cartella o la ricerca web |
| `Ctrl+Invio` | mostra il file nella sua cartella (`explorer /select`) |
| `Esc` o clic fuori | chiude la barra |
| `g qualcosa` | cerca solo sul web |

Dalla sintassi di Everything puoi usare anche `ext:pdf`, `size:>10mb`, `dm:today`, `a|b`, `"frase esatta"`…

## Installazione

Servono **Windows 11**, **Python 3.12** (o 3.13) e **[Everything](https://www.voidtools.com/downloads/) 1.4.1 o successivo**
(consigliato con il servizio Everything attivo: è lui che permette a un programma non amministratore di leggere l'indice NTFS).
`Everything64.dll` ed `es.exe` sono già in `vendor/`.

```powershell
git clone https://github.com/nickguti/everything-launcher.git
cd everything-launcher
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
```

Al primo avvio si apre una breve configurazione guidata (attivazione, aspetto, ricerca, browser). Per avviarlo senza console fai
doppio clic su **`Avvia Spotlight.bat`** (se manca l'ambiente virtuale lo crea e installa le dipendenze) oppure usa
`.venv\Scripts\pythonw.exe main.py`. Attivando «Avvia con Windows» dalla tray il comando viene scritto in `HKCU\...\Run`.

Se Everything non è in esecuzione il launcher lo avvia da solo in background (`-startup`, senza finestra). Il caricamento
dell'indice può richiedere qualche secondo; nel frattempo la barra lo dice e la ricerca web resta disponibile.

## Configurazione

Dal pannello Impostazioni (tray → Impostazioni…) oppure a mano in `%APPDATA%\Spotlight\config.json`:

| Chiave | Default | Significato |
|---|---|---|
| `double_tap_win` / `double_tap_ms` | `true` / `300` | doppio tocco del tasto Windows e pausa massima tra i tocchi (150–600) |
| `hotkey` / `hotkey_enabled` | `"Alt+Space"` / `true` | scorciatoia globale (`Ctrl`, `Alt`, `Shift`, `Win` + un tasto) |
| `max_results` | `8` | file mostrati (1–20), più la riga Google |
| `debounce_ms` | `80` | attesa dopo l'ultimo tasto prima di cercare |
| `sort` | `"date_modified_desc"` | `date_modified_desc`, `name_asc`, `path_asc`, `size_desc` |
| `result_filter` | `"all"` | `all`, `files`, `folders` |
| `excluded_paths` | cache, `AppData\Local`, `.git`, `node_modules`… | testi da escludere dal percorso completo |
| `browser` / `browser_path` / `browser_profile` | `"chrome"` / `""` / `""` | `chrome`, `edge`, `firefox`, `custom`; percorso del `.exe` (obbligatorio per `custom`); cartella del profilo Chromium (vuoto = il primo) |
| `theme` | `"system"` | `system`, `light`, `dark` |
| `monitor` | `"primary"` | `primary`, `cursor` o il numero dello schermo (`"1"`, `"2"`…) |
| `animations` | `true` | animazioni (spente comunque se Windows ha disattivato gli effetti) |
| `everything_path` | `""` | percorso di `Everything.exe`, se non viene trovato |

Un valore non valido viene sostituito dal default e segnalato; un file rovinato viene salvato come `config.json.bad`.
Il registro degli eventi è in `%APPDATA%\Spotlight\spotlight.log`.

## Limiti noti

- Sopra le finestre avviate **come amministratore** il doppio tocco non funziona (Windows non lascia vedere i tasti a un programma
  non elevato); `Alt+Space` sì.
- Un tocco singolo del tasto Windows apre Start con un ritardo pari all'intervallo del doppio tocco (300 ms di default).
- Il doppio tocco usa un hook di tastiera di basso livello: la logica è coperta da test automatici, ma è la parte più recente e
  meno provata su configurazioni diverse. Se dà problemi disattivalo dalle Impostazioni e usa la scorciatoia.
- «Tutti gli schermi insieme» non esiste: la barra compare su uno schermo alla volta.
- Con l'ordine per data i file recenti di cache e programmi possono salire in alto: servono le esclusioni di percorso.

## Sviluppo

```powershell
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

```
main.py            avvio
spotlight/
  app.py           collegamento: config, ricerca, finestra, hotkey, tray, impostazioni
  search.py        Everything (SDK + es.exe), thread di ricerca
  ui.py            barra, lista risultati, temi, animazioni
  hotkey.py        scorciatoia globale (RegisterHotKey)
  winhook.py       doppio tocco del tasto Windows (WH_KEYBOARD_LL)
  browser.py       browser, profili, ricerche web a prefisso
  config.py        configurazione JSON
  settings.py      pannello Impostazioni e wizard
  tray.py          icona nella tray
  instance.py      istanza unica con sostituzione
  autostart.py     avvio con Windows
  winapi.py        piccole chiamate Win32
vendor/            Everything64.dll ed es.exe (voidtools, MIT, firmati)
NOTES.md           decisioni, scoperte e prossimi passi
```

Idee per dopo: avvio delle app installate dal menu Start, calcolatrice inline, comandi con prefisso (`yt`, `gh`, `maps`; basta
aggiungere una voce a `browser.WEB_SEARCHES`), priorità ai file aperti di recente.

## Licenza

MIT, vedi [LICENSE](LICENSE). I file in `vendor/` sono di voidtools, anch'essi sotto licenza MIT (vedi `vendor/README.md`).
