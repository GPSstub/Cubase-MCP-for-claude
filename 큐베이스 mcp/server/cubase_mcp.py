"""MCP server that controls Cubase 13 through the MIDI Remote script mcp_bridge.js and two loopMIDI
ports: every mixer channel, the selected track's inserts/plugin parameters/sends, transport,
tempo/locators, every Cubase key command, plus offline audio file analysis."""

import json
import os
import re
import threading
import time

from mcp.server.fastmcp import FastMCP

import audio_analysis

TO_CUBASE = "CubaseMCP_ToCubase"
FROM_CUBASE = "CubaseMCP_FromCubase"
HEADER = [0x7D, 0x43, 0x4D]  # after F0
MAX_SLOTS = 16
MAX_BANKS = 64
CHANNEL_FIELDS = ("volume", "pan", "mute", "solo", "rec", "monitor", "selected")

COMMANDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "commands.json")


def load_commands():
    with open(COMMANDS_FILE, encoding="utf-8") as f:
        return ["%s / %s" % (c, n) for c, n in json.load(f)]


COMMANDS = load_commands()


def encode(obj):
    text = json.dumps(obj, ensure_ascii=True, separators=(",", ":"))
    return HEADER + [b & 0x7F for b in text.encode("ascii")]


def decode(data):
    data = list(data)
    if data and data[0] == 0xF0:
        data = data[1:]
    if data[:3] != HEADER:
        return None
    body = bytes(b for b in data[3:] if b != 0xF7).decode("ascii", "replace")
    try:
        return json.loads(body)
    except ValueError:
        return None


