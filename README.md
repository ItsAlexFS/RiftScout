# RiftScout

Aplicación de scouting de partidas de **League of Legends** que consume la **API de Riot Games** y guarda todo en una base de datos **Clave-Valor (Redis Cloud)**. Permite armar el roster de un equipo, sincronizar sus partidas rankeadas y consultar champion pools, historial, duos y leaderboards en tiempo real.

| Capa | Tecnología |
|---|---|
| Backend | Python 3.11+ · FastAPI · `redis-py` (cliente oficial, modo asyncio) · `httpx` |
| Base de datos | Redis Cloud (Managed Service / DaaS) |
| Fuente de datos | Riot Games API: account-v1, summoner-v4, league-v4, match-v5 |
| Frontend | HTML + JS vanilla servido por FastAPI |

```
Navegador ──HTTP/JSON──> FastAPI (app/api) ──> Servicios (app/services)
                                                  │            │
                                    redis-py (pool, TLS opcional)  httpx + rate limiter
                                                  │            │
                                           Redis Cloud      Riot Games API
```

## Requisitos

- Python 3.11 o superior
- Una base de datos gratuita en [Redis Cloud](https://redis.io/try-free/) (30 MB alcanzan de sobra)
- Una API key de [developer.riotgames.com](https://developer.riotgames.com)

## Instalación

```bash
git clone <tu-repo> riftscout && cd riftscout
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # y edita los valores
```

### 1. Crear la base en Redis Cloud

1. Crea una cuenta y una base **Free** (cualquier proveedor/región, de preferencia `us-east`).
2. En la base, abre **Connect** y copia el *public endpoint* (`host:port`) y la contraseña del usuario `default`.
3. En `.env`: `REDIS_URL=redis://default:<password>@<host>:<port>`
4. En **Configuration > Durability**, revisa que la *data eviction policy* sea `volatile-lru` (es el valor por defecto). Con esa política Redis solo puede expulsar claves con TTL (caché), nunca los datos del roster.

> Las bases Free se eliminan tras 14 días sin actividad. Ejecuta el seed unos días antes del examen y vuelve a correrlo el mismo día.

### 2. Configurar la API de Riot

Copia tu *Development API Key* en `RIOT_API_KEY`. **Expira cada 24 h**, así que regénerala el día de la demo. Los límites de esa key son 20 peticiones/s y 100 peticiones/2 min; la app los respeta con un rate limiter guardado en Redis.

## Preparar y poblar la base

Redis no tiene esquema (no hay `CREATE TABLE`): cada estructura se crea con la primera escritura en su clave. Por eso la preparación tiene dos scripts:

**1. Script de base de datos: `scripts/init_db.py`**

```bash
python -m scripts.init_db
```

- Verifica la conexión con Redis Cloud (host, versión, ping) y que la *eviction policy* no pueda expulsar datos sin TTL.
- Borra únicamente las claves `rift:*` con `SCAN` + `UNLINK` (nunca `FLUSHDB`), así que deja la base vacía sin tocar datos de otras apps.
- Crea las estructuras iniciales (`rift:stats:cache` con los contadores en 0).
- Imprime el esquema de claves definido en `app/keys.py`.

**2. Script de datos iniciales (seeding): `scripts/seed.py`**

Edita `scripts/players.txt` con los Riot IDs reales que quieres cargar (uno por línea, `Nombre#TAG` o `Nombre#TAG,ROL`) y ejecuta:

```bash
python -m scripts.seed --reset
```

- `--reset` ejecuta primero `init_db`, así que este único comando deja la base lista para las pruebas.
- Por cada jugador: lo agrega al roster y sincroniza sus últimas 20 partidas reales desde la API de Riot (`--matches` para cambiarlo, máximo 20).
- Usa los mismos servicios que la API, así que crea exactamente las mismas claves que la aplicación.
- Al final imprime cuántas claves hay de cada tipo (hash, list, set, zset, string).
- También acepta jugadores por línea de comandos: `python -m scripts.seed --players "Nombre#LAN,Otro#LAN"`.

## Ejecutar

```bash
uvicorn app.main:app --reload
```

- App: http://localhost:8000
- Documentación interactiva de la API (Swagger): http://localhost:8000/docs

## Pruebas

```bash
pip install -r requirements-dev.txt
pytest
```

Las pruebas usan `fakeredis` y un cliente de Riot falso (`tests/fake_riot.py` con `tests/fixtures/riot_data.json`), así que no requieren red ni credenciales. Ese cliente solo existe en `tests/`; la app siempre llama a la API real.

## Endpoints

| Método | Ruta | Operación Redis principal |
|---|---|---|
| GET | `/api/players` | `SMEMBERS` + `HGETALL` en pipeline |
| POST | `/api/players` | `MULTI` · `HSET` · `SET` · `SADD` · `ZADD` · `EXEC` |
| GET | `/api/players/{puuid}` | `HGETALL` + `TTL` |
| GET | `/api/players/by-riot-id/{name}/{tag}` | `GET` (índice secundario) |
| PATCH | `/api/players/{puuid}` | `HSET` |
| DELETE | `/api/players/{puuid}` | `UNLINK` · `SREM` · `ZREM` · `ZINCRBY` |
| POST | `/api/players/{puuid}/sync` | `SET NX EX` (lock) · `SMISMEMBER` · `LPUSH`/`LTRIM` · `HINCRBY` · `ZINCRBY` |
| GET | `/api/players/{puuid}/matches` | `LRANGE` + `HGETALL` |
| GET | `/api/players/{puuid}/champions` | `ZREVRANGE` + `ZMSCORE` |
| GET | `/api/matches/{matchId}` | `HGETALL` |
| GET | `/api/leaderboards/{rank\|winrate\|kda}` | `ZREVRANGE ... WITHSCORES` |
| GET | `/api/champions/top` | `ZREVRANGE` |
| GET | `/api/system/health` | `PING` · `INFO` · `DBSIZE` |
| GET | `/api/system/keys` | `SCAN` + `TYPE` + `TTL` |
| GET / DELETE | `/api/system/cache` | `HGETALL` / `SCAN` + `UNLINK` |

## Estructura

```
app/
  main.py            FastAPI, manejo de errores, ciclo de vida de la conexión
  config.py          Variables de entorno (pydantic-settings)
  db.py              Pool de conexiones a Redis Cloud
  keys.py            Convención de nombres de claves (única fuente de verdad)
  container.py       Inyección de dependencias
  riot/client.py     Cliente Riot + caché cache-aside con TTL
  riot/ratelimit.py  Rate limiter distribuido (ventanas fijas en Redis)
  services/          players (CRUD), sync (ingesta), stats (consultas), system
  api/               Rutas y esquemas
  static/            Frontend
scripts/
  init_db.py               Script de base de datos (verificación, reset, estructuras iniciales)
  seed.py                  Datos iniciales con jugadores reales
  players.txt              Lista de Riot IDs que carga el seed
docs/MODELO_DATOS.md       Diseño del modelo clave-valor y catálogo de consultas
tests/                     pytest + fakeredis + cliente Riot falso (solo para tests)
```

## Problemas comunes

| Síntoma | Causa probable |
|---|---|
| `API key de Riot inválida o expirada` | La dev key caducó (24 h). Genera otra. |
| `ConnectionError` al iniciar | `REDIS_URL` mal copiada, o la base Free se eliminó por inactividad. |
| `max number of clients reached` | El plan Free permite 30 conexiones; baja `REDIS_MAX_CONNECTIONS`. |
| HTTP 429 al sincronizar | Cooldown de 60 s por jugador o límite de Riot; el cuerpo trae `retryAfter`. |
