"""
Player info window for the Draft Helper mod.

Runs inside Minify while Dota is open:

- reads Dota's console in two ways, whichever works:
  * the console log Dota writes with the `-condebug` launch option (always passive),
  * a TCP connection to the console port: VConsole's protocol (`-vconsole`, port 29000)
    or plain text netcon (`-netconport`); over it `status` is sent every few seconds,
- reads every player's ping, packet loss and state from `status`,
- reads the `MDH_BRIDGE {...}` lines the in-game panel prints to the console
  (team, hero and Steam ID of each player: `status` only shows names),
- asks STRATZ for each player's public profile with the user's token,
- shows everything in a separate always-on-top window.

STRATZ only returns what a player made public, so private profiles stay private.
Everything fails quietly: no console connection, no token or no STRATZ answer
only leave the related cells empty.
"""

import json
import os
import re
import socket
import sys
import threading
import time
import types

STEAM64_BASE = 76561197960265728
DEFAULT_PORT = 29000  # VConsole's port
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

RECENT_MATCHES = 40  # asked for; tournament, lobby and old games are dropped afterwards
RECENT_DAYS = 60
MATCH_FIELDS = ("startDateTime", "leagueId", "lobbyType")
# Lobby types that are not public games (tournaments, private lobbies, bots, tutorials...)
NON_PUBLIC_LOBBIES = ("PRACTICE", "TOURNAMENT", "TUTORIAL", "COOP_VS_BOTS", "LOCAL", "SPECTATOR", "EVENT", "TEAM_MATCH")
RECENT_FIELDS = ("heroId", "position", "isVictory", "imp", "kills", "deaths", "assists")
SPAM_SHARE = 0.4  # one hero in at least 40% of the recent games...
SPAM_MIN_GAMES = 6  # ...and at least this many games
SCALAR_KINDS = ("SCALAR", "ENUM")

RANKS_RU = ("Без ранга", "Рекрут", "Страж", "Рыцарь", "Герой", "Легенда", "Властелин", "Божество", "Титан")
RANKS_EN = ("Uncalibrated", "Herald", "Guardian", "Crusader", "Archon", "Legend", "Ancient", "Divine", "Immortal")

# Survives Minify reloading this module, so a second click does not open a second window
_store = sys.modules.setdefault("_minify_draft_helper_companion", types.ModuleType("_minify_draft_helper_companion"))


def rank_parts(season_rank):
    """(medal 0-8, stars 0-5) from STRATZ's seasonRank (e.g. 54 = Legend 4)."""
    try:
        medal, stars = divmod(int(season_rank), 10)
    except (TypeError, ValueError):
        return None, 0
    return (medal, stars) if 0 <= medal <= 8 else (None, 0)


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


def _type_info(token, name):
    """({field: {kind, type, args}}, {input field: type}) for a STRATZ GraphQL type."""
    import draft_helper_stratz as stratz

    data = stratz.query(token, stratz._TYPE_QUERY, {"name": name})
    info = (data or {}).get("__type") or {}
    fields = {}
    for f in info.get("fields") or []:
        t = stratz._unwrap(f.get("type"))
        args = {a["name"]: stratz._unwrap(a.get("type")) for a in f.get("args") or []}
        fields[f["name"]] = {"kind": t.get("kind"), "type": t.get("name"), "args": args}
    inputs = {i["name"]: stratz._unwrap(i.get("type")) for i in info.get("inputFields") or []}
    return fields, inputs