class Bridge:
    """Mirror of Cubase state, fed by SysEx events. `send_raw` takes SysEx data without F0/F7."""

    def __init__(self, send_raw):
        self.send_raw = send_raw
        self.num_params, self.num_mix, self.num_sends = 16, 16, 8
        self.num_commands = None
        self.lock = threading.Lock()
        self.op_lock = threading.RLock()
        self.values = {}
        self.plugin = {}
        self.plugin_events = 0
        self.times = {}
        self.tempo = None
        self.strips = {}
        self.errors = []
        self.last_rx = 0.0
        self.dump_done = threading.Event()
        self.ack_event = threading.Event()
        self.waiting_ack = None
        self.next_id = 1
        self.synced = False
        self.slot = 0
        self.bank = 0
        self.mix_bank = 0

    # ---- incoming ----

    def on_message(self, msg):
        with self.lock:
            self.last_rx = time.time()
            e = msg.get("e")
            if e == "value":
                self.values[msg["k"]] = msg
            elif e == "plugin":
                new = {k: msg.get(k, "") for k in ("name", "vendor", "version", "format")}
                if new != self.plugin:
                    self.plugin_events += 1
                self.plugin = new
            elif e == "time":
                self.times[msg["which"]] = {"time": msg.get("time"), "format": msg.get("format")}
            elif e == "tempo":
                self.tempo = msg.get("bpm")
            elif e == "strip":
                self.strips[msg["m"]] = {"name": msg.get("name", ""), "vendor": msg.get("vendor", "")}
            elif e == "ack":
                if msg.get("id") == self.waiting_ack:
                    self.ack_event.set()
            elif e == "error":
                self.errors = (self.errors + [msg.get("msg")])[-10:]
            elif e in ("hello", "dump_end"):
                self.num_params = msg.get("numParams", self.num_params)
                self.num_mix = msg.get("numMix", self.num_mix)
                self.num_sends = msg.get("numSends", self.num_sends)
                self.num_commands = msg.get("numCommands", self.num_commands)
                if e == "dump_end":
                    self.dump_done.set()

    # ---- outgoing ----

    def send(self, obj):
        self.send_raw(encode(obj))

    def settle(self, min_wait=0.12, quiet=0.08, max_wait=1.5):
        """Wait until Cubase stops sending updates."""
        t0 = time.time()
        time.sleep(min_wait)
        while time.time() - t0 < max_wait:
            if time.time() - self.last_rx > quiet:
                return
            time.sleep(0.01)

    def set_value(self, key, v):
        self.send({"c": "set", "k": key, "v": float(v)})
        self.settle()

    def action(self, name, times=1):
        """Press a bridge action and wait until Cubase has released it (acked on its idle tick)
        and the resulting value updates have stopped arriving."""
        for _ in range(times):
            self.next_id += 1
            self.waiting_ack = self.next_id
            self.ack_event.clear()
            self.send({"c": "act", "n": name, "id": self.next_id})
            self.ack_event.wait(1.5)
            self.settle()

    def dump(self, timeout=3.0):
        self.dump_done.clear()
        self.send({"c": "dump"})
        if not self.dump_done.wait(timeout):
            raise RuntimeError(
                "Cubase가 응답하지 않습니다. Cubase가 켜져 있고 MIDI Remote에 'MCP Bridge'가 활성화됐는지 확인하세요."
            )
        self.settle()
        self.synced = True

    # ---- state views ----

    def value(self, key):
        with self.lock:
            return dict(self.values.get(key, {}))

    def bank_params(self, prefix="param"):
        out = []
        for i in range(self.num_params):
            v = self.value("%s%d" % (prefix, i))
            if v.get("name"):
                out.append({"index": i, "name": v["name"], "display": _disp(v), "value": round(v.get("v", 0), 4)})
        return out

    def channel(self, prefix=""):
        vol = self.value(prefix + "volume")
        out = {"name": vol.get("title", "")}
        for f in CHANNEL_FIELDS:
            v = self.value(prefix + f)
            out[f] = _disp(v) if f in ("volume", "pan") else v.get("v", 0) > 0.5
        out["volume_value"] = round(vol.get("v", 0), 4)
        return out

    def sends(self):
        out = []
        for i in range(self.num_sends):
            lvl = self.value("send%d_level" % i)
            if lvl.get("title"):
                out.append({"index": i, "destination": lvl.get("title"), "level": _disp(lvl),
                            "on": self.value("send%d_on" % i).get("v", 0) > 0.5})
        return out

    # ---- navigation ----

    def goto_slot(self, slot):
        before = self.bank_signature()
        self.action("resetSlot")
        self.action("nextSlot", slot)
        self.wait_change(before, timeout=1.5)
        self.slot, self.bank = slot, 0
        self.refresh_names()

    def refresh_names(self):
        """After a script reload Cubase only reports parameter names when they change, so an
        unchanged bank looks empty: step the bank forward and back to make it report them."""
        if self.bank_params():
            return
        self.action("nextBank")
        self.wait_change([])
        self.action("resetBank")
        self.wait_change([])
        self.bank = 0

    def goto_bank(self, bank):
        if bank == self.bank:
            return
        self.action("resetBank")
        self.action("nextBank", bank)
        self.bank = bank

    def goto_mix_bank(self, bank):
        self.action("resetMixBank")
        self.action("nextMixBank", bank)
        self.mix_bank = bank

    def slot_name(self):
        # The insert On switch is titled with the focused plugin's name and, unlike the identity
        # callback, is reported again after a script reload.
        return self.value("insertOn").get("title") or self.plugin.get("name", "")

    def scan_slots(self):
        start = self.slot
        self.action("resetSlot")
        time.sleep(0.2)
        names = []
        for s in range(MAX_SLOTS):
            if s:
                prev = self.slot_name()
                self.action("nextSlot")
                t0 = time.time()
                while self.slot_name() == prev and time.time() - t0 < 0.8:
                    time.sleep(0.05)
                if self.slot_name() == prev:
                    break  # last slot: the viewer did not move
            names.append(self.slot_name())
        self.goto_slot(start)
        info = self.plugin if self.plugin.get("name") in names else {}
        return [dict({k: v for k, v in info.items() if info.get("name") == n}, name=n, slot=i)
                for i, n in enumerate(names) if n]

    def scan_params(self, stop_at=None):
        """All parameters of the current plugin across banks. Cubase wraps around after the last
        bank, so scanning stops once a bank holds only names already seen. With `stop_at`, it
        also stops as soon as a parameter with exactly that name (case-insensitive) is found."""
        before = self.bank_signature()
        self.action("resetBank")
        self.wait_change(before)
        self.bank = 0
        self.refresh_names()
        result, seen, first = [], set(), None
        for b in range(MAX_BANKS):
            if b:
                self.wait_change(before)
            before = self.bank_signature()
            params = self.bank_params()
            names = {p["name"] for p in params}
            if not params:
                break
            if first is None:
                first = params[0]["name"]
            elif first in names and names <= seen:
                break  # wrapped around (runs of repeated names like "MIDI CC" are not a wrap)
            result += [dict(p, bank=b) for p in params]
            seen |= names
            if stop_at and any(n.lower() == stop_at.lower() for n in names):
                break
            self.action("nextBank")
            self.bank = b + 1
        return result

    def scan_strip(self, mod):
        """All parameters of a channel-strip module across banks; leaves the module on bank 0."""
        prefix, act = "s_%s_p" % mod, "s_%s_" % mod
        self.action(act + "resetBank")
        result, prev = [], None
        for bnk in range(MAX_BANKS):
            params = self.bank_params(prefix)
            names = [p["name"] for p in params]
            if not params or names == prev:
                break
            result += [dict(p, bank=bnk) for p in params]
            prev = names
            self.action(act + "nextBank")
        self.action(act + "resetBank")
        return result

    def find_strip_param(self, mod, name):
        params = self.scan_strip(mod)
        exact = [p for p in params if p["name"].lower() == name.lower()]
        hits = exact or [p for p in params if name.lower() in p["name"].lower()]
        if not hits:
            raise ValueError("'%s' 모듈에 '%s' 파라미터가 없습니다. 있는 파라미터: %s"
                             % (mod, name, ", ".join(p["name"] for p in params)))
        p = hits[0]
        self.action("s_%s_nextBank" % mod, p["bank"])
        return "s_%s_p%d" % (mod, p["index"])

    def scan_tracks(self):
        """Every mixer channel, as (bank, channel-in-bank, info)."""
        self.goto_mix_bank(0)
        result, prev = [], None
        for b in range(MAX_BANKS):
            chans = [(c, self.channel("mix%d_" % c)) for c in range(self.num_mix)]
            chans = [(c, info) for c, info in chans if info["name"]]
            names = [info["name"] for _, info in chans]
            if not chans or names == prev:
                break
            result += [(b, c, info) for c, info in chans]
            prev = names
            if len(chans) < self.num_mix:
                break
            self.action("nextMixBank")
            self.mix_bank = b + 1
        return result

    def select_only(self, name):
        """Select one track exclusively. Setting a mixer channel's Selected value adds to the
        current selection, so every other selected channel is switched off afterwards."""
        tracks = self.scan_tracks()
        exact = [t for t in tracks if t[2]["name"].lower() == name.lower()]
        hits = exact or [t for t in tracks if name.lower() in t[2]["name"].lower()]
        if not hits:
            raise ValueError("'%s' 트랙을 찾지 못했습니다. 트랙: %s" % (name, ", ".join(t[2]["name"] for t in tracks)))
        target = hits[0]
        self.goto_mix_bank(target[0])
        self.set_value("mix%d_selected" % target[1], 1)
        for bank, c, info in tracks:
            if (bank, c) != target[:2] and info["selected"]:
                self.goto_mix_bank(bank)
                self.set_value("mix%d_selected" % c, 0)
        return target[2]["name"]

    def find_track(self, name):
        tracks = self.scan_tracks()
        exact = [t for t in tracks if t[2]["name"].lower() == name.lower()]
        hits = exact or [t for t in tracks if name.lower() in t[2]["name"].lower()]
        if not hits:
            raise ValueError("'%s' 트랙을 찾지 못했습니다. 트랙: %s" % (name, ", ".join(t[2]["name"] for t in tracks)))
        bank, c, _ = hits[0]
        self.goto_mix_bank(bank)
        return "mix%d_" % c

    def find_param(self, name):
        params = self.scan_params(stop_at=name)
        exact = [p for p in params if p["name"].lower() == name.lower()]
        hits = exact or [p for p in params if name.lower() in p["name"].lower()]
        if not hits:
            raise ValueError("'%s' 파라미터를 찾지 못했습니다. 있는 파라미터: %s" % (name, ", ".join(p["name"] for p in params)))
        return hits[0]

    def bank_signature(self, prefix="param"):
        return [(p["index"], p["name"], p["display"]) for p in self.bank_params(prefix)]

    def wait_change(self, before, timeout=0.6):
        """After a bank move, Cubase's new names can arrive after the ack: wait until the visible
        bank differs from `before` (or give up quietly: the bank may legitimately look the same)."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.bank_signature() != before:
                self.settle(min_wait=0.03)
                return
            time.sleep(0.03)

    def focus_param(self, name):
        """Navigate to a plugin parameter and return its value key, only once Cubase reports that
        slot as holding this exact parameter (bank changes can land after the ack)."""
        for p in self.bank_params():  # already on screen: no need to rescan from bank 0
            if p["name"].lower() == name.lower():
                return "param%d" % p["index"]
        p = self.find_param(name)
        self.goto_bank(p["bank"])
        key = "param%d" % p["index"]
        self.expect_name(key, p["name"])
        return key

    def expect_name(self, key, name, timeout=2.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.value(key).get("name") == name:
                return
            time.sleep(0.05)
        self.goto_bank(self.bank if self.bank else 0)
        raise RuntimeError("'%s' 위치에 '%s' 대신 '%s'가 있어 값을 바꾸지 않았습니다. 다시 시도하세요."
                           % (key, name, self.value(key).get("name")))


def _disp(v):
    return ("%s %s" % (v.get("disp", ""), v.get("units", ""))).strip()


def _number(text):
    m = re.search(r"[-+]?\d+(?:[.,]\d+)?", text.replace("−", "-"))
    if not m:
        return None
    return float(m.group(0).replace(",", "."))


def _value_number(v):
    """Displayed number of a value state, scaling '1.2 kHz' style displays to Hz."""
    n = _number(v.get("disp", ""))
    text = (v.get("disp", "") + " " + v.get("units", "")).lower()
    if n is not None and re.search(r"\dk|khz|\bk\b", text):
        n *= 1000
    elif n is not None and re.search(r"\d\s*(s|sec)\b", text):
        n *= 1000  # seconds -> ms, so time displays stay monotonic across the ms/sec switch
    return n


def _bool(x):
    return 1 if x else 0


# ---------- MIDI transport ----------

_bridge = None


def _find_port(names, wanted):
    for n in names:
        if wanted.lower() in n.lower():
            return n
    raise RuntimeError(
        "MIDI 포트 '%s'를 찾지 못했습니다. loopMIDI에서 'CubaseMCP_ToCubase'와 'CubaseMCP_FromCubase' 포트를 만드세요. 현재 포트: %s"
        % (wanted, names)
    )


def bridge():
    global _bridge
    if _bridge is None:
        import mido

        out_port = mido.open_output(_find_port(mido.get_output_names(), TO_CUBASE))

        def send_raw(data):
            out_port.send(mido.Message("sysex", data=data))

        b = Bridge(send_raw)

        def on_midi(msg):
            if msg.type == "sysex":
                obj = decode(msg.data)
                if obj is not None:
                    b.on_message(obj)

        b._in_port = mido.open_input(_find_port(mido.get_input_names(), FROM_CUBASE), callback=on_midi)
        b._out_port = out_port
        _bridge = b
    if not _bridge.synced or time.time() - _bridge.last_rx > 600:
        _bridge.dump()
    if _bridge.num_commands is not None and _bridge.num_commands != len(COMMANDS):
        COMMANDS[:] = load_commands()  # bridge was rebuilt since this server started
    return _bridge


# ---------- MCP tools: overview ----------

mcp = FastMCP("cubase")


@mcp.tool()
def cubase_status() -> dict:
    """Overview: selected track (mixer values, sends), its focused insert plugin and visible parameters,
    transport state, position, tempo, cycle locators."""
    b = bridge()
    with b.op_lock:
        warn = None
        if b.num_commands is not None and b.num_commands != len(COMMANDS):
            warn = "Cubase 스크립트와 commands.json의 명령 개수가 다릅니다. tools/build_bridge.py --install 후 Cubase에서 Reload Scripts 하세요."
        return {
            "selected_track": b.channel(),
            "sends": b.sends(),
            "insert_slot": b.slot,
            "plugin": b.plugin,
            "param_bank": b.bank,
            "params": b.bank_params(),
            "transport": {k: b.value(k).get("v", 0) > 0.5 for k in ("play", "record", "cycle", "metronome")},
            "position": b.times.get("pos"),
            "cycle": {"left": b.times.get("left"), "right": b.times.get("right")},
            "tempo_bpm": b.tempo,
            "recent_errors": b.errors,
            "warning": warn,
        }


# ---------- tracks / mixer ----------

@mcp.tool()
def list_tracks() -> list:
    """Every channel in the MixConsole (audio, instrument, group, FX, ...) with volume, pan, mute, solo,
    record-arm, monitor and selected state."""
    b = bridge()
    with b.op_lock:
        return [info for _, _, info in b.scan_tracks()]


@mcp.tool()
def select_track(name: str | None = None, direction: str | None = None, steps: int = 1) -> dict:
    """Select a track by name (exact or partial), or move the selection with direction 'next'/'prev'.
    The selected track is the one whose inserts, plugin parameters and sends the other tools work on."""
    b = bridge()
    with b.op_lock:
        if name:
            b.select_only(name)
        elif direction in ("next", "prev"):
            b.action("nextTrack" if direction == "next" else "prevTrack", steps)
        else:
            raise ValueError("name 또는 direction('next'/'prev')을 주세요.")
        b.goto_slot(0)
        return {"track": b.channel(), "plugin": b.plugin}


@mcp.tool()
def set_track(track: str | None = None, volume: float | None = None, pan: float | None = None,
              mute: bool | None = None, solo: bool | None = None, record_arm: bool | None = None,
              monitor: bool | None = None) -> dict:
    """Set mixer values of a track by name, or of the selected track when `track` is omitted.
    volume/pan are normalized 0..1 (pan 0.5 = center). Use set_track_volume_db for a dB target."""
    b = bridge()
    with b.op_lock:
        prefix = b.find_track(track) if track else ""
        for field, val in (("volume", volume), ("pan", pan)):
            if val is not None:
                b.set_value(prefix + field, val)
        for field, val in (("mute", mute), ("solo", solo), ("rec", record_arm), ("monitor", monitor)):
            if val is not None:
                b.set_value(prefix + field, _bool(val))
        return b.channel(prefix)


@mcp.tool()
def set_track_volume_db(db: float, track: str | None = None) -> dict:
    """Set a track's fader to a dB value (binary search on the displayed value)."""
    b = bridge()
    with b.op_lock:
        prefix = b.find_track(track) if track else ""
        _seek_display(b, prefix + "volume", db)
        return b.channel(prefix)


