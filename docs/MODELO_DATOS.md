# Modelo de datos Clave-Valor (Redis Cloud)

Material base para las secciones "Justificación del paradigma", "Diseño del modelo NoSQL" y "Catálogo de consultas" del PDF.

## 1. Convención de nombres de claves

Formato: `<app>:<entidad>:<id>[:<subentidad>]`, con `:` como separador jerárquico (convención estándar de Redis que además agrupa las claves en RedisInsight). Todas las claves se construyen en `app/keys.py`, de modo que el código, el seed y esta documentación no pueden divergir.

| Clave | Tipo | TTL | Contenido | Comandos |
|---|---|---|---|---|
| `rift:player:{puuid}` | Hash | sin TTL | Perfil: Riot ID, tier, LP, rol, notas, timestamps | `HSET` `HGETALL` `HMGET` `UNLINK` |
| `rift:idx:riotid:{nombre#tag}` | String | sin TTL | Índice secundario Riot ID → PUUID | `SET` `GET` |
| `rift:roster` | Set | sin TTL | PUUIDs del roster | `SADD` `SREM` `SISMEMBER` `SMEMBERS` |
| `rift:player:{puuid}:matches` | List | sin TTL, tope 100 | IDs de partidas, la más reciente primero | `LPUSH` `LTRIM` `LRANGE` `LLEN` |
| `rift:player:{puuid}:seen` | Set | sin TTL | Partidas ya procesadas (idempotencia) | `SADD` `SMISMEMBER` |
| `rift:player:{puuid}:totals` | Hash | sin TTL | Contadores: games, wins, kills, deaths, assists, cs, damage, seconds | `HINCRBY` `HGETALL` |
| `rift:player:{puuid}:champs:games` | Sorted Set | sin TTL | Campeón → partidas jugadas | `ZINCRBY` `ZREVRANGE` |
| `rift:player:{puuid}:champs:wins` | Sorted Set | sin TTL | Campeón → victorias | `ZINCRBY` `ZMSCORE` |
| `rift:match:{matchId}` | Hash | sin TTL | Partida compacta (metadatos + 10 participantes en JSON) | `HSET` `HGETALL` |
| `rift:lb:rank` · `rift:lb:winrate` · `rift:lb:kda` | Sorted Set | sin TTL | Leaderboards del roster | `ZADD` `ZREVRANGE WITHSCORES` `ZREM` |
| `rift:champs:roster` | Sorted Set | sin TTL | Campeones más jugados por todo el roster | `ZINCRBY` `ZREVRANGE` `ZREMRANGEBYSCORE` |
| `rift:recent:searches` | List | sin TTL, tope 10 | Últimos Riot IDs agregados | `LPUSH` `LTRIM` `LRANGE` |
| `rift:stats:cache` | Hash | sin TTL | hits, misses y llamadas a Riot | `HINCRBY` `HGETALL` |
| `rift:cache:{tipo}:{id}` | String (JSON) | 60 s – 24 h | Respuestas de la API de Riot (cache-aside) | `GET` `SET EX` |
| `rift:rl:riot:{ventana}:{bucket}` | String (contador) | 2 s / 121 s | Rate limiter de ventana fija | `INCR` `EXPIRE` en `MULTI` |
| `rift:lock:sync:{puuid}` | String | 30 s | Lock distribuido de sincronización | `SET NX EX` + Lua |
| `rift:cooldown:sync:{puuid}` | String | 60 s | Cooldown entre sincronizaciones | `SET EX` `TTL` |

Ejemplo de `rift:player:{puuid}` (Hash):

```
gameName Kukulcan        tagLine MYB          platform la1
tier DIAMOND             rank II              lp 54
rankedWins 88            rankedLosses 79      summonerLevel 276
role MID                 notes "Main Ahri"    profileIconId 33
createdAt 1794955065503  updatedAt ...        lastSyncAt ...
```

### Por qué cada estructura

- **Hash para entidades** (jugador, partida, totales): agrupa campos de un mismo objeto bajo una sola clave, permite leer o actualizar un campo sin reescribir el resto (`HSET notes ...`) y `HINCRBY` hace contadores atómicos.
- **Set para pertenencia**: `SISMEMBER` y `SMISMEMBER` responden en O(1) si un jugador está en el roster o si una partida ya se procesó.
- **List para historial**: el orden de inserción es el orden cronológico; `LPUSH` + `LTRIM` mantiene una ventana acotada (capped list) sin crecer sin límite.
- **Sorted Set para rankings**: el score es la métrica y Redis mantiene el orden en cada escritura. `ZREVRANGE 0 9` devuelve el top 10 en O(log N + 10) sin `ORDER BY`.
- **String con TTL para datos volátiles**: caché, contadores del rate limiter, locks y cooldowns se borran solos.

### Partida compacta

La respuesta de match-v5 pesa decenas de KB. `compact_match()` conserva solo lo necesario y la guarda como Hash de ~3.6 KB (medido con `MEMORY USAGE`). Los 10 participantes van en un campo JSON porque siempre se leen junto con la partida: es un caso de agregado que no necesita acceso parcial. Con 30 MB caben alrededor de 7 000 partidas.