def build_player_query(token):
    """
    Builds the player query only from fields STRATZ's schema really has.
    Returns (query, notes); notes explain which optional parts were left out and why.
    """
    notes = []
    player, _ = _type_info(token, "PlayerType")
    scalar = [f for f in PLAYER_FIELDS if player.get(f, {}).get("kind") in SCALAR_KINDS]
    account_part = ""
    if "steamAccount" in player:
        account, _ = _type_info(token, player["steamAccount"]["type"])
        names = [f for f in ACCOUNT_FIELDS if account.get(f, {}).get("kind") in SCALAR_KINDS]
        if names:
            account_part = " steamAccount { " + " ".join(names) + " }"

    recent_part = ""
    matches = player.get("matches")
    request = (matches or {}).get("args", {}).get("request")
    if not matches:
        notes.append("recent games: PlayerType has no 'matches'")
    elif not request:
        notes.append(f"recent games: 'matches' has no 'request' argument ({', '.join(matches['args'])})")
    else:
        _, request_inputs = _type_info(token, request.get("name"))
        match_fields, _ = _type_info(token, matches["type"])
        players = match_fields.get("players")
        if "take" not in request_inputs:
            notes.append(f"recent games: request has no 'take' ({', '.join(request_inputs)})")
        elif not players:
            notes.append(f"recent games: {matches['type']} has no 'players'")
        else:
            player_fields, _ = _type_info(token, players["type"])
            wanted = [f for f in RECENT_FIELDS if player_fields.get(f, {}).get("kind") in SCALAR_KINDS]
            by_id = "steamAccountId" in players["args"]
            if not by_id and "steamAccountId" in player_fields:
                wanted.append("steamAccountId")
            if "heroId" not in wanted:
                notes.append(f"recent games: {players['type']} has no 'heroId'")
            else:
                args = "(steamAccountId: $id)" if by_id else ""
                # Server-side filters when the request type supports them, client-side filtering covers the rest
                filters = [f"take: {RECENT_MATCHES}"]
                if "isLeague" in request_inputs:
                    filters.append("isLeague: false")
                if "startDateTime" in request_inputs:
                    filters.append(f"startDateTime: {int(time.time()) - RECENT_DAYS * 86400}")
                match_extra = [f for f in MATCH_FIELDS if match_fields.get(f, {}).get("kind") in SCALAR_KINDS]
                recent_part = (
                    f" matches(request: {{{', '.join(filters)}}}) {{ {' '.join(match_extra)} "
                    f"players{args} {{ {' '.join(wanted)} }} }}"
                )
                missing = [f for f in RECENT_FIELDS if f not in wanted]
                notes.append("recent games: ok" + (f" (missing: {', '.join(missing)})" if missing else ""))
                notes.append(
                    f"recent filters: request [{', '.join(filters)}], match fields [{', '.join(match_extra) or '-'}]"
                )

    body = " ".join(scalar) + account_part + recent_part
    return "query ($id: Long!) { player(steamAccountId: $id) { " + body + " } }", notes


def _position_number(value):
    text = str(value or "")
    return int(text[-1]) if text.startswith("POSITION_") and text[-1].isdigit() else 0