@mcp.tool()
def set_send(index: int, level: float | None = None, on: bool | None = None, level_db: float | None = None) -> dict:
    """Set a send (0-based) of the selected track: normalized level 0..1, a dB target, and/or on/off."""
    b = bridge()
    with b.op_lock:
        if on is not None:
            b.set_value("send%d_on" % index, _bool(on))
        if level is not None:
            b.set_value("send%d_level" % index, level)
        if level_db is not None:
            _seek_display(b, "send%d_level" % index, level_db)
        return {"sends": b.sends()}


# ---------- inserts / plugin parameters ----------

@mcp.tool()
def list_inserts() -> list:
    """Insert plugins on the selected track (empty slots skipped), with name, vendor and format."""
    b = bridge()
    with b.op_lock:
        return b.scan_slots()


@mcp.tool()
def select_insert(slot: int) -> dict:
    """Focus an insert (0-based index from list_inserts) so its parameters can be read and set."""
    b = bridge()
    with b.op_lock:
        b.goto_slot(slot)
        return {"slot": slot, "plugin": b.plugin, "params": b.bank_params()}


@mcp.tool()
def get_params(all_banks: bool = True) -> dict:
    """Parameters of the focused insert plugin. value is normalized 0..1, display is what Cubase shows."""
    b = bridge()
    with b.op_lock:
        params = b.scan_params() if all_banks else [dict(p, bank=b.bank) for p in b.bank_params()]
        return {"plugin": b.plugin, "params": params}


