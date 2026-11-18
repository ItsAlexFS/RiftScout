// RiftScout frontend: vanilla JS over the FastAPI REST API.
const API = "/api";
let DDRAGON = "15.22.1";
let selected = null;
let board = "rank";
let cooldownTimer = null;

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const champImg = (name) => `https://ddragon.leagueoflegends.com/cdn/${DDRAGON}/img/champion/${encodeURIComponent(name)}.png`;
const iconImg = (id) => `https://ddragon.leagueoflegends.com/cdn/${DDRAGON}/img/profileicon/${id}.png`;
const img = (src, cls = "") => `<img class="${cls}" src="${src}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">`;
const timeAgo = (ms) => {
  const h = Math.round((Date.now() - ms) / 3.6e6);
  return h < 24 ? `hace ${h} h` : `hace ${Math.round(h / 24)} d`;
};

async function api(path, opts = {}) {
  const res = await fetch(API + path, { headers: { "content-type": "application/json" }, ...opts });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(body.error || `HTTP ${res.status}`), { status: res.status, body });
  return body;
}

function toast(msg, error = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = `toast show${error ? " error" : ""}`;
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.className = "toast"), 3500);
}

// ---------------------------------------------------------------- health
async function loadHealth() {
  try {
    const h = await api("/system/health");
    $("#health").innerHTML = `
      <span><i class="dot"></i>Redis <b>${esc(h.redisVersion)}</b></span>
      <span>host <b>${esc(h.redisHost)}</b></span>
      <span>ping <b>${h.pingMs} ms</b></span>
      <span>claves <b>${h.dbSize}</b></span>
      <span>memoria <b>${esc(h.usedMemory)}</b></span>
      <span>eviction <b>${esc(h.maxMemoryPolicy)}</b></span>`;
  } catch {
    $("#health").innerHTML = `<span><i class="dot off"></i>Sin conexión con Redis</span>`;
  }
}

// ---------------------------------------------------------------- roster
async function loadRoster() {
  const players = await api("/players");
  $("#roster").innerHTML = players.length ? players.map((p) => `
    <li data-puuid="${p.puuid}" class="${p.puuid === selected ? "active" : ""}">
      ${img(iconImg(p.profileIconId))}
      <div>
        <div class="name">${esc(p.riotId)}</div>
        <div class="meta">${esc(p.tier)} ${esc(p.rank)} · ${p.lp} LP · ${p.stats.winrate ?? "–"}% WR</div>
      </div>
      <span class="pill" style="margin-left:auto">${esc(p.role)}</span>
    </li>`).join("") : `<li class="muted">Roster vacío. Agrega un Riot ID.</li>`;
  document.querySelectorAll("#roster li[data-puuid]").forEach((li) =>
    li.addEventListener("click", () => selectPlayer(li.dataset.puuid)));
}

$("#add-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = new FormData(e.target);
  const btn = e.target.querySelector("button");
  btn.disabled = true;
  try {
    const p = await api("/players", { method: "POST", body: JSON.stringify(Object.fromEntries(form)) });
    toast(`${p.riotId} agregado (HSET + SADD + ZADD)`);
    e.target.reset();
    await refreshAll();
    selectPlayer(p.puuid);
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
});

// ---------------------------------------------------------------- detail
async function selectPlayer(puuid) {
  selected = puuid;
  document.querySelectorAll("#roster li").forEach((li) => li.classList.toggle("active", li.dataset.puuid === puuid));
  try {
    const [p, champs, matches] = await Promise.all([
      api(`/players/${puuid}`), api(`/players/${puuid}/champions`), api(`/players/${puuid}/matches?limit=20`),
    ]);
    renderDetail(p, champs, matches);
  } catch (err) {
    $("#detail").innerHTML = `<div class="empty">${esc(err.message)}</div>`;
  }
}