def summarize_recent(matches, aid, heroes, current_hero=0, keys=None):
    """Top heroes, spam flag, last 3 games and average IMP / winrate from the player's recent matches
    (STRATZ returns the newest match first)."""
    keys = keys or {}
    if not matches:
        # STRATZ returns no matches when the player hides their match data
        return {"games": 0, "reason": "hidden"}
    oldest = time.time() - RECENT_DAYS * 86400
    games = []
    for match in matches:
        match = match or {}
        if match.get("leagueId"):
            continue  # tournament game
        if str(match.get("lobbyType") or "").upper() in NON_PUBLIC_LOBBIES:
            continue
        started = match.get("startDateTime")
        if isinstance(started, (int, float)) and started < oldest:
            continue
        for p in match.get("players") or []:
            if p.get("steamAccountId") in (None, aid) and p.get("heroId"):
                games.append(p)
                break
    games = games[:25]
    n = len(games)
    if not n:
        return {"games": 0, "reason": "old"}
    wins = sum(1 for g in games if g.get("isVictory"))
    imps = [g["imp"] for g in games if isinstance(g.get("imp"), (int, float))]
    kills = sum(g.get("kills") or 0 for g in games)
    deaths = sum(g.get("deaths") or 0 for g in games)
    assists = sum(g.get("assists") or 0 for g in games)
    has_kda = any(k in games[0] for k in ("kills", "deaths", "assists"))

    by_hero = {}
    for g in games:
        h = by_hero.setdefault(int(g["heroId"]), {"n": 0, "wins": 0, "imps": [], "pos": {}})
        h["n"] += 1
        h["wins"] += 1 if g.get("isVictory") else 0
        if isinstance(g.get("imp"), (int, float)):
            h["imps"].append(g["imp"])
        pos = _position_number(g.get("position"))
        if pos:
            h["pos"][pos] = h["pos"].get(pos, 0) + 1

    top = []
    for hero_id, h in sorted(by_hero.items(), key=lambda kv: (kv[1]["n"], kv[1]["wins"]), reverse=True)[:3]:
        main_pos = max(h["pos"].items(), key=lambda kv: kv[1])[0] if h["pos"] else 0
        top.append(
            {
                "hero": heroes.get(hero_id, f"#{hero_id}"),
                "key": keys.get(hero_id, ""),
                "games": h["n"],
                "winrate": round(h["wins"] / h["n"] * 100),
                "pos": main_pos,
                "imp": round(sum(h["imps"]) / len(h["imps"])) if h["imps"] else None,
                "spam": h["n"] >= SPAM_MIN_GAMES and h["n"] / n >= SPAM_SHARE,
            }
        )
    last = []
    for g in games[:3]:
        hero_id = int(g["heroId"])
        last.append(
            {
                "hero": heroes.get(hero_id, f"#{hero_id}"),
                "key": keys.get(hero_id, ""),
                "k": g.get("kills") if has_kda else None,
                "d": g.get("deaths") if has_kda else None,
                "a": g.get("assists") if has_kda else None,
                "win": bool(g.get("isVictory")),
                "imp": g.get("imp") if isinstance(g.get("imp"), (int, float)) else None,
                "pos": _position_number(g.get("position")),
            }
        )
    current = by_hero.get(int(current_hero or 0))
    return {
        "last": last,
        "games": n,
        "wins": wins,
        "winrate": round(wins / n * 100),
        "imp": round(sum(imps) / len(imps), 1) if imps else None,
        "kda": round((kills + assists) / max(1, deaths), 1) if has_kda else None,
        "top": top,
        "currentGames": current["n"] if current else 0,
        "currentWinrate": round(current["wins"] / current["n"] * 100) if current else None,
    }


VCONSOLE_HEADER = 12  # type[4] version[4] length[2] handle[2], big endian
VCONSOLE_CMD_VERSION = 0x00D20000


def vconsole_command(text):
    """A VConsole2 'CMND' frame that runs `text` in the game console."""
    payload = text.encode("utf-8") + b"\0"
    length = VCONSOLE_HEADER + len(payload)
    return b"CMND" + VCONSOLE_CMD_VERSION.to_bytes(4, "big") + length.to_bytes(2, "big") + b"\0\0" + payload


def _printable(segment):
    return bool(segment) and all(b >= 32 or b in (9, 10, 13) for b in segment)


def vconsole_text(payload):
    """Text of a 'PRNT' frame: the payload starts with channel / colour fields, then a NUL-terminated string."""
    for offset in (28, 24, 32, 20, 16, 12, 8, 4, 0):
        segment = payload[offset:].split(b"\0", 1)[0]
        if _printable(segment):
            return segment.decode("utf-8", "replace")
    return ""


def looks_like_vconsole(data):
    return len(data) >= 4 and all(65 <= b <= 90 for b in data[:4])