Las partidas son entidades compartidas: si dos jugadores del roster jugaron juntos, la segunda sincronización encuentra `rift:match:{id}` y no vuelve a llamar a Riot. Por eso borrar un jugador no borra sus partidas.

## 2. Estrategia de expiración (TTL)

| Dato | TTL | Razón |
|---|---|---|
| Riot ID → PUUID | 24 h | Cambia solo si el jugador renombra su cuenta |
| Summoner (nivel, ícono) | 1 h | Cambia poco |
| Liga (tier, LP) | 10 min | Cambia después de cada partida |
| Lista de IDs de partidas | 2 min | Puede aparecer una partida nueva en cualquier momento |
| Riot ID inexistente (caché negativa) | 60 s | Evita gastar el límite de Riot con errores de escritura |
| Cooldown de sincronización | 60 s | Limita el abuso desde la UI; el `TTL` restante se muestra en el botón |
| Lock de sincronización | 30 s | Si el proceso muere, el lock se libera solo |
| Contadores de rate limit | 2 s / 121 s | Ventanas de 1 s y 120 s de la dev key de Riot |

Los datos de dominio (roster, partidas, agregados, leaderboards) **no** tienen TTL: son el estado persistente de la aplicación.

## 3. Persistencia y política de memoria

- **Eviction:** `volatile-lru` (default de Redis Cloud). Si la base llega a su límite de memoria, Redis solo expulsa claves con TTL, es decir, la caché. Los datos sin TTL quedan protegidos. Esta separación es la razón de diseño para que *todo* lo volátil tenga TTL y nada persistente lo tenga.
- **Durabilidad:** Redis Cloud es un servicio gestionado; los datos sobreviven a reinicios de la aplicación porque viven en el servidor remoto. En planes pagos se puede elegir persistencia **AOF cada segundo** (pérdida máxima ≈1 s de escrituras) o snapshots **RDB** periódicos. La recomendación para producción sería AOF cada segundo, ya que el roster y los agregados no se pueden reconstruir sin volver a gastar cuota de Riot. El plan Free no ofrece backups y se elimina tras 14 días sin actividad, lo cual es aceptable para un prototipo académico porque el seed reconstruye el estado.
- **Reset seguro:** el script de base de datos (`scripts/init_db.py`, también llamado por `seed --reset`) borra con `SCAN MATCH rift:*` + `UNLINK` (no bloqueante) en lugar de `FLUSHDB` o `KEYS`.

## 4. Catálogo de consultas

Cada operación se muestra como comando Redis y como llamada del SDK (`redis-py`, cliente asyncio).

### Create: agregar jugador (transacción atómica)

```
MULTI
HSET rift:player:{puuid} gameName Kukulcan tagLine MYB tier DIAMOND rank II lp 54 role MID ...
SET  rift:idx:riotid:kukulcan#myb {puuid}
SADD rift:roster {puuid}
ZADD rift:lb:rank 2654 {puuid}
LPUSH rift:recent:searches "Kukulcan#MYB"
LTRIM rift:recent:searches 0 9
EXEC
```
```python
pipe = r.pipeline(transaction=True)
pipe.hset(keys.player(puuid), mapping=data)
pipe.set(keys.riot_id_index(name, tag), puuid)
pipe.sadd(keys.roster(), puuid)
pipe.zadd(keys.leaderboard("rank"), {puuid: score})
await pipe.execute()
```

### Read: perfil y roster

```
HGETALL rift:player:{puuid}
SMEMBERS rift:roster                      -- luego HGETALL de cada uno en un pipeline
GET rift:idx:riotid:kukulcan#myb          -- búsqueda por Riot ID
```

### Update: rol y notas

```
HSET rift:player:{puuid} role MID notes "Main Ahri" updatedAt 1794955065503
```

### Delete: jugador y todas sus claves

```
MULTI
UNLINK rift:player:{puuid} rift:player:{puuid}:matches rift:player:{puuid}:seen
       rift:player:{puuid}:totals rift:player:{puuid}:champs:games rift:player:{puuid}:champs:wins
       rift:idx:riotid:kukulcan#myb rift:cooldown:sync:{puuid}
SREM rift:roster {puuid}
ZREM rift:lb:rank {puuid}
ZREM rift:lb:winrate {puuid}
ZREM rift:lb:kda {puuid}
ZINCRBY rift:champs:roster -9 Ahri        -- una por campeón del jugador
ZREMRANGEBYSCORE rift:champs:roster -inf 0
EXEC
```

### Cache-aside con TTL (API de Riot)

```
GET rift:cache:league:{puuid}                        -- hit: se responde sin HTTP
SET rift:cache:league:{puuid} '<json>' EX 600        -- miss: se llama a Riot y se guarda
TTL rift:cache:league:{puuid}
```

### Rate limiter distribuido

