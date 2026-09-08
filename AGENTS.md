# DATAcube MCP Server

## Lokácia
- Projekt: `/root/datacube-mcp/`
- Repo: `git@github.com:Abra7abra7/datacube-mcp.git`
- Server: `server.py` (MCP stdio server)
- DB: `datacube.db` (SQLite, ~19 MB)

## Čo to je
MCP server, ktorý sprístupňuje **Open Data API Štatistického úradu SR** ako sadu MCP toolov.
Každý AI agent (Hermes, Claude Desktop, Cursor, iný MCP host) vie cez neho queryovať reálne štatistické dáta.

## API zdroj
- **URL:** `https://data.statistics.sk/api/v2/`
- **Licencia:** CC-BY 4.0
- **Refresh dát:** každý pracovný deň 10:00 a 22:00 (API zdroj)
- **Obsah:** 678 data cubes (kociek) v 8 doménach (demografia, makro, podniky, odvetvia, životné prostredie, indikátory, Eurostat...)

## Štruktúra súborov
| Súbor | Účel |
|---|---|
| `server.py` | MCP server — 6 toolov, ingest, stdio protokol |
| `requirements.txt` | `mcp>=1.0.0, httpx>=0.27.0` |
| `test_server.py` | Automatické testy všetkých toolov |
| `deploy.sh` | Deploy script na cieľový server (`./deploy.sh user@server`) |
| `datacube-mcp.json` | MCP manifest (features/tools) |
| `datacube.db` | SQLite databáza (678 cubes, 150 898 dimenzionálnych hodnôt) |

## MCP Tools (6)
1. **`list_cubes(search?)`** — zoznam kociek, voliteľne filtrovaný fulltextom
2. **`cube_dimensions(code)`** — dimenzie kocky + všetky možné hodnoty
3. **`cube_query(code, dim_values)`** — reálne dáta z kocky (CSV/JSON)
4. **`find_cube(question)`** — prirodzený jazyk → najlepšia kocka
5. **`ingest_status()`** — stav DB (počet kociek, posledný update)
6. **`ingest_run()`** — manuálny refresh kolekcie a dimenzií

## Architektúra
```
                          ┌─────────────────┐
                          │  ŠÚ SR API       │
                          │  data.statistics │
                          │  .sk/api/v2/     │
                          └────────┬─────────┘
                                   │ HTTP GET
                                   ▼
┌──────────────┐         ┌─────────────────┐
│ MCP Client   │◄──MCP──►│  server.py      │
│ (Hermes,     │  stdio  │  MCP stdio      │
│  Claude,     │         │  server          │
│  Cursor...)  │         │                  │
└──────────────┘         │  ┌───────────┐   │
                         │  │ SQLite DB  │   │
                         │  │ datacube   │   │
                         │  │ .db        │   │
                         │  └───────────┘   │
                         └─────────────────┘
```

**Dátový tok:**
1. `ingest_run()` stiahne kolekciu (zoznam všetkých kociek) + dimenzie (možné hodnoty každého filtra)
2. Uloží do lokálnej SQLite
3. Query tooly (`list_cubes`, `cube_dimensions`, `find_cube`) čítajú z DB — bleskovo, bez API callu
4. `cube_query` volá reálne API endpointy ŠÚ SR (dáta sú živé)

## Deploy na cieľový server

### Prerekvizity
- Python 3.10+
- SSH prístup
- ~20 MB voľného miesta (DB) + ~100 MB (Python + dependencies)

### Postup
```bash
# 1. Naklonovať
git clone git@github.com:Abra7abra7/datacube-mcp.git /opt/datacube-mcp

# 2. Závislosti
cd /opt/datacube-mcp && pip install -r requirements.txt

# 3. Spustiť ingest (ak nie je DB)
python3 server.py --ingest   # ~5 minút

# 4. Spustiť MCP server
python3 server.py            # čaká na stdin
```

### Ako to pripojiť z MCP clienta
```json
{
  "mcpServers": {
    "datacube": {
      "command": "python3",
      "args": ["/opt/datacube-mcp/server.py"]
    }
  }
}
```

### Ako service (systemd)
```
[Unit]
Description=DATAcube MCP Server
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/datacube-mcp/server.py
WorkingDirectory=/opt/datacube-mcp
Restart=always
User=datacube

[Install]
WantedBy=multi-user.target
```

## Produkčné nasadenie — odporúčania

### Pre jeden server (niekoľko agentov)
- **SSH tunnel:** MCP stdio je navrhnutý na lokálne použitie. Ak server nie je ten istý, spustiť cez SSH tunel: `ssh -R 127.0.0.1:5000:127.0.0.1:5000`
- **Alternatíva:** wrapper script, ktorý spustí `python3 server.py` cez SSH

### Pre viacero agentov / REST prístup
- Obrátiť `server.py` na HTTP server (FastAPI + uvicorn)
- `ingest_run()` je už pripravený ako cron job

### Cron job na refresh
Odporúčaný interval: **každých 12h** (pracovné dni)
```bash
0 */12 * * * cd /opt/datacube-mcp && python3 server.py --ingest
```

## Bezpečnosť

### Riziká
| Riziko | Úroveň | Mitigácia |
|---|---|---|
| API kľúče | **žiadne** | API ŠÚ SR je free, CC-BY, bez autentifikácie |
| DB injection | nízke | Iba SQLite read queries cez parameterizované SQL |
| Remote code execution | nízke | Čistý Python, žiadne eval(), žiadne exec() |
| SSH kľúč v repozitári | vysoké | **NIKDY** necommitovať `.env` ani SSH kľúče |
| DB v gite (19MB) | stredné | DB je commitnutá, časom narastie — riešiť .gitignore |

### Dobré praktiky
- **Nespúšťať ako root** — vytvor system user `datacube`
- **Pravidelný refresh** — cron job, nie bežiaci daemon
- **Git ignore pre DB** — ak nechceš veľký repo, pridaj `datacube.db` do `.gitignore` a ingestuj na cieľovom serveri

## Aplikácia nad MCP serverom

### Možnosti čo postaviť
1. **Chat bot** (Telegram, Slack, web) — "Aká bola priemerná mzda v BA v 2023?"
2. **Dashboard** — automatické generovanie reportov + grafov
3. **API wrapper** — REST API pre iné služby
4. **Notifikácie** — "povedz mi keď HDP klesne pod X"
5. **LLM plugin** — knowledge retrieval pre AI agentov

### Príklad: jednoduchý Telegram bot
```
user: "Aká bola nezamestnanosť v KE kraji 2024?"
bot:  call find_cube("nezamestnanosť Košický kraj")
      → nájde cube, dimenzie
      call cube_query(code, ["SK042", "2024", ...])
      → vráti hodnotu
      odpovie: "Nezamestnanosť v KE kraji v 2024 bola 8,2 %"
```

## Vývoj
- Python, stdlib + `mcp` knižnica
- TDD: testy v `test_server.py`
- Iný agent ho tooluje cez MCP protokol (žiadny HTTP server, žiadne API endpointy na otvorenie)

## Known Issues / Roadmap
- [ ] Pridať `.gitignore` — exclude `datacube.db`
- [ ] Automatický cron refresh (systemd timer)
- [ ] HTTP wrapper pre REST prístup
- [ ] Možnosť queryovať len z cache (bez API callu)
- [ ] Batch ingest — vrátane dátových snapshotov (teraz len metadáta)