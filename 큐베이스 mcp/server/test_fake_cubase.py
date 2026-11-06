"""Exercises the MCP tools against a simulated Cubase (no MIDI, no Cubase needed).
Run: python test_fake_cubase.py"""

import os
import tempfile
import threading

import numpy as np
import soundfile as sf

import cubase_mcp as cm

N = 16
MIX = 16


def make_plugin(name, params):
    return {"name": name, "vendor": "FabFilter", "version": "1", "format": "VST3", "params": params}


def thr_disp(v):
    return ("%.1f" % (-60 + 60 * v), "dB")


def vol_disp(v):
    return ("-oo", "dB") if v == 0 else ("%.2f" % (-60 + 66 * v), "dB")


class Track:
    def __init__(self, name, plugins):
        self.name = name
        self.plugins = plugins
        self.vals = {"volume": 0.7, "pan": 0.5, "mute": 0, "solo": 0, "rec": 0, "monitor": 0, "selected": 0}


TRACKS = [Track("Track %02d" % i, []) for i in range(20)]
TRACKS[3] = Track("Vocal", [
    make_plugin("Pro-Q 4", [["Band %d Gain" % (i + 1), 0.5, None] for i in range(40)]),
    make_plugin("Pro-C 2", [["Threshold", 0.5, thr_disp], ["Ratio", 0.2, None], ["Attack", 0.1, None]]),
])
TRACKS[0].vals["selected"] = 1


class FakeCubase:
    def __init__(self):
        self.sel = 0
        self.slot = 0
        self.bank = 0
        self.mix_bank = 0
        self.transport = {"play": 0, "stop": 0, "record": 0, "cycle": 0, "metronome": 0}
        self.tempo = 120.0
        self.pos = "1.1.1.0"
        self.ran = []
        self.render_folder = None
        self.bridge = None

    def emit(self, msg):
        threading.Timer(0.003, self.bridge.on_message, [msg]).start()

    def track(self):
        return TRACKS[self.sel]

    def plugin(self):
        p = self.track().plugins
        return p[self.slot] if p else make_plugin("", [])

    def val(self, key, title, name, v, fmt=None):
        disp, units = fmt(v) if fmt else ("%.2f" % v, "")
        return {"e": "value", "k": key, "title": title, "name": name, "disp": disp, "units": units, "v": v}

    def param_state(self, i):
        params = self.plugin()["params"]
        idx = self.bank * N + i
        if idx >= len(params):
            return self.val("param%d" % i, "", "", 0)
        name, v, fmt = params[idx]
        return self.val("param%d" % i, self.plugin()["name"], name, v, fmt)

    def chan_states(self, prefix, t):
        for f, v in t.vals.items():
            yield self.val(prefix + f, t.name if t else "", f, v, vol_disp if f == "volume" else None)

    def emit_all(self):
        p = self.plugin()
        self.emit({"e": "plugin", **{k: p[k] for k in ("name", "vendor", "version", "format")}})
        for i in range(N):
            self.emit(self.param_state(i))
        for m in self.chan_states("", self.track()):
            self.emit(m)
        for c in range(MIX):
            idx = self.mix_bank * MIX + c
            if idx < len(TRACKS):
                for m in self.chan_states("mix%d_" % c, TRACKS[idx]):
                    self.emit(m)
            else:
                self.emit(self.val("mix%d_volume" % c, "", "", 0))
        for k, v in self.transport.items():
            self.emit(self.val(k, "Transport", k, v))
        self.emit({"e": "time", "which": "pos", "time": self.pos, "format": "Bars+Beats"})
        self.emit({"e": "tempo", "bpm": self.tempo})

    def select(self, idx):
        for t in TRACKS:
            t.vals["selected"] = 0
        TRACKS[idx].vals["selected"] = 1
        self.sel, self.slot, self.bank = idx, 0, 0

    def receive(self, data):
        msg = cm.decode(data)
        c = msg["c"]
        if c == "dump":
            self.emit_all()
            self.emit({"e": "dump_end", "numParams": N, "numMix": MIX, "numSends": 8,
                       "numCommands": len(cm.COMMANDS)})
            return
        if c == "act":
            n = msg["n"]
            if n == "resetSlot":
                self.slot, self.bank = 0, 0
            elif n == "nextSlot":
                if self.slot < len(self.track().plugins) - 1:
                    self.slot, self.bank = self.slot + 1, 0
                else:
                    return
            elif n == "resetBank":
                self.bank = 0
            elif n == "nextBank":  # Cubase wraps around after the last bank
                self.bank = self.bank + 1 if (self.bank + 1) * N < len(self.plugin()["params"]) else 0
            elif n == "resetMixBank":
                self.mix_bank = 0
            elif n == "nextMixBank":
                if (self.mix_bank + 1) * MIX < len(TRACKS):
                    self.mix_bank += 1
            elif n == "nextTrack":
                self.select(min(self.sel + 1, len(TRACKS) - 1))
            elif n.startswith("cmd:"):
                self.ran.append(cm.COMMANDS[int(n[4:])])
                if self.ran[-1] == "Render in Place / Render" and self.render_folder:
                    sr = 44100
                    tone = 0.3 * np.sin(2 * np.pi * 220 * np.arange(sr * 2) / sr)
                    sf.write(os.path.join(self.render_folder, "Audio 01_01-render.wav"), tone, sr)
            self.emit({"e": "ack", "id": msg.get("id")})
        elif c == "set":
            k, v = msg["k"], msg["v"]
            if k.startswith("param"):
                self.plugin()["params"][self.bank * N + int(k[5:])][1] = v
            elif k.startswith("mix"):
                ci, f = k[3:].split("_", 1)
                idx = self.mix_bank * MIX + int(ci)
                if f == "selected" and v:
                    self.select(idx)
                else:
                    TRACKS[idx].vals[f] = v
            elif k in self.transport:
                self.transport[k] = v
                if k == "stop" and v:
                    self.transport["play"] = 0
                if k == "play" and v:
                    self.transport["play"] = 1
            else:
                self.track().vals[k] = v
        elif c == "time":
            self.pos = msg["t"]
        elif c == "tempo":
            self.tempo = msg["bpm"]
        self.emit_all()