function renderDetail(p, champs, matches) {
  const s = p.stats;
  const roles = ["TOP", "JUNGLE", "MID", "ADC", "SUPPORT", "FILL"];
  const maxGames = Math.max(1, ...champs.map((c) => c.games));
  $("#detail").innerHTML = `
    <div class="player-head">
      ${img(iconImg(p.profileIconId), "avatar")}
      <div>
        <h3>${esc(p.gameName)}<span class="muted"> #${esc(p.tagLine)}</span></h3>
        <div><span class="tier">${esc(p.tier)} ${esc(p.rank)}</span> · ${p.lp} LP · nivel ${p.summonerLevel}
          · ${p.rankedWins}W ${p.rankedLosses}L</div>
        <div class="muted">Última sync: ${p.lastSyncAt !== "0" ? new Date(+p.lastSyncAt).toLocaleString() : "nunca"}</div>
      </div>
      <div class="actions">
        <button id="sync-btn">Sincronizar</button>
        <button id="delete-btn" class="danger">Eliminar</button>
      </div>
    </div>

    <div class="stats">
      <div class="stat"><b>${s.games}</b><span>partidas guardadas</span></div>
      <div class="stat"><b>${s.winrate ?? "–"}%</b><span>winrate</span></div>
      <div class="stat"><b>${s.kda ?? "–"}</b><span>KDA (${s.avgKills ?? 0}/${s.avgDeaths ?? 0}/${s.avgAssists ?? 0})</span></div>
      <div class="stat"><b>${s.csPerMin ?? "–"}</b><span>CS / min</span></div>
    </div>

    <div class="edit-row">
      <select id="role">${roles.map((r) => `<option ${r === p.role ? "selected" : ""}>${r}</option>`).join("")}</select>
      <textarea id="notes" placeholder="Notas de scouting…">${esc(p.notes)}</textarea>
      <button id="save-btn" class="ghost">Guardar</button>
    </div>

    <div class="section-title">Campeones más jugados</div>
    <div class="note">
      <p><code>ZREVRANGE</code> lee un sorted set ordenado de mayor a menor score. Aquí devuelve los 5 campeones
        con más partidas de <code>rift:player:{puuid}:champs:games</code> (miembro = campeón, score = partidas).</p>
      <p><code>ZMSCORE</code> devuelve el score de varios miembros en una sola llamada. Aquí trae las victorias de
        esos 5 campeones desde <code>rift:player:{puuid}:champs:wins</code>. Winrate = victorias / partidas.</p>
    </div>
    <div class="champ-pool">${champs.length ? champs.map((c) => `
      <div class="champ-row">
        ${img(champImg(c.champion))}<span>${esc(c.champion)}</span>
        <div class="bar"><i style="width:${(100 * c.games) / maxGames}%"></i></div>
        <span class="muted">${c.games} · ${c.winrate}%</span>
      </div>`).join("") : `<div class="muted">Sin partidas. Pulsa “Sincronizar”.</div>`}
    </div>

    <div class="section-title">Últimas partidas</div>
    <div class="note">
      <p><code>LRANGE</code> lee un rango de elementos de una lista por posición. Aquí lee los primeros 20 IDs de
        <code>rift:player:{puuid}:matches</code>; la partida más reciente queda al inicio porque se agrega con <code>LPUSH</code>.</p>
      <p><code>HGETALL</code> devuelve todos los campos y valores de un hash. Aquí trae los datos de cada partida desde
        <code>rift:match:{matchId}</code> (duración, fecha, jugadores, etc.). Las 20 lecturas van juntas en un pipeline,
        es decir, en un solo viaje de ida y vuelta a Redis.</p>
    </div>
    <div class="matches">${matches.map((m) => `
      <div class="match ${m.win ? "win" : "loss"}" data-match="${m.matchId}">
        <span class="edge"></span>
        ${img(champImg(m.champion))}
        <div>
          <div><b>${m.win ? "Victoria" : "Derrota"}</b> · ${esc(m.champion)} <small>${esc(m.position)}</small></div>
          <small>${timeAgo(m.gameCreation)} · ${m.durationMin} min</small>
          ${m.rosterAllies.length ? `<div class="allies">Con ${m.rosterAllies.map(esc).join(", ")}</div>` : ""}
        </div>
        <div class="kda"><b>${m.k}/${m.d}/${m.a}</b><br><small>${m.kda} KDA</small></div>
        <div><b>${m.cs}</b><br><small>CS</small></div>
        <small>${esc(m.matchId)}</small>
      </div>`).join("") || `<div class="muted">Sin partidas.</div>`}
    </div>`;

  $("#save-btn").onclick = async () => {
    try {
      await api(`/players/${p.puuid}`, { method: "PATCH",
        body: JSON.stringify({ role: $("#role").value, notes: $("#notes").value }) });
      toast("Jugador actualizado (HSET)");
      loadRoster();
    } catch (err) { toast(err.message, true); }
  };
  $("#delete-btn").onclick = async () => {
    if (!confirm(`¿Eliminar a ${p.riotId} y todas sus claves?`)) return;
    try {
      await api(`/players/${p.puuid}`, { method: "DELETE" });
      toast(`${p.riotId} eliminado (UNLINK + SREM + ZREM)`);
      selected = null;
      $("#detail").innerHTML = `<div class="empty">Selecciona un jugador del roster</div>`;
      refreshAll();
    } catch (err) { toast(err.message, true); }
  };
  $("#sync-btn").onclick = () => syncPlayer(p.puuid);
  document.querySelectorAll(".match").forEach((el) => el.addEventListener("click", () => openMatch(el.dataset.match)));
  startCooldown(p.syncCooldown);
}