@mcp.tool()
def set_param(name: str, value: float) -> dict:
    """Set a parameter of the focused plugin by name (exact or partial match) to a normalized 0..1 value."""
    b = bridge()
    with b.op_lock:
        key = b.focus_param(name)
        b.set_value(key, value)
        v = b.value(key)
        return {"name": v.get("name"), "display": _disp(v), "value": v.get("v")}


@mcp.tool()
def set_param_display(name: str, target: float) -> dict:
    """Set a parameter so its displayed number reaches `target` (e.g. -18 for '-18 dB', 2500 for '2500 Hz').
    Uses a binary search over the normalized range; assumes the display is monotonic."""
    b = bridge()
    with b.op_lock:
        key = b.focus_param(name)
        _seek_display(b, key, target)
        v = b.value(key)
        return {"name": v.get("name"), "display": _disp(v), "value": v.get("v")}


def _seek_display(b, key, target):
    """Binary-search a value so its displayed number reaches `target`. If the search cannot run
    (non-numeric display such as 'Max'), the original value is restored before raising."""
    before = b.value(key)
    try:
        _seek_display_inner(b, key, target)
    except Exception:
        if before.get("v") is not None and b.value(key).get("name") == before.get("name"):
            b.set_value(key, before["v"])
        raise


def _seek_display_inner(b, key, target):
    name = b.value(key).get("name")

    def probe(x):
        if b.value(key).get("name") != name:
            raise RuntimeError("탐색 중 '%s' 위치의 파라미터가 바뀌어 중단했습니다." % key)
        b.set_value(key, x)
        return _value_number(b.value(key))

    lo_val, hi_val = probe(0.0), probe(1.0)
    if hi_val is None:
        raise ValueError("이 값의 표시는 숫자가 아닙니다: %s" % _disp(b.value(key)))
    rising = lo_val is None or hi_val >= lo_val  # e.g. fader at 0 shows "-oo"
    lo, hi = 0.0, 1.0
    for _ in range(16):
        mid = (lo + hi) / 2
        val = probe(mid)
        if val is None or (val < target) == rising:
            lo = mid
        else:
            hi = mid
    best = min((lo, hi), key=lambda x: abs((probe(x) or -1e9) - target))
    b.set_value(key, best)