fake = FakeCubase()
fake.bridge = cm.Bridge(fake.receive)
cm._bridge = fake.bridge

status = cm.cubase_status()
assert status["selected_track"]["name"] == "Track 00", status
assert status["warning"] is None, status

tracks = cm.list_tracks()
assert len(tracks) == 20 and tracks[3]["name"] == "Vocal", [t["name"] for t in tracks]

r = cm.select_track(name="voc")
assert r["track"]["name"] == "Vocal" and r["plugin"]["name"] == "Pro-Q 4", r

inserts = cm.list_inserts()
assert [s["name"] for s in inserts] == ["Pro-Q 4", "Pro-C 2"], inserts

params = cm.get_params()["params"]
assert len(params) == 40 and params[-1]["bank"] == 2, len(params)

cm.set_param("Band 35 Gain", 0.9)
assert abs(TRACKS[3].plugins[0]["params"][34][1] - 0.9) < 1e-9

cm.select_insert(1)
r = cm.set_param_display("thresh", -18)
assert abs(float(r["display"].split()[0]) + 18) <= 0.1, r

r = cm.set_track(track="Track 18", mute=True)
assert r["name"] == "Track 18" and r["mute"] is True, r
r = cm.set_track_volume_db(-6, track="Track 18")
assert abs(float(r["volume"].split()[0]) + 6) <= 0.1, r

r = cm.transport(play=True, position="9.1.1.0", tempo_bpm=98)
assert r["playing"] and r["position"]["time"] == "9.1.1.0" and r["tempo_bpm"] == 98, r
assert cm.transport(play=False)["playing"] is False

hits = cm.search_commands("transport start")
assert hits, "no command found"
cm.run_command(hits[0])
assert fake.ran == [hits[0]], fake.ran

sr = 44100
t = np.arange(sr * 3) / sr
tone = 0.5 * np.sin(2 * np.pi * 100 * t)
path = os.path.join(tempfile.mkdtemp(), "tone.wav")
sf.write(path, np.stack([tone, tone], axis=1), sr)
a = cm.analyze_audio(path, tempo_and_key=False)
assert abs(a["peak_dbfs"] + 6.02) < 0.1 and a["band_energy_percent"]["bass (60-250Hz)"] > 95, a
assert a["stereo_correlation"] > 0.99 and "integrated_lufs" in a, a
assert cm.list_audio_files(os.path.dirname(path))[0]["seconds"] == 3.0

fake.render_folder = tempfile.mkdtemp()
fake.ran.clear()
open(os.path.join(fake.render_folder, "placeholder_02.wav"), "wb").close()  # Cubase-style 0-byte file
r = cm.render_and_analyze(reference=path, audio_folder=fake.render_folder, timeout=10)
assert fake.ran == ["Edit / Select All on Tracks", "Render in Place / Render", "Edit / Undo"], fake.ran
assert r["rendered_file"].endswith("render.wav") and r["render_undone"], r
assert r["comparison"]["spectral_centroid_hz"]["target"] > 0, r["comparison"]

print("all fake-Cubase checks passed (%d key commands loaded)" % len(cm.COMMANDS))

