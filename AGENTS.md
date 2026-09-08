# DATAcube — Štatistický úrad SR AI Data Platform

## Prehľad
Platforma pre AI queryovanie Open Data ŠÚ SR cez **MCP + REST API**.
Architekt: Marián Stančík — ukážka architektonického myslenia (MCP, security, CI/CD).

## Repo
`git@github.com:Abra7abra7/datacube-mcp.git`

## Architektúra (2-vrstvová)

```
 MCP stdio (server.py)     HTTP API (api.py)
 — AI agenti                — REST (cURL, web)
 — JSON-RPC 2.0             — API key auth
 — 6 toolov                 — Rate limiting
                             — CORS + Swagger docs
         \                   /
          ┌───────────────┐
          │   core.py     │
          │(zdieľaná log.)│
          └───────┬───────┘
                  │
          ┌───────▼───────┐
          │  SQLite DB    │
          │  678 cubes    │
          │  150 898 hod. │
          └───────────────┘
```

## MCP a volanie príkazov

**MCP (Model Context Protocol)** = štandard ako definovať nástroje pre AI agentov.

1. Užívateľ: "Aká bola mzda v BA?"
2. LLM volá `find_cube("mzda Bratislava")` → MCP vráci np3106rr
3. LLM volá `cube_dimensions("np3106rr")` → dimenzie (kraj, rok, zamestnanie, pohlavie)
4. LLM volá `cube_query("np3106rr", ["SK021","2023","E_PRIEM_HR_MZDA","7"])` → dáta
5. Odpoveď: "Priemerná hrubá mzda v BA kraji 2023 bola 1 423,50 €"

## Súbory
- `core.py` — zdieľaná logika (API, DB, ingest, tool funkcie)
- `server.py` — MCP stdio server
- `api.py` — FastAPI HTTP wrapper (auth, rate limit, CORS)
- `Dockerfile` + `docker-compose.yml` — container deploy
- `.github/workflows/deploy.yml` — CI/CD
- `test_server.py` — testy

## Deploy (GitHub → auto-deploy)
- Coolify (free, už ho máš), Railway (€5), Render (€7), VPS (€4)

## Env vars
- `DATACUBE_API_KEY` — auth (prázdne = disabled)
- `DATACUBE_CORS_ORIGINS` — povolené domény
- `DATACUBE_RATE_LIMIT` — default 100/minute

## Bezpečnosť
- API key auth, rate limiting, CORS, parameterizované SQL, žiadne eval/exec

## Endpointy
- `GET /api/health` — health check
- `GET /api/cubes?search=` — zoznam kociek
- `GET /api/cubes/{code}/dims` — dimenzie
- `GET /api/cubes/{code}/query?dim_values=a,b,c` — dáta
- `GET /api/cubes/find?q=..` — AI fulltext
- `GET /api/status` — stav DB
- `POST /api/ingest` — refresh

## Dashboard — Next.js vs CopilotKit
- **Next.js** = full web framework → grafy, routing, data viz
- **CopilotKit** = React knižnica → AI chat UI, tool calling, generatívne UI
- **Odporúčam: Next.js + CopilotKit** — dashboard s grafmi + AI chat panel

## Roadmap
- [x] MCP server
- [x] HTTP API + auth + rate limit
- [x] Docker + CI/CD
- [ ] Dashboard (Next.js + CopilotKit)
- [ ] Automatický refresh (cron)
- [ ] Data cache