@mcp.tool()
def set_insert(on: bool | None = None, bypass: bool | None = None, open_editor: bool | None = None) -> dict:
    """Turn the focused insert on/off, bypass it, or open/close its plugin window."""
    b = bridge()
    with b.op_lock:
        for key, val in (("insertOn", on), ("insertBypass", bypass), ("insertEdit", open_editor)):
            if val is not None:
                b.set_value(key, _bool(val))
        return {
            "plugin": b.plugin,
            "on": b.value("insertOn").get("v", 0) > 0.5,
            "bypass": b.value("insertBypass").get("v", 0) > 0.5,
            "editor_open": b.value("insertEdit").get("v", 0) > 0.5,
        }


# ---------- transport ----------

@mcp.tool()
def transport(play: bool | None = None, record: bool | None = None, cycle: bool | None = None,
              metronome: bool | None = None, position: str | None = None,
              cycle_left: str | None = None, cycle_right: str | None = None,
              tempo_bpm: float | None = None) -> dict:
    """Control transport. play=False stops. Times use Cubase's current primary time format,
    e.g. bars/beats '5.1.1.0'. tempo_bpm sets the project tempo."""
    b = bridge()
    with b.op_lock:
        if play is True:
            b.set_value("play", 1)
        elif play is False:
            b.set_value("stop", 1)
            b.set_value("stop", 0)
        for key, val in (("record", record), ("cycle", cycle), ("metronome", metronome)):
            if val is not None:
                b.set_value(key, _bool(val))
        for which, t in (("pos", position), ("left", cycle_left), ("right", cycle_right)):
            if t is not None:
                b.send({"c": "time", "which": which, "t": t})
                b.settle()
        if tempo_bpm is not None:
            b.send({"c": "tempo", "bpm": tempo_bpm})
            b.settle()
        return {
            "playing": b.value("play").get("v", 0) > 0.5,
            "recording": b.value("record").get("v", 0) > 0.5,
            "cycle": b.value("cycle").get("v", 0) > 0.5,
            "metronome": b.value("metronome").get("v", 0) > 0.5,
            "position": b.times.get("pos"),
            "cycle_range": {"left": b.times.get("left"), "right": b.times.get("right")},
            "tempo_bpm": b.tempo,
            "recent_errors": b.errors,
        }


