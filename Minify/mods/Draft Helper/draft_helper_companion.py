"""
Player info window for the Draft Helper mod.

Runs inside Minify while Dota is open:

- connects to Dota's console through the official `-netconport <port>` launch option,
- sends `status` every few seconds and reads every player's ping, packet loss and state,
- reads the `MDH_BRIDGE {...}` lines the in-game panel prints to the console
  (team, hero and Steam ID of each player: `status` only shows names),
- asks STRATZ for each player's public profile with the user's token,
- shows everything in a separate always-on-top window.

STRATZ only returns what a player made public, so private profiles stay private.
Everything fails quietly: no console connection, no token or no STRATZ answer
only leave the related cells empty.
"""

import json
import re
import socket
import sys
import threading
import time
import types

STEAM64_BASE = 76561197960265728
BRIDGE_TAG = "MDH_BRIDGE "
STATUS_INTERVAL = 3.0
RECONNECT_DELAY = 5.0
STATS_PAUSE = 0.6

# "[Client]    1     00:22   21    0     active  80000 'Monster Energy'"
STATUS_ROW = re.compile(r"^\s*(?:\[[^\]]*\]\s*)*(\d+)\s+(BOT|[\d:]+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\d+)\s+'(.*)'\s*$")
MATCH_ID = re.compile(r"Lobby MatchID:\s*(\d+)")
GAME_STATE = re.compile(r"GameState:\s*(\S+)")

PLAYER_FIELDS = ("matchCount", "winCount", "firstMatchDate", "lastMatchDate")
ACCOUNT_FIELDS = ("name", "seasonRank", "isAnonymous", "smurfFlag", "isStratzPublic")

RANKS_RU = ("Без ранга", "Рекрут", "Страж", "Рыцарь", "Герой", "Легенда", "Властелин", "Божество", "Титан")
RANKS_EN = ("Uncalibrated", "Herald", "Guardian", "Crusader", "Archon", "Legend", "Ancient", "Divine", "Immortal")

# Survives Minify reloading this module, so a second click does not open a second window
_store = sys.modules.setdefault("_minify_draft_helper_companion", types.ModuleType("_minify_draft_helper_companion"))


def rank_text(season_rank, russian=True):
    try:
        value = int(season_rank)
    except (TypeError, ValueError):
        return ""
    medal, stars = divmod(value, 10)
    names = RANKS_RU if russian else RANKS_EN
    if not 0 <= medal < len(names):
        return ""
    if medal in (0, 8) or not stars:
        return names[medal]
    return f"{names[medal]} {stars}"


def account_id(steamid):
    try:
        value = int(steamid)
    except (TypeError, ValueError):
        return 0
    if value > STEAM64_BASE:
        value -= STEAM64_BASE
    return value if value > 0 else 0


class StatusParser:
    """Collects `status` tables and bridge lines from the console stream."""

    def __init__(self):
        self.rows = {}
        self._pending = None
        self.bridge = None
        self.match_id = None
        self.game_state = None

    def feed(self, line):
        """Returns True when something visible changed."""
        if BRIDGE_TAG in line:
            try:
                self.bridge = json.loads(line.split(BRIDGE_TAG, 1)[1].strip())
                return True
            except ValueError:
                return False
        if "---------players--------" in line:
            self._pending = {}
            return False
        if self._pending is not None:
            m = STATUS_ROW.match(line)
            if m:
                slot, played, ping, loss, state, rate, name = m.groups()
                if played != "BOT" or name != "SourceTV":
                    self._pending[name] = {
                        "slot": int(slot),
                        "bot": played == "BOT",
                        "time": played,
                        "ping": int(ping),
                        "loss": int(loss),
                        "state": state,
                    }
                return False
        m = MATCH_ID.search(line)
        if m:
            self.match_id = m.group(1)
        m = GAME_STATE.search(line)
        if m:
            self.game_state = m.group(1).replace("DOTA_GAMERULES_STATE_", "")
        if "#end" in line and self._pending is not None:
            self.rows = self._pending
            self._pending = None
            return True
        return False