function startCooldown(seconds) {
  clearInterval(cooldownTimer);
  const btn = $("#sync-btn");
  if (!btn) return;
  let left = seconds;
  const paint = () => {
    btn.disabled = left > 0;
    btn.textContent = left > 0 ? `Cooldown ${left}s (TTL)` : "Sincronizar";
  };
  paint();
  if (left > 0) cooldownTimer = setInterval(() => { left -= 1; paint(); if (left <= 0) clearInterval(cooldownTimer); }, 1000);
}

async function syncPlayer(puuid) {
  const btn = $("#sync-btn");
  btn.disabled = true;
  btn.textContent = "Sincronizando…";
  try {
    const r = await api(`/players/${puuid}/sync?count=20`, { method: "POST" });
    toast(`${r.newMatches} partidas nuevas · ${r.downloadedFromRiot} descargadas de Riot · ` +
          `${r.reusedFromRedis} reutilizadas de Redis · ${r.riotCalls} llamadas · ${r.elapsedMs} ms`);
    await refreshAll();
    await selectPlayer(puuid);
  } catch (err) {
    toast(err.message, true);
    if (err.body?.retryAfter) startCooldown(err.body.retryAfter);
    else { btn.disabled = false; btn.textContent = "Sincronizar"; }
  }
}

async function openMatch(matchId) {
  const m = await api(`/matches/${matchId}`);
  const team = (id) => `
    <div class="team ${m.winningTeam === +id ? "win" : "loss"}">
      <h4>${id === "100" ? "Lado azul" : "Lado rojo"} · ${m.winningTeam === +id ? "Victoria" : "Derrota"}</h4>
      ${m.teams[id].map((p) => `
        <div class="p-row ${p.inRoster ? "roster-mate" : ""}">
          ${img(champImg(p.champion))}<span>${esc(p.riotId)}<br><small class="muted">${esc(p.champion)} · ${esc(p.position)}</small></span>
          <span>${p.k}/${p.d}/${p.a}</span><span class="muted">${p.cs} CS</span>
        </div>`).join("")}
    </div>`;
  $("#match-body").innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center">
      <h3>${esc(m.matchId)}</h3><button class="ghost small" onclick="this.closest('dialog').close()">Cerrar</button>
    </div>
    <div class="muted">Ranked Solo/Duo · ${m.durationMin} min · parche ${esc(m.gameVersion.split(".").slice(0, 2).join("."))}
      · <code>HGETALL rift:match:${esc(m.matchId)}</code></div>
    <div class="teams">${team("100")}${team("200")}</div>`;
  $("#match-modal").showModal();
}

// ---------------------------------------------------------- leaderboards
async function loadLeaderboard() {
  const rows = await api(`/leaderboards/${board}?top=10`);
  const fmt = (r) => board === "rank" ? `${r.tier} ${r.rank} · ${r.lp} LP` : board === "winrate" ? `${r.score}%` : `${r.score} KDA`;
  $("#leaderboard").innerHTML = rows.map((r) => `
    <li><div class="row"><span>${esc(r.riotId)} <span class="pill">${esc(r.role)}</span></span><b>${esc(fmt(r))}</b></div></li>`).join("")
    || `<li class="muted">Sin datos</li>`;
}
document.querySelectorAll("#lb-tabs button").forEach((b) => b.addEventListener("click", () => {
  board = b.dataset.board;
  document.querySelectorAll("#lb-tabs button").forEach((x) => x.classList.toggle("active", x === b));
  loadLeaderboard();
}));

async function loadRosterChamps() {
  const champs = await api("/champions/top?top=12");
  $("#roster-champs").innerHTML = champs.map((c) => `<li>${img(champImg(c.champion))}${esc(c.champion)} <b>${c.games}</b></li>`).join("")
    || `<li class="muted">Sin datos</li>`;
}

// ------------------------------------------------------------ redis panel
async function loadRedisPanel() {
  const [stats, keys] = await Promise.all([api("/system/cache"), api("/system/keys")]);
  $("#cache-stats").innerHTML = `
    <div class="stat"><b>${stats.hits}</b><span>hits</span></div>
    <div class="stat"><b>${stats.misses}</b><span>misses</span></div>
    <div class="stat"><b>${stats.hitRatio ?? "–"}%</b><span>hit ratio</span></div>
    <div class="stat"><b>${stats.riotCalls}</b><span>llamadas Riot</span></div>`;
  $("#ratelimit").innerHTML = stats.rateLimit.map((w) => `
    <div class="rl">Rate limit ${w.window_s}s: ${w.used}/${w.limit} <span class="muted">(reinicia en ${w.resets_in_s}s)</span>
      <div class="bar"><i style="width:${Math.min(100, (100 * w.used) / w.limit)}%"></i></div></div>`).join("");
  $("#keys").innerHTML = keys.map((k) => `
    <tr class="${k.ttl >= 0 && k.ttl < 15 ? "low" : ""}">
      <td title="${esc(k.key)}">${esc(k.key.replace(/([A-Za-z0-9_-]{12})[A-Za-z0-9_-]{20,}/, "$1…"))}</td>
      <td class="ttl">${k.ttl >= 0 ? `${k.ttl}s` : "∞"}</td>
    </tr>`).join("") || `<tr><td class="muted">Sin claves volátiles</td></tr>`;
}
$("#purge").addEventListener("click", async () => {
  const r = await api("/system/cache", { method: "DELETE" });
  toast(`${r.removed} claves de caché eliminadas (SCAN + UNLINK)`);
  loadRedisPanel();
});

// ---------------------------------------------------------------- boot
async function refreshAll() {
  await Promise.all([loadRoster(), loadLeaderboard(), loadRosterChamps(), loadRedisPanel(), loadHealth()]);
}

(async () => {
  try {
    const versions = await (await fetch("https://ddragon.leagueoflegends.com/api/versions.json")).json();
    DDRAGON = versions[0];
  } catch { /* offline: keep default version */ }
  await refreshAll().catch((e) => toast(e.message, true));
  setInterval(() => loadRedisPanel().catch(() => {}), 2000);
  setInterval(() => loadHealth(), 5000);
})();