class VConsoleReader:
    """Splits a VConsole2 byte stream into frames and returns the printed lines."""

    def __init__(self):
        self.buffer = b""

    def feed(self, chunk):
        self.buffer += chunk
        lines = []
        while len(self.buffer) >= VCONSOLE_HEADER:
            if not looks_like_vconsole(self.buffer):
                self.buffer = self.buffer[1:]  # resync
                continue
            length = int.from_bytes(self.buffer[8:10], "big")
            if length < VCONSOLE_HEADER:
                self.buffer = self.buffer[1:]
                continue
            if len(self.buffer) < length:
                break
            kind, payload = self.buffer[:4], self.buffer[VCONSOLE_HEADER:length]
            self.buffer = self.buffer[length:]
            if kind == b"PRNT":
                lines.extend(vconsole_text(payload).splitlines())
        return lines


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
    def __init__(self, port, token, heroes, russian=True, log=None, log_path=""):
        self.port = port
        self.token = token
        self.heroes = heroes  # {valve hero id: display name}
        try:
            self.hero_keys = hero_keys()  # {valve hero id: short name used by Valve's image CDN}
        except Exception:
            self.hero_keys = {}
        self.russian = russian
        self.log = log
        self.parser = StatusParser()
        self.stop_event = threading.Event()
        self.window = None
        self.connected = False
        self.protocol = ""
        self.error = ""
        self.log_path = log_path
        self.log_seen = 0.0  # last time the console log grew
        self.stats = {}  # account id -> dict, or None while loading / when missing
        self._query = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ lifecycle

    def start(self):
        import webview

        self.window = webview.create_window(
            "Draft Helper",
            html=WINDOW_HTML,
            width=960,
            height=680,
            resizable=True,
            on_top=True,
        )
        try:
            self.window.events.closed += self.stop
            self.window.events.loaded += lambda *a: self.push()
        except Exception:
            pass
        threading.Thread(target=self._console_loop, name="mdh-console", daemon=True).start()
        threading.Thread(target=self._log_loop, name="mdh-log", daemon=True).start()
        threading.Thread(target=self._stats_loop, name="mdh-stats", daemon=True).start()

    def stop(self, *args):
        self.stop_event.set()

    @property
    def alive(self):
        return not self.stop_event.is_set()

    # ------------------------------------------------------------------ console

    def _feed_lines(self, lines):
        changed = False
        for line in lines:
            changed |= self.parser.feed(line.rstrip("\r"))
        if changed:
            self.push()

    def _console_loop(self):
        while self.alive:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
                    self._serve(sock)
            except OSError as e:
                self.error = str(e)
            self.connected, self.protocol = False, ""
            self.push()
            self.stop_event.wait(RECONNECT_DELAY)

    def _serve(self, sock):
        # VConsole sends binary frames right after connecting; netcon stays silent and speaks plain text
        sock.settimeout(1.5)
        try:
            first = sock.recv(65536)
        except socket.timeout:
            first = b""
        vconsole = looks_like_vconsole(first)
        self.connected, self.error = True, ""
        self.protocol = "vconsole" if vconsole else "netcon"
        self.push()
        reader = VConsoleReader()
        text_buffer = b""
        pending = first
        last_status = 0.0
        sock.settimeout(0.5)
        while self.alive:
            if time.time() - last_status >= STATUS_INTERVAL:
                sock.sendall(vconsole_command("status") if vconsole else b"status\n")
                last_status = time.time()
            if pending:
                chunk, pending = pending, b""
            else:
                try:
                    chunk = sock.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    return
            if vconsole:
                self._feed_lines(reader.feed(chunk))
            else:
                text_buffer += chunk
                lines = text_buffer.split(b"\n")
                text_buffer = lines.pop()
                self._feed_lines(raw.decode("utf-8", "replace") for raw in lines)

    def _log_loop(self):
        """Follows console.log (written by Dota with -condebug), starting at its current end."""
        position = None
        rest = b""
        while self.alive:
            try:
                size = os.path.getsize(self.log_path) if self.log_path else -1
            except OSError:
                size = -1
            if size < 0:
                position = None
            else:
                if position is None or size < position:
                    position = size if position is None else 0  # first look: skip history; truncated: restart
                    rest = b""
                if size > position:
                    with open(self.log_path, "rb") as file:
                        file.seek(position)
                        data = file.read(size - position)
                    position = size
                    self.log_seen = time.time()
                    lines = (rest + data).split(b"\n")
                    rest = lines.pop()
                    self._feed_lines(raw.decode("utf-8", "replace") for raw in lines)
            self.stop_event.wait(0.5)

    @property
    def log_active(self):
        return time.time() - self.log_seen < 60

    # ------------------------------------------------------------------ STRATZ

    def _build_query(self):
        query, notes = build_player_query(self.token)
        if self.log:
            for note in notes:
                if not note.endswith(": ok"):
                    self.log(f"[Draft Helper] player info: {note}")
        return query

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
            "protocol": self.protocol,
            "logActive": self.log_active,
            "logPath": self.log_path,
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
            "heroKey": self.hero_keys.get(int(hero or 0), ""),
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
                    "medal": rank_parts(account.get("seasonRank"))[0],
                    "stars": rank_parts(account.get("seasonRank"))[1],
                    "matches": matches,
                    "winrate": round(wins / matches * 100, 1) if matches else None,
                    "private": bool(account.get("isAnonymous")) or account.get("isStratzPublic") is False,
                    "smurf": bool(account.get("smurfFlag")),
                    "error": stats.get("error", ""),
                    "recent": summarize_recent(stats.get("matches"), aid, self.heroes, hero, self.hero_keys),
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