class Companion:
    def __init__(self, port, token, heroes, russian=True, log=None):
        self.port = port
        self.token = token
        self.heroes = heroes  # {valve hero id: display name}
        self.russian = russian
        self.log = log
        self.parser = StatusParser()
        self.stop_event = threading.Event()
        self.window = None
        self.connected = False
        self.error = ""
        self.stats = {}  # account id -> dict, or None while loading / when missing
        self._query = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ lifecycle

    def start(self):
        import webview

        self.window = webview.create_window(
            "Draft Helper",
            html=WINDOW_HTML,
            width=820,
            height=470,
            resizable=True,
            on_top=True,
        )
        try:
            self.window.events.closed += self.stop
            self.window.events.loaded += lambda *a: self.push()
        except Exception:
            pass
        threading.Thread(target=self._console_loop, name="mdh-console", daemon=True).start()
        threading.Thread(target=self._stats_loop, name="mdh-stats", daemon=True).start()

    def stop(self, *args):
        self.stop_event.set()

    @property
    def alive(self):
        return not self.stop_event.is_set()

    # ------------------------------------------------------------------ console

    def _console_loop(self):
        while self.alive:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
                    sock.settimeout(0.5)
                    self.connected, self.error = True, ""
                    self.push()
                    buffer = b""
                    last_status = 0.0
                    while self.alive:
                        if time.time() - last_status >= STATUS_INTERVAL:
                            sock.sendall(b"status\n")
                            last_status = time.time()
                        try:
                            chunk = sock.recv(65536)
                        except socket.timeout:
                            continue
                        if not chunk:
                            break
                        buffer += chunk
                        lines = buffer.split(b"\n")
                        buffer = lines.pop()
                        changed = False
                        for raw in lines:
                            changed |= self.parser.feed(raw.decode("utf-8", "replace").rstrip("\r"))
                        if changed:
                            self.push()
            except OSError as e:
                self.error = str(e)
            self.connected = False
            self.push()
            self.stop_event.wait(RECONNECT_DELAY)

    # ------------------------------------------------------------------ STRATZ

    def _build_query(self):
        """Builds the player query from the fields STRATZ really has (checked once by introspection)."""
        import draft_helper_stratz as stratz

        def fields_of(type_name):
            data = stratz.query(self.token, stratz._TYPE_QUERY, {"name": type_name})
            info = (data or {}).get("__type") or {}
            out = {}
            for f in info.get("fields") or []:
                t = stratz._unwrap(f.get("type"))
                out[f["name"]] = (t.get("kind"), t.get("name"))
            return out

        player = fields_of("PlayerType")
        scalar = [f for f in PLAYER_FIELDS if player.get(f, ("",))[0] in ("SCALAR", "ENUM")]
        account_part = ""
        if "steamAccount" in player:
            account = fields_of(player["steamAccount"][1])
            names = [f for f in ACCOUNT_FIELDS if account.get(f, ("",))[0] in ("SCALAR", "ENUM")]
            if names:
                account_part = " steamAccount { " + " ".join(names) + " }"
        body = " ".join(scalar) + account_part
        return "query ($id: Long!) { player(steamAccountId: $id) { " + body + " } }"

    def _stats_loop(self):
        import draft_helper_stratz as stratz

        while self.alive:
            ids = []
            bridge = self.parser.bridge or {}
            for p in bridge.get("players") or []:
                aid = account_id(p.get("steamid"))
                if aid and aid not in self.stats:
                    ids.append(aid)
            if ids and self.token:
                try:
                    if self._query is None:
                        self._query = self._build_query()
                    for aid in ids:
                        if not self.alive:
                            break
                        with self._lock:
                            self.stats[aid] = None
                        try:
                            data = stratz.query(self.token, self._query, {"id": aid})
                            self.stats[aid] = data.get("player") or {}
                        except stratz.StratzError as e:
                            self.stats[aid] = {"error": str(e)[:80]}
                        self.push()
                        time.sleep(STATS_PAUSE)
                except Exception as e:
                    if self.log:
                        self.log(f"[Draft Helper] player info: {type(e).__name__}: {e}")
                    self.stop_event.wait(10)
            self.stop_event.wait(1.5)

    # ------------------------------------------------------------------ view

    def model(self):
        bridge = self.parser.bridge or {}
        rows = dict(self.parser.rows)
        local_team = bridge.get("localTeam")
        players = []
        seen = set()
        for p in bridge.get("players") or []:
            name = p.get("name") or ""
            status = rows.get(name, {})
            seen.add(name)
            aid = account_id(p.get("steamid"))
            players.append(self._row(name, p.get("team"), local_team, p.get("hero"), status, aid, p.get("local")))
        for name, status in rows.items():
            if name not in seen:
                players.append(self._row(name, None, local_team, 0, status, 0, False))
        if self.connected:
            connection = "ok"
        elif self.error:
            connection = self.error
        else:
            connection = "connecting"
        return {
            "connected": self.connected,
            "connection": connection,
            "port": self.port,
            "matchId": self.parser.match_id,
            "gameState": self.parser.game_state,
            "bridge": bool(bridge),
            "token": bool(self.token),
            "russian": self.russian,
            "players": players,
            "time": time.strftime("%H:%M:%S"),
        }

    def _row(self, name, team, local_team, hero, status, aid, local):
        side = ""
        if team and local_team:
            side = "ally" if team == local_team else "enemy"
        stats = self.stats.get(aid) if aid else None
        row = {
            "name": name,
            "side": side,
            "local": bool(local),
            "hero": self.heroes.get(int(hero or 0), ""),
            "ping": status.get("ping"),
            "loss": status.get("loss"),
            "state": status.get("state", ""),
            "bot": status.get("bot", False),
            "hidden": not aid,
            "loading": bool(aid) and aid in self.stats and self.stats[aid] is None,
        }
        if stats:
            account = stats.get("steamAccount") or {}
            matches = stats.get("matchCount") or 0
            wins = stats.get("winCount") or 0
            row.update(
                {
                    "rank": rank_text(account.get("seasonRank"), self.russian),
                    "matches": matches,
                    "winrate": round(wins / matches * 100, 1) if matches else None,
                    "private": bool(account.get("isAnonymous")) or account.get("isStratzPublic") is False,
                    "smurf": bool(account.get("smurfFlag")),
                    "error": stats.get("error", ""),
                }
            )
        return row

    def push(self):
        if not self.window:
            return
        try:
            self.window.evaluate_js("window.render && window.render(" + json.dumps(self.model()) + ")")
        except Exception:
            pass