```
MULTI
INCR   rift:rl:riot:1s:1794955081
EXPIRE rift:rl:riot:1s:1794955081 2
INCR   rift:rl:riot:120s:14957959
EXPIRE rift:rl:riot:120s:14957959 121
EXEC
```

### Lock + cooldown de sincronización

```
TTL rift:cooldown:sync:{puuid}                       -- > 0: responder 429 con retryAfter
SET rift:lock:sync:{puuid} {token} NX EX 30          -- nil: ya hay otra sync en curso
... ingesta ...
EVAL "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0"
     1 rift:lock:sync:{puuid} {token}
SET rift:cooldown:sync:{puuid} 1 EX 60
```

### Ingesta idempotente de una partida

```
SMISMEMBER rift:player:{puuid}:seen LA1_1620031676 LA1_1620095028 ...
HGETALL rift:match:LA1_1620031676                    -- si ya existe, no se descarga de Riot
HSET rift:match:LA1_1620031676 matchId ... participants '[...]'
MULTI
SADD    rift:player:{puuid}:seen LA1_1620031676
LPUSH   rift:player:{puuid}:matches LA1_1620031676
LTRIM   rift:player:{puuid}:matches 0 99
HINCRBY rift:player:{puuid}:totals games 1
HINCRBY rift:player:{puuid}:totals kills 5
...
ZINCRBY rift:player:{puuid}:champs:games 1 Syndra
ZINCRBY rift:player:{puuid}:champs:wins 1 Syndra     -- solo si ganó
ZINCRBY rift:champs:roster 1 Syndra
EXEC
ZADD rift:lb:winrate 57.14 {puuid}
ZADD rift:lb:kda 4.05 {puuid}
```

### Consultas especializadas

```
LRANGE rift:player:{puuid}:matches 0 9                       -- últimas 10 partidas
HGETALL rift:match:{matchId}                                  -- (pipeline, una por partida)
ZREVRANGE rift:player:{puuid}:champs:games 0 4 WITHSCORES     -- top 5 campeones
ZMSCORE rift:player:{puuid}:champs:wins Ahri Syndra Viktor    -- victorias de esos campeones
ZREVRANGE rift:lb:kda 0 9 WITHSCORES                          -- top 10 por KDA
ZREVRANGE rift:champs:roster 0 9 WITHSCORES                   -- campeones más jugados del equipo
SCAN 0 MATCH rift:cache:* COUNT 200                           -- inspector de claves volátiles
```

## 5. Justificación frente a un SGBD relacional

| Necesidad del problema | Modelo relacional | Modelo clave-valor (Redis) |
|---|---|---|
| Responder sin gastar la cuota de Riot (100 req / 2 min) | Una tabla de caché requiere un job para purgar filas vencidas | `SET ... EX` expira solo; la expiración es nativa |
| Rankings que cambian en cada partida | `ORDER BY` sobre agregados en cada lectura, o vistas materializadas | Sorted Set mantiene el orden al escribir; leer el top N es O(log N + N) |
| Contadores por jugador y campeón | `UPDATE ... SET x = x + 1` con bloqueo de fila | `HINCRBY` / `ZINCRBY` atómicos en memoria |
| Rate limiter y locks compartidos entre procesos | Tablas auxiliares con transacciones y limpieza manual | `INCR`+`EXPIRE`, `SET NX EX`: patrones nativos |
| Datos de Riot con esquema JSON que cambia por parche | Migraciones al agregar o quitar campos | Hashes sin esquema fijo |
| Acceso siempre por clave conocida (PUUID, matchId) | Joins entre jugadores, partidas y participantes | Lectura directa por clave en O(1), sin joins |

Lo que se cede al elegir Redis: no hay consultas ad hoc (por ejemplo "partidas con más de 10 kills" requeriría un índice adicional o RediSearch), la integridad referencial es responsabilidad de la aplicación (por eso `delete` limpia todas las estructuras en una transacción) y el dataset está acotado por la memoria contratada.

## 6. Consistencia y rendimiento (para la defensa)

- **Atomicidad:** las escrituras que tocan varias claves usan `MULTI/EXEC` (`pipeline(transaction=True)`). Redis ejecuta la transacción completa sin intercalar comandos de otros clientes.
- **Latencia de red:** las lecturas de N claves se agrupan en un pipeline para pagar un solo viaje de ida y vuelta a la nube en lugar de N.
- **Concurrencia:** el lock `SET NX EX` evita dos sincronizaciones simultáneas del mismo jugador; se libera con Lua (comparar token y borrar de forma atómica) para no liberar el lock de otro proceso.
- **Idempotencia:** el Set `seen` garantiza que repetir una sincronización no duplica partidas ni infla contadores.
- **Operaciones no bloqueantes:** `SCAN` en vez de `KEYS` y `UNLINK` en vez de `DEL` para borrados masivos.
- **Conexión:** pool limitado a 10 conexiones (el plan Free admite 30), `health_check_interval` y reintentos con backoff exponencial ante cortes de red.
- **Credenciales:** `REDIS_URL` y `RIOT_API_KEY` solo vienen de variables de entorno (`.env` está en `.gitignore`).