# ---------- key commands ----------

@mcp.tool()
def search_commands(query: str, limit: int = 30) -> list:
    """Search all Cubase key commands (about 2000: editing, project, track, mixer, windows, export, ...)
    by words, e.g. 'add track audio', 'mixdown', 'quantize'. Returns names usable with run_command."""
    words = query.lower().split()
    return [c for c in COMMANDS if all(w in c.lower() for w in words)][:limit]


@mcp.tool()
def run_command(command: str) -> str:
    """Run a Cubase key command, given exactly as returned by search_commands ('Category / Name').
    Commands act on Cubase's current selection/focus, like pressing their key shortcut."""
    matches = [i for i, c in enumerate(COMMANDS) if c.lower() == command.lower()]
    if not matches:
        hits = search_commands(command, 10)
        raise ValueError("정확히 일치하는 명령이 없습니다. 비슷한 명령: %s" % hits)
    b = bridge()
    with b.op_lock:
        b.action("cmd:%d" % matches[0])
        return "실행: " + COMMANDS[matches[0]]


# ---------- audio files ----------

@mcp.tool()
def list_audio_files(folder: str) -> list:
    """List audio files (wav/aiff/flac/ogg/mp3) under a folder, e.g. a Cubase project's Audio folder
    or a mixdown folder, with duration, sample rate and channel count."""
    return audio_analysis.list_audio(folder)