def open_window(port, token, heroes, russian=True, log=None):
    """Opens the window, or reuses the one already open. Returns a short status string."""
    current = getattr(_store, "companion", None)
    if current is not None and current.alive:
        try:
            current.window.show()
            current.window.restore()
        except Exception:
            pass
        return "already open"
    companion = Companion(port, token, heroes, russian=russian, log=log)
    companion.start()
    _store.companion = companion
    return "opened"


WINDOW_HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Draft Helper</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; background: #0b0f14; color: #d6dde4; font: 13px/1.35 "Segoe UI", system-ui, sans-serif; }
  header { padding: 8px 12px; background: #121820; border-bottom: 1px solid #ffffff14; display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap; }
  header b { color: #e8c76a; letter-spacing: 1px; text-transform: uppercase; }
  .meta { color: #8a96a3; font-size: 12px; }
  .bad { color: #e8695a; } .good { color: #7fd36b; } .warn { color: #e8c76a; } .muted { color: #6b7782; }
  main { padding: 6px 12px 12px; }
  h3 { margin: 10px 0 4px; font-size: 12px; text-transform: uppercase; letter-spacing: 1px; }
  h3.ally { color: #7fd36b; } h3.enemy { color: #e8695a; } h3.other { color: #8a96a3; }
  table { width: 100%; border-collapse: collapse; table-layout: fixed; }
  td, th { overflow: hidden; text-overflow: ellipsis; }
  th { text-align: left; color: #8a96a3; font-weight: normal; font-size: 12px; padding: 3px 6px; border-bottom: 1px solid #ffffff14; }
  td { padding: 4px 6px; border-bottom: 1px solid #ffffff0a; white-space: nowrap; }
  td.num, th.num { text-align: right; }
  tr.local td { background: #e8c76a14; }
  .tag { font-size: 11px; padding: 0 5px; border-radius: 3px; background: #ffffff14; margin-left: 4px; }
  .tag.smurf { background: #e8695a33; color: #ffb3a8; }
  .hint { color: #8a96a3; padding: 12px 0; white-space: normal; }
</style></head>
<body>
<header><b>Draft Helper</b><span id="conn" class="meta"></span><span id="match" class="meta"></span><span id="upd" class="meta"></span></header>
<main id="main"><div class="hint">…</div></main>
<script>
const L = (ru, en) => (window.__ru === false ? en : ru);
function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function pingClass(p) { return p == null ? "muted" : p >= 120 ? "bad" : p >= 70 ? "warn" : "good"; }
function wrClass(w) { return w == null ? "muted" : w >= 52 ? "good" : w <= 48 ? "bad" : ""; }
function table(rows) {
  let h = "<table><colgroup><col style='width:23%'><col style='width:17%'><col style='width:7%'><col style='width:8%'>" +
          "<col style='width:10%'><col style='width:19%'><col style='width:8%'><col style='width:8%'></colgroup><tr><th>" + L("Игрок","Player") + "</th><th>" + L("Герой","Hero") + "</th><th class=num>" + L("Пинг","Ping") +
          "</th><th class=num>" + L("Потери","Loss") + "</th><th>" + L("Статус","State") + "</th><th>" + L("Ранг","Rank") +
          "</th><th class=num>" + L("Матчи","Matches") + "</th><th class=num>WR</th></tr>";
  for (const r of rows) {
    let profile;
    if (r.bot) profile = "<td colspan=3 class=muted>" + L("бот","bot") + "</td>";
    else if (r.hidden) profile = "<td colspan=3 class=muted>" + L("нет Steam ID","no Steam ID") + "</td>";
    else if (r.loading) profile = "<td colspan=3 class=muted>" + L("загрузка…","loading…") + "</td>";
    else if (r.error) profile = "<td colspan=3 class=bad>" + esc(r.error) + "</td>";
    else if (r.private) profile = "<td colspan=3 class=muted>" + L("профиль скрыт","private profile") + "</td>";
    else if (r.matches != null) profile = "<td>" + esc(r.rank) + (r.smurf ? "<span class='tag smurf'>" + L("смурф?","smurf?") + "</span>" : "") +
        "</td><td class=num>" + r.matches + "</td><td class='num " + wrClass(r.winrate) + "'>" + (r.winrate == null ? "—" : r.winrate.toFixed(1) + "%") + "</td>";
    else profile = "<td colspan=3 class=muted>—</td>";
    const loss = r.loss == null ? "—" : r.loss + "%";
    h += "<tr class='" + (r.local ? "local" : "") + "'><td>" + esc(r.name) + (r.local ? "<span class=tag>" + L("вы","you") + "</span>" : "") +
         "</td><td>" + esc(r.hero) + "</td><td class='num " + pingClass(r.ping) + "'>" + (r.ping == null ? "—" : r.ping) +
         "</td><td class='num " + (r.loss ? "bad" : "") + "'>" + loss + "</td><td>" + esc(r.state) + "</td>" + profile + "</tr>";
  }
  return h + "</table>";
}
window.render = function (m) {
  window.__ru = m.russian;
  document.getElementById("conn").innerHTML = m.connected
    ? "<span class=good>" + L("консоль подключена","console connected") + "</span>"
    : "<span class=bad>" + L("нет связи с консолью Доты (порт ","no Dota console (port ") + m.port + "): " + esc(m.connection) + "</span>";
  document.getElementById("match").textContent = m.matchId ? L("матч ","match ") + m.matchId + (m.gameState ? " · " + m.gameState : "") : "";
  document.getElementById("upd").textContent = m.time;
  const main = document.getElementById("main");
  if (!m.players.length) {
    main.innerHTML = "<div class=hint>" + (m.connected
      ? L("Ждём игру: данные появятся, когда начнётся загрузка или драфт.", "Waiting for a game: data appears once loading or the draft starts.")
      : L("Запустите Доту с параметром -netconport ", "Start Dota with -netconport ") + m.port +
        L(" и укажите тот же порт в настройках Draft Helper.", " and set the same port in the Draft Helper settings.")) + "</div>";
    return;
  }
  const groups = { ally: [], enemy: [], other: [] };
  for (const p of m.players) (groups[p.side] || groups.other).push(p);
  let h = "";
  if (groups.ally.length) h += "<h3 class=ally>" + L("Союзники","Allies") + "</h3>" + table(groups.ally);
  if (groups.enemy.length) h += "<h3 class=enemy>" + L("Враги","Enemies") + "</h3>" + table(groups.enemy);
  if (groups.other.length) h += "<h3 class=other>" + L("Игроки","Players") + "</h3>" + table(groups.other);
  if (!m.bridge) h += "<div class=hint>" + L("Герои и профили появятся, когда панель Draft Helper загрузится на экране драфта (включите «Player info window» в настройках и перепатчьте).",
                                                   "Heroes and profiles appear once the Draft Helper panel loads in the draft (enable “Player info window” and patch again).") + "</div>";
  else if (!m.token) h += "<div class=hint>" + L("Для рангов и винрейта нужен токен STRATZ.", "A STRATZ token is needed for ranks and winrates.") + "</div>";
  main.innerHTML = h;
};
</script>
</body></html>
"""