def console_log_path():
    """Where Dota writes the console with -condebug: <library>/steamapps/common/dota 2 beta/game/dota/console.log"""
    try:
        from core import steam

        if steam.LIBRARY:
            return os.path.join(steam.LIBRARY, "steamapps", "common", "dota 2 beta", "game", "dota", "console.log")
    except Exception:
        pass
    return ""


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
    companion = Companion(port, token, heroes, russian=russian, log=log, log_path=console_log_path())
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
  tr.details td { padding: 2px 6px 8px 18px; border-bottom: 1px solid #ffffff14; color: #9fb0bf; font-size: 12px; white-space: normal; }
  .det { display: flex; gap: 22px; }
  .det .col { min-width: 0; }
  .det .ct { color: #6b7782; font-size: 11px; text-transform: uppercase; letter-spacing: .5px; margin-bottom: 2px; }
  .det .line { display: flex; align-items: center; gap: 6px; height: 22px; white-space: nowrap; }
  img.hi { width: 26px; height: 26px; border-radius: 3px; object-fit: cover; background: #ffffff10; }
  img.hm { width: 30px; height: 30px; border-radius: 3px; object-fit: cover; vertical-align: middle; background: #ffffff10; }
  .kda { font-family: Consolas, "Cascadia Mono", monospace; color: #d6dde4; min-width: 64px; display: inline-block; }
  .wl { font-weight: 600; width: 14px; display: inline-block; }
  .medal { position: relative; display: inline-block; width: 34px; height: 34px; vertical-align: middle; }
  .medal img { position: absolute; inset: 0; width: 34px; height: 34px; }
  .hero { color: #d6dde4; }
  .tag.spam { background: #e8c76a33; color: #f3d98b; }
  .tag.comfort { background: #7fd36b26; color: #a9e69b; }
  .tag.new { background: #ffffff14; color: #9fb0bf; }
  .legend { color: #6b7782; font-size: 11px; margin-top: 10px; white-space: normal; }
</style></head>
<body>
<header><b>Draft Helper</b><span id="conn" class="meta"></span><span id="match" class="meta"></span><span id="upd" class="meta"></span></header>
<main id="main"><div class="hint">…</div></main>
<script>
const L = (ru, en) => (window.__ru === false ? en : ru);
function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function pingClass(p) { return p == null ? "muted" : p >= 120 ? "bad" : p >= 70 ? "warn" : "good"; }
function impClass(v) { return v == null ? "muted" : v >= 5 ? "good" : v <= -5 ? "bad" : ""; }
function signed(v) { return v == null ? "—" : (v > 0 ? "+" : "") + v; }
const HERO_ICON = "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/icons/";
const RANK_IMG = "https://cdn.stratz.com/images/dota2/seasonal_rank/";
function heroIcon(key, name, cls) {
  if (!key) return "<span class=muted>" + esc(name || "") + "</span>";
  return "<img class=" + (cls || "hi") + " src='" + HERO_ICON + esc(key) + ".png' title='" + esc(name) + "' alt='" + esc(name) +
         "' onerror=\"this.replaceWith(document.createTextNode(this.alt))\">";
}
function medal(r) {
  if (r.medal == null) return esc(r.rank || "");
  let h = "<span class=medal title='" + esc(r.rank) + "'><img src='" + RANK_IMG + "medal_" + r.medal + ".png' onerror=\"this.parentNode.textContent=this.parentNode.title\">";
  if (r.stars >= 1 && r.stars <= 5 && r.medal >= 1 && r.medal <= 7) h += "<img src='" + RANK_IMG + "star_" + r.stars + ".png' onerror='this.remove()'>";
  return h + "</span>";
}
function details(r) {
  const s = r.recent;
  if (!s) return "";
  if (!s.games) {
    const msg = s.reason === "hidden"
      ? L("Игры скрыты: игрок не открыл статистику матчей.", "Games are hidden: the player does not share match data.")
      : L("Нет свежих публичных игр за последние 60 дней (турниры и лобби не считаются).", "No recent public games in the last 60 days (tournaments and lobbies are not counted).");
    return "<tr class=details><td colspan=8 class=muted>" + msg + "</td></tr>";
  }
  const top = s.top.map(t => "<div class=line>" + heroIcon(t.key, t.hero) + t.games + L(" игр", " g") + " · " +
    "<span class='" + wrClass(t.winrate) + "'>" + t.winrate + "%</span>" + (t.pos ? " · " + L("поз. ", "pos ") + t.pos : "") +
    (t.imp != null ? " · IMP <span class='" + impClass(t.imp) + "'>" + signed(t.imp) + "</span>" : "") +
    (t.spam ? "<span class='tag spam'>" + L("спамит", "spams") + "</span>" : "") + "</div>").join("");
  const last = (s.last || []).map(g => "<div class=line>" + heroIcon(g.key, g.hero) +
    "<span class=kda>" + (g.k == null ? "—" : g.k + "/" + g.d + "/" + g.a) + "</span>" +
    "<span class='wl " + (g.win ? "good" : "bad") + "'>" + (g.win ? L("В", "W") : L("П", "L")) + "</span>" +
    (g.imp != null ? "IMP <span class='" + impClass(g.imp) + "'>" + signed(g.imp) + "</span>" : "") + "</div>").join("");
  let form = "<div class=line>IMP <b class='" + impClass(s.imp) + "'>" + signed(s.imp) + "</b></div>" +
    "<div class=line>" + s.wins + "-" + (s.games - s.wins) + " (<span class='" + wrClass(s.winrate) + "'>" + s.winrate + "%</span>)</div>";
  if (r.hero) {
    form += "<div class=line>" + heroIcon(r.heroKey, r.hero) + (s.currentGames >= 3
      ? "<span class='tag comfort'>" + s.currentGames + L(" игр, ", " g, ") + s.currentWinrate + "%</span>"
      : "<span class='tag new'>" + L("мало игр", "few games") + " (" + s.currentGames + ")</span>") + "</div>";
  }
  return "<tr class=details><td colspan=8><div class=det>" +
    "<div class=col><div class=ct>" + L("Топ героев · ", "Top heroes · ") + s.games + L(" игр", " games") + "</div>" + top + "</div>" +
    "<div class=col><div class=ct>" + L("Последние 3 игры", "Last 3 games") + "</div>" + last + "</div>" +
    "<div class=col><div class=ct>" + L("Форма", "Form") + "</div>" + form + "</div>" +
    "</div></td></tr>";
}
function wrClass(w) { return w == null ? "muted" : w >= 52 ? "good" : w <= 48 ? "bad" : ""; }
function table(rows) {
  let h = "<table><colgroup><col style='width:26%'><col style='width:8%'><col style='width:8%'><col style='width:8%'>" +
          "<col style='width:12%'><col style='width:18%'><col style='width:10%'><col style='width:10%'></colgroup><tr><th>" + L("Игрок","Player") + "</th><th>" + L("Герой","Hero") + "</th><th class=num>" + L("Пинг","Ping") +
          "</th><th class=num>" + L("Потери","Loss") + "</th><th>" + L("Статус","State") + "</th><th>" + L("Ранг","Rank") +
          "</th><th class=num>" + L("Матчи","Matches") + "</th><th class=num>WR</th></tr>";
  for (const r of rows) {
    let profile;
    if (r.bot) profile = "<td colspan=3 class=muted>" + L("бот","bot") + "</td>";
    else if (r.hidden) profile = "<td colspan=3 class=muted>" + L("нет Steam ID","no Steam ID") + "</td>";
    else if (r.loading) profile = "<td colspan=3 class=muted>" + L("загрузка…","loading…") + "</td>";
    else if (r.error) profile = "<td colspan=3 class=bad>" + esc(r.error) + "</td>";
    else if (r.private) profile = "<td colspan=3 class=muted>" + L("профиль скрыт","private profile") + "</td>";
    else if (r.matches != null) profile = "<td>" + medal(r) + (r.smurf ? "<span class='tag smurf'>" + L("смурф?","smurf?") + "</span>" : "") +
        "</td><td class=num>" + r.matches + "</td><td class='num " + wrClass(r.winrate) + "'>" + (r.winrate == null ? "—" : r.winrate.toFixed(1) + "%") + "</td>";
    else profile = "<td colspan=3 class=muted>—</td>";
    const loss = r.loss == null ? "—" : r.loss + "%";
    h += "<tr class='" + (r.local ? "local" : "") + "'><td>" + esc(r.name) + (r.local ? "<span class=tag>" + L("вы","you") + "</span>" : "") +
         "</td><td>" + heroIcon(r.heroKey, r.hero, "hm") + "</td><td class='num " + pingClass(r.ping) + "'>" + (r.ping == null ? "—" : r.ping) +
         "</td><td class='num " + (r.loss ? "bad" : "") + "'>" + loss + "</td><td>" + esc(r.state) + "</td>" + profile + "</tr>" + details(r);
  }
  return h + "</table>";
}
window.render = function (m) {
  window.__ru = m.russian;
  const parts = [];
  if (m.connected) parts.push("<span class=good>" + L("консоль подключена","console connected") + " (" + esc(m.protocol) + ", " + m.port + ")</span>");
  else parts.push("<span class=muted>" + L("порт ","port ") + m.port + L(": нет связи",": not connected") + "</span>");
  parts.push(m.logActive ? "<span class=good>console.log ✓</span>" : "<span class=muted>console.log —</span>");
  document.getElementById("conn").innerHTML = parts.join(" · ");
  document.getElementById("match").textContent = m.matchId ? L("матч ","match ") + m.matchId + (m.gameState ? " · " + m.gameState : "") : "";
  document.getElementById("upd").textContent = m.time;
  const main = document.getElementById("main");
  if (!m.players.length) {
    main.innerHTML = "<div class=hint>" + ((m.connected || m.logActive)
      ? L("Ждём игру: данные появятся, когда начнётся загрузка или драфт.", "Waiting for a game: data appears once loading or the draft starts.")
      : L("Нет связи с Дотой. Добавьте в параметры запуска Доты <b>-condebug</b> (герои и профили) и <b>-vconsole</b> (пинг в фоне), перезапустите Доту. Окно подключится само.",
          "No link to Dota. Add <b>-condebug</b> (heroes and profiles) and <b>-vconsole</b> (background ping) to Dota's launch options and restart Dota. The window connects by itself.")) + "</div>";
    return;
  }
  const groups = { ally: [], enemy: [], other: [] };
  for (const p of m.players) (groups[p.side] || groups.other).push(p);
  let h = "";
  if (groups.ally.length) h += "<h3 class=ally>" + L("Союзники","Allies") + "</h3>" + table(groups.ally);
  if (groups.enemy.length) h += "<h3 class=enemy>" + L("Враги","Enemies") + "</h3>" + table(groups.enemy);
  if (groups.other.length) h += "<h3 class=other>" + L("Игроки","Players") + "</h3>" + table(groups.other);
  if (!m.connected && !m.players.some(p => p.ping != null)) h += "<div class=hint>" + L("Пинг появится, когда окно подключится к консоли (-vconsole) или когда в консоли выполнится status.", "Ping appears once the window reaches the console (-vconsole) or when status runs in the console.") + "</div>";
  if (!m.bridge) h += "<div class=hint>" + L("Герои и профили появятся, когда панель Draft Helper загрузится на экране драфта (включите «Player info window» в настройках и перепатчьте).",
                                                   "Heroes and profiles appear once the Draft Helper panel loads in the draft (enable “Player info window” and patch again).") + "</div>";
  else if (!m.token) h += "<div class=hint>" + L("Для рангов и винрейта нужен токен STRATZ.", "A STRATZ token is needed for ranks and winrates.") + "</div>";
  if (m.players.some(p => p.recent)) h += "<div class=legend>" + L("IMP — оценка STRATZ того, насколько полезно игрок сыграл матч: 0 — средний уровень для этого героя и позиции, плюс — лучше, минус — хуже. «Спамит» — один герой в 40%+ последних игр.",
    "IMP is STRATZ's rating of how useful a player was in a match: 0 is average for that hero and position, plus is better, minus is worse. “Spams” means one hero in 40%+ of recent games.") + "</div>";
  main.innerHTML = h;
};
</script>
</body></html>
"""


def parse_port(value):
    try:
        port = int(str(value or "").strip())
    except ValueError:
        return 0
    return port if 0 < port < 65536 else 0


def hero_keys():
    """{valve hero id: short name}, e.g. 1 -> "antimage" (npc_dota_hero_antimage)."""
    import draft_helper_core

    raw = draft_helper_core.load_cached()
    if not raw:
        return {}
    info = raw["heroinfo"]
    ids = info.get("valveids", {})
    vnames = info.get("vnames", {})
    return {
        int(ids[n]): draft_helper_core._npc_name(n, vnames).replace("npc_dota_hero_", "")
        for n in info["heronames"]
        if n in ids
    }


def hero_names():
    """{valve hero id: name} from the cached dotapicker data."""
    import draft_helper_core

    raw = draft_helper_core.load_cached()
    if not raw:
        return {}
    info = raw["heroinfo"]
    ids = info.get("valveids", {})
    return {int(ids[n]): n for n in info["heronames"] if n in ids}


def open_when_minify_is_ready(port, token, heroes, russian=True, log=None, timeout=120):
    """Used at Minify start-up: waits for Minify's own window, then opens the player window."""

    def worker():
        try:
            import webview
        except Exception:
            return
        for _ in range(timeout):
            if getattr(webview, "windows", None):
                break
            time.sleep(1)
        else:
            return
        time.sleep(3)
        try:
            open_window(port, token, heroes, russian=russian, log=log)
        except Exception as e:
            if log:
                log(f"[Draft Helper] could not open the player window: {type(e).__name__}: {e}")

    threading.Thread(target=worker, name="mdh-autostart", daemon=True).start()