@mcp.tool()
def analyze_audio(path: str, tempo_and_key: bool = True) -> dict:
    """Analyze an audio file: peak, true peak, RMS, integrated LUFS, crest factor, clipping, stereo
    correlation, energy per frequency band, spectral centroid, and estimated tempo and key."""
    return audio_analysis.analyze(path, tempo_and_key)


@mcp.tool()
def compare_audio(reference: str, target: str, ref_start: float | None = None, ref_end: float | None = None,
                  target_start: float | None = None, target_end: float | None = None,
                  focus_low: float = 30.0, focus_high: float = 8000.0) -> dict:
    """Compare the tone of a reference file with a target (e.g. your processed bass mixdown or DI):
    1/3-octave levels normalized to each file's average, the smoothed difference, suggested EQ
    moves (frequency, shape, gain, Q), dynamics/compression advice, brightness and loudness.
    Optional start/end (seconds) pick matching sections; focus_low/high limit the analysed range."""
    return audio_analysis.compare(reference, target, ref_start, ref_end, target_start, target_end,
                                  focus_low, focus_high)


def _run_cmd(b, command):
    matches = [i for i, c in enumerate(COMMANDS) if c.lower() == command.lower()]
    if not matches:
        raise ValueError("Cubase 명령 '%s'을(를) 찾지 못했습니다." % command)
    b.action("cmd:%d" % matches[0])


def _find_audio_folder():
    """The Audio folder of the most recently touched project under Documents/Cubase Projects."""
    root = os.path.join(os.path.expanduser("~"), "Documents", "Cubase Projects")
    folders = [os.path.join(root, d, "Audio") for d in os.listdir(root)] if os.path.isdir(root) else []
    folders = [f for f in folders if os.path.isdir(f)]
    if not folders:
        raise ValueError("Cubase 프로젝트의 Audio 폴더를 찾지 못했습니다. audio_folder를 지정하세요.")
    return max(folders, key=os.path.getmtime)


