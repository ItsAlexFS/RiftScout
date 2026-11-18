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

## Poblar la base (seeding)

```bash
python -m scripts.seed --reset --players "TuNombre#LAN,OtroJugador#LAN" --matches 20
```

`--reset` borra únicamente las claves `rift:*` con `SCAN` + `UNLINK` (nunca `FLUSHDB`). También puedes no usar el seed y agregar los jugadores desde la interfaz.

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

## Guion sugerido para la demo (4 min)

1. Mostrar el header: host de Redis Cloud, ping, número de claves y política de eviction.
2. **Create**: agregar un Riot ID real. Señalar en el panel "Redis en vivo" las claves `rift:cache:*` nuevas con su TTL.
3. **Read**: abrir el jugador y su champion pool, historial y duos detectados.
4. **Sync**: pulsar *Sincronizar*. El toast muestra cuántas partidas se reutilizaron desde Redis sin llamar a Riot. El botón queda en cooldown leyendo el `TTL` de `rift:cooldown:sync:*`.
5. **Update**: cambiar rol y notas, guardar.
6. Leaderboards por rango, winrate y KDA (Sorted Sets).
7. **Delete**: eliminar al jugador y mostrar que sus claves desaparecen.
8. **Persistencia**: detener `uvicorn`, volver a levantarlo y recargar: los datos siguen ahí porque viven en la nube.

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
  seed.py                  Seeding / reset con jugadores reales
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
