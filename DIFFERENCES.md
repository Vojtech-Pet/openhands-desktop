# Čím sa táto appka líši od originálneho OpenHands

*[English version](DIFFERENCES.en.md)*

OpenHands samotný (`OPENHANDS_REPO`, `app_server`) je bežný web-based agent
server — táto appka je nezávislý natívny desktop klient naň napojený cez
REST/WebSocket API, ktorý pridáva vrstvu vecí, čo v origináli vôbec nie sú.

## 1. Natívny PySide6/Qt desktop klient, nie webové UI
Žiadny prehliadač, žiadny frontend server — priamy REST/WebSocket klient
k `app_server`u. Beží ako obyčajná desktopová aplikácia s vlastným oknom,
sidebarom a Mission Control panelom.

## 2. Auto-supervise (ConversationWatchdog)
OpenHands má vlastný "stuck detector", ale je to čierna skrinka bez
možnosti nastavenia prahu. Táto appka sleduje **živý event stream** každej
konverzácie sama a robí:
- hash-based detekciu opakovaných tool callov (rovnaký nástroj + rovnaké
  argumenty)
- 3 odstupňované auto-nudge pokusy (zmeň nástroj → vzdaj sa podúlohy →
  zhrň a pokračuj) namiesto jednej generickej výzvy
- progress-scoring: keď je vidieť skutočný pokrok, resetuje aj tvrdý
  strop na počet krokov (namiesto plochého orezania po 30 krokoch bez
  ohľadu na produktivitu)
- keď sa naozaj vzdá (a o pár sekúnd sa nerozbehne sám), **vynúti okno do
  popredia** a presmeruje agenta, nech **sám navrhne konkrétne riešenia**
  cez `ask_user_question` — appka mu nevymýšľa generické možnosti za neho

Zapnuté automaticky pre každú konverzáciu (Settings → Agent → Auto-supervise
conversations).

## 3. Prístup k súborom mimo sandboxu (Workspace MCP bridge)
OpenHands agent vidí iba svoj vlastný sandbox (`/workspace/project`) — nemá
API na pripojenie ľubovoľného priečinka na hostiteľskom počítači. Táto
appka hostuje vlastný MCP server (`connect_folder`/`list_folder`/
`read_file`/`write_file`), ktorý to rieši — **s povinným potvrdením od
používateľa** pred každým pripojením priečinka aj pred každým zápisom
súboru (okrem Bypass permissions módu, kde sa auto-schvaľuje).

## 4. Ask-user nástroj
OpenHands nemá žiadny vstavaný spôsob, ako sa agent môže opýtať
používateľa na rozhodnutie uprostred úlohy. Appka pridáva `ask_user_question`
MCP tool s podporou viacnásobného výberu (checkboxy), a v Bypass móde ho
automaticky zodpovie namiesto večného čakania.

## 5. Mode selector (Bypass / Auto / Manual)
Jeden combo box v toolbare, ktorý naraz nastavuje `confirmation_mode` +
`security_analyzer` (inak roztrúsené v Settings → Verification) a navyše
riadi, či appka autonómne schvaľuje workspace/ask-user požiadavky.

## 6. Auto-decide Plan vs Code
Pred štartom novej konverzácie appka spraví lacný LLM call, ktorý
rozhodne, či úloha potrebuje najprv plán, alebo môže ísť rovno do kódu —
namiesto toho, aby si to musel vyberať ručne zakaždým. Manuálna voľba má
vždy prednosť.

## 7. Continue as Code (Plan → Act handoff)
Keď Plan konverzácia skončí, appka ponúkne (alebo automaticky spustí)
pokračovanie ako Code agent, napojené cez `parent_conversation_id` —
niečo, čo v origináli neexistuje ako samostatný, jedno-klikový workflow.

## 8. Zbalený "Working" log s live náhľadom
Namiesto toho, aby sa každý reasoning krok a tool call zobrazoval ako
vlastná karta (alebo aby chatové UI ukazovalo iba surový text), appka
balí celý ťah do jednej zbalenej karty s LM-Studio-štýlovým živým
náhľadom (posledné 2 riadky reasoning textu, elapsed timer, "Completed ✓"
po dokončení) — vidno, že sa niečo deje, aj bez rozbaľovania.

## 9. Hlbšia integrácia s LM Studiom
- Automatická detekcia a prepínanie načítaného modelu podľa profilu
- Nastaviteľná dĺžka kontextu (Settings → LLM), ktorá sa pri zmene
  automaticky prepočíta a zosynchronizuje s `max_input_tokens`/
  `condenser.max_tokens` na profiloch (namiesto ručného dolaďovania)
- Rozpoznanie, kedy je LM Studio nedostupné/nemá nahraný model, a jedno-
  klikové spustenie servera priamo z appky

## 10. Mission Control + Errors panel
Prehľad všetkých konverzácií na serveri (nielen tej otvorenej), s live
stavom a možnosťou mazania — a samostatný panel zbierajúci všetky chyby
z aktuálnej konverzácie na jedno miesto.

## 11. Explicitné Plan/Code inštrukcie namiesto skúšania naslepo
Overené naživo: Plan agent bez zdôvodnenia opakovane skúšal `invoke_skill("ssh")`
v nádeji, že sa nejako dostane na internet, kým sám prišiel na to, že je
Plan agent bez terminálu. Appka teraz každému agentovi hneď na začiatku
pošle presnú informáciu o jeho úlohe a dostupných/nedostupných nástrojoch
(Plan: glob/grep/planning editor, žiadny terminal; Code: terminal/file
editor/git, over kroky z plánu naozaj), namiesto toho, aby si to musel
domýšľať za behu.

## 12. Robustnosť, ktorú OpenHands web UI nerieši
- retry na známy neškodný keep-alive race v httpx/uvicorn
- ochrana proti MCP serverom, ktoré začnú registrovať skôr, než reálne
  počúvajú (rozbité MCP session natrvalo pre zvyšok konverzácie)
- appka pri zatvorení nevyloguje LM Studio modely, pokiaľ ešte niekde
  beží iná konverzácia
- oprava blokovania celého GUI na niekoľko sekúnd (synchrónny `lms ps`
  fallback bežal priamo na Qt event loope)