def _render_folders(project_audio):
    """Folders Render in Place may write to: the project's Audio folder plus every custom
    location saved in Cubase's Defaults.xml (Render Setup > File Location)."""
    folders = [project_audio]
    defaults = os.path.join(os.environ.get("APPDATA", ""), "Steinberg", "Cubase 13_64", "Defaults.xml")
    try:
        with open(defaults, encoding="utf-8", errors="replace") as f:
            text = f.read()
        pat = r'<member name="path">\s*<string name="Name" value="([^"]*)"[^>]*/>\s*<string name="Path" value="([^"]*)"'
        for name, base in re.findall(pat, text):
            path = os.path.join(base, name)
            if os.path.isdir(path) and path not in folders:
                folders.append(path)
    except OSError:
        pass
    return folders


def _wav_files(folders):
    out = {}
    for folder in folders:
        for f in os.listdir(folder):
            if f.lower().endswith(".wav"):
                p = os.path.join(folder, f)
                out[p] = os.path.getmtime(p)
    return out


def _wait_new_file(folders, before, timeout):
    """Wait for a new or rewritten wav in any of `folders`, then until its size stops changing."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        now = _wav_files(folders)
        # Cubase also leaves 0-byte placeholder files next to a render: only take ones with data
        new = [f for f, m in now.items() if (f not in before or m > before[f] + 0.5) and os.path.getsize(f) > 0]
        if new:
            path = max(new, key=lambda f: now[f])
            size = -1
            while time.time() - t0 < timeout:
                s = os.path.getsize(path)
                if s == size and s > 0:
                    return path
                size = s
                time.sleep(0.7)
        time.sleep(0.5)
    return None


def _reattach_viewer(b):
    name = b.channel().get("name")
    b.action("nextTrack")
    time.sleep(0.3)
    if b.channel().get("name") != name:
        b.action("prevTrack")
    else:  # already the last track
        b.action("prevTrack")
        time.sleep(0.3)
        b.action("nextTrack")
    time.sleep(0.3)
    with b.lock:
        for k in list(b.values):
            if k.startswith("param") or k.startswith("insert"):
                b.values.pop(k)


@mcp.tool()
def render_and_analyze(reference: str | None = None, track: str | None = None, audio_folder: str | None = None,
                       undo: bool = True, timeout: float = 30.0,
                       ref_start: float | None = None, ref_end: float | None = None,
                       focus_low: float = 30.0, focus_high: float = 8000.0) -> dict:
    """Render the selected track (or `track` by name) WITH its plugins via Cubase's Render in Place,
    analyze the rendered wav, and compare it with `reference` when given. With undo=True the render
    (new track/event) is undone in Cubase afterwards; the rendered file stays on disk.
    Render in Place uses the options last chosen in its Render Setup dialog (complete signal path
    is what you want here). audio_folder defaults to the newest project's Audio folder."""
    folders = _render_folders(audio_folder or _find_audio_folder())
    before = _wav_files(folders)
    b = bridge()
    with b.op_lock:
        b.select_only(track or b.channel()["name"])  # render exactly one track
        _run_cmd(b, "Edit / Select All on Tracks")
        _run_cmd(b, "Render in Place / Render")
        path = _wait_new_file(folders, before, timeout)
        if not path:
            raise RuntimeError(
                "렌더된 파일이 %s에 %d초 안에 생기지 않았습니다. 트랙에 오디오 클립이 없으면 렌더할 게 없습니다. "
                "클립이 있다면 Render in Place 설정을 확인하세요. Render 명령이 트랙을 만들었을 수 있으니 확인 후 Undo 하세요."
                % (", ".join(folders), timeout))
        undone = False
        if undo:
            _run_cmd(b, "Edit / Undo")
            undone = True
        # Rendering briefly selects the new track, which leaves the insert viewer detached (it
        # acks actions but no longer moves). Selecting another track and coming back re-attaches
        # it; then forget the stale parameter cache and return to the slot we were on.
        _reattach_viewer(b)
        b.goto_slot(b.slot)
    result = {"rendered_file": path, "render_undone": undone,
              "analysis": audio_analysis.analyze(path, tempo_and_key=False)}
    if reference:
        result["comparison"] = audio_analysis.compare(reference, path, ref_start, ref_end, None, None,
                                                      focus_low, focus_high)
    return result


if __name__ == "__main__":
    mcp.run()
