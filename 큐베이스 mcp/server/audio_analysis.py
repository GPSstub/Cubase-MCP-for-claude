"""Offline analysis of audio files (mixdowns, recorded takes) for the Cubase MCP server."""

import os

import numpy as np
import soundfile as sf
# Imported up front: importing scipy for the first time inside the MCP server's tool thread
# took over 40 s on Windows, while at startup it takes about a second.
import pyloudnorm  # noqa: F401
import scipy.signal  # noqa: F401

AUDIO_EXT = (".wav", ".aif", ".aiff", ".flac", ".ogg", ".mp3")

BANDS = [
    ("sub (20-60Hz)", 20, 60),
    ("bass (60-250Hz)", 60, 250),
    ("low-mid (250-500Hz)", 250, 500),
    ("mid (500Hz-2kHz)", 500, 2000),
    ("high-mid (2-6kHz)", 2000, 6000),
    ("high (6-20kHz)", 6000, 20000),
]
KEYS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# Krumhansl-Schmuckler key profiles
MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def db(x):
    return float(20 * np.log10(max(float(x), 1e-12)))


def list_audio(folder):
    out = []
    for dirpath, _, files in os.walk(folder):
        for name in files:
            if name.lower().endswith(AUDIO_EXT):
                path = os.path.join(dirpath, name)
                try:
                    info = sf.info(path)
                    out.append({"path": path, "seconds": round(info.duration, 2),
                                "samplerate": info.samplerate, "channels": info.channels})
                except RuntimeError:
                    out.append({"path": path, "error": "unreadable"})
    return out


def analyze(path, tempo_and_key=True):
    data, sr = sf.read(path, always_2d=True, dtype="float64")
    mono = data.mean(axis=1)
    result = {
        "path": path,
        "seconds": round(len(data) / sr, 2),
        "samplerate": sr,
        "channels": data.shape[1],
        "peak_dbfs": round(db(np.abs(data).max()), 2),
        "rms_dbfs": round(db(np.sqrt(np.mean(data ** 2))), 2),
        "clipped_samples": int(np.sum(np.abs(data) >= 0.999)),
    }
    result["crest_factor_db"] = round(result["peak_dbfs"] - result["rms_dbfs"], 2)

    try:
        from scipy.signal import resample_poly
        tp = max(np.abs(resample_poly(data[:, ch], 4, 1)).max() for ch in range(data.shape[1]))
        result["true_peak_dbtp"] = round(db(tp), 2)
    except ImportError:
        pass

    try:
        import pyloudnorm as pyln
        if len(data) > sr * 0.5:
            result["integrated_lufs"] = round(float(pyln.Meter(sr).integrated_loudness(data)), 2)
    except ImportError:
        pass

    if data.shape[1] == 2:
        l, r = data[:, 0], data[:, 1]
        if l.std() > 0 and r.std() > 0:
            result["stereo_correlation"] = round(float(np.corrcoef(l, r)[0, 1]), 3)
        side = (l - r) / 2
        result["side_to_mid_db"] = round(db(np.sqrt(np.mean(side ** 2))) - db(np.sqrt(np.mean(((l + r) / 2) ** 2))), 2)

    # Average spectrum over 8192-point frames
    n = 8192
    frames = [mono[i:i + n] for i in range(0, max(len(mono) - n, 1), n // 2)][:2000]
    window = np.hanning(n)
    power = np.zeros(n // 2 + 1)
    for f in frames:
        if len(f) < n:
            f = np.pad(f, (0, n - len(f)))
        power += np.abs(np.fft.rfft(f * window)) ** 2
    freqs = np.fft.rfftfreq(n, 1 / sr)
    total = power[(freqs >= 20) & (freqs <= 20000)].sum() or 1e-12
    result["band_energy_percent"] = {
        name: round(100 * float(power[(freqs >= lo) & (freqs < hi)].sum() / total), 1) for name, lo, hi in BANDS
    }
    result["spectral_centroid_hz"] = round(float((freqs * power).sum() / (power.sum() or 1e-12)), 1)

    if tempo_and_key:
        try:
            import librosa
            y = librosa.resample(mono.astype(np.float32), orig_sr=sr, target_sr=22050)
            tempo, _ = librosa.beat.beat_track(y=y, sr=22050)
            result["tempo_bpm"] = round(float(np.atleast_1d(tempo)[0]), 1)
            chroma = librosa.feature.chroma_cqt(y=y, sr=22050).mean(axis=1)
            scores = []
            for i in range(12):
                scores.append((np.corrcoef(chroma, np.roll(MAJOR, i))[0, 1], KEYS[i] + " major"))
                scores.append((np.corrcoef(chroma, np.roll(MINOR, i))[0, 1], KEYS[i] + " minor"))
            result["key_estimate"] = max(scores)[1]
        except ImportError:
            result["tempo_and_key"] = "librosa not installed"
    return result


# ---------- Reference matching ----------

THIRD_OCTAVE = [1000 * 2 ** (k / 3) for k in range(-16, 14)]  # 25 Hz .. 20 kHz


def load_section(path, start=None, end=None):
    """Mono float audio and sample rate, optionally cut to [start, end] seconds."""
    try:
        data, sr = sf.read(path, always_2d=True, dtype="float64")
    except RuntimeError:  # formats libsndfile cannot read
        import librosa
        y, sr = librosa.load(path, sr=None, mono=False)
        data = np.atleast_2d(y).T
    a = int((start or 0) * sr)
    b = int(end * sr) if end else len(data)
    return data[a:b], sr


def third_octave_levels(mono, sr):
    """Power per 1/3-octave band in dB, from a Welch average spectrum."""
    from scipy.signal import welch
    freqs, psd = welch(mono, sr, nperseg=min(len(mono), 16384))
    out = []
    for fc in THIRD_OCTAVE:
        lo, hi = fc * 2 ** (-1 / 6), fc * 2 ** (1 / 6)
        sel = (freqs >= lo) & (freqs < hi)
        out.append(10 * np.log10(max(psd[sel].sum(), 1e-20)) if sel.any() and hi < sr / 2 else None)
    return out


def dynamics(mono, sr):
    """Crest factor and short-term (400 ms) loudness spread, a rough measure of compression."""
    win = int(0.4 * sr)
    rms = [np.sqrt(np.mean(mono[i:i + win] ** 2)) for i in range(0, len(mono) - win, win // 2)]
    rms = np.array([r for r in rms if r > 1e-5]) if rms else np.array([1e-5])
    st = 20 * np.log10(rms)
    st = st[st > st.max() - 30]  # ignore silence between notes
    return {
        "crest_factor_db": round(db(np.abs(mono).max()) - db(np.sqrt(np.mean(mono ** 2))), 2),
        "short_term_spread_db": round(float(np.percentile(st, 95) - np.percentile(st, 10)), 2),
    }


def _fmt_hz(f):
    return "%d Hz" % round(f) if f < 1000 else "%.1f kHz" % (f / 1000)


def compare(reference, target, ref_start=None, ref_end=None, target_start=None, target_end=None,
            focus_low=30.0, focus_high=8000.0):
    """Tone difference between a reference and a target take: per-band levels (each spectrum
    normalized to its own average inside the focus range, so overall volume does not matter),
    the smoothed difference, suggested EQ moves, and dynamics."""
    ref, sr_r = load_section(reference, ref_start, ref_end)
    tgt, sr_t = load_section(target, target_start, target_end)
    ref_m, tgt_m = ref.mean(axis=1), tgt.mean(axis=1)
    lr, lt = third_octave_levels(ref_m, sr_r), third_octave_levels(tgt_m, sr_t)

    idx = [i for i, fc in enumerate(THIRD_OCTAVE)
           if focus_low <= fc <= focus_high and lr[i] is not None and lt[i] is not None]
    if len(idx) < 3:
        raise ValueError("focus range too narrow for these files")
    ref_n = np.array([lr[i] for i in idx]) - np.mean([lr[i] for i in idx])
    tgt_n = np.array([lt[i] for i in idx]) - np.mean([lt[i] for i in idx])
    diff = ref_n - tgt_n
    smooth = np.convolve(np.pad(diff, 1, mode="edge"), [0.25, 0.5, 0.25], mode="valid")
    centers = [THIRD_OCTAVE[i] for i in idx]

    bands = [{"hz": round(fc, 1), "reference_db": round(float(r), 1), "target_db": round(float(t), 1),
              "difference_db": round(float(d), 1)} for fc, r, t, d in zip(centers, ref_n, tgt_n, smooth)]

    # EQ suggestions: local extrema of the smoothed difference beyond 1.5 dB
    moves = []
    for i, d in enumerate(smooth):
        left = smooth[i - 1] if i else 0.0  # outside the range counts as "no difference"
        right = smooth[i + 1] if i + 1 < len(smooth) else 0.0
        is_peak = d > 0 and d >= left and d >= right
        is_dip = d < 0 and d <= left and d <= right
        if abs(d) < 1.5 or not (is_peak or is_dip):
            continue
        # width: neighbours still above half the extreme
        j0 = i
        while j0 > 0 and abs(smooth[j0 - 1]) > abs(d) / 2 and np.sign(smooth[j0 - 1]) == np.sign(d):
            j0 -= 1
        j1 = i
        while j1 + 1 < len(smooth) and abs(smooth[j1 + 1]) > abs(d) / 2 and np.sign(smooth[j1 + 1]) == np.sign(d):
            j1 += 1
        octaves = max((j1 - j0 + 1) / 3, 1 / 3)
        q = round(float(np.clip(1.41 / octaves, 0.4, 4.0)), 2)
        shape = "Bell"
        if j0 == 0 and centers[i] < 120:
            shape = "Low Shelf"
        elif j1 == len(smooth) - 1 and centers[i] > 3000:
            shape = "High Shelf"
        moves.append({"frequency": _fmt_hz(centers[i]), "hz": round(centers[i], 1), "shape": shape,
                      "gain_db": round(float(np.clip(d, -12, 12)), 1), "q": q})
    moves.sort(key=lambda m: -abs(m["gain_db"]))

    dr, dt = dynamics(ref_m, sr_r), dynamics(tgt_m, sr_t)
    spread = dt["short_term_spread_db"] - dr["short_term_spread_db"]
    crest = dt["crest_factor_db"] - dr["crest_factor_db"]
    if spread > 2 or crest > 2:
        comp = "target is more dynamic than the reference: compress more (about %.1f dB less spread, %.1f dB less crest)" % (spread, crest)
    elif spread < -2 or crest < -2:
        comp = "target is already more compressed than the reference: compress less"
    else:
        comp = "dynamics are close to the reference"

    def centroid(m, sr):
        spec = np.abs(np.fft.rfft(m[: min(len(m), sr * 30)]))
        f = np.fft.rfftfreq(min(len(m), sr * 30), 1 / sr)
        return round(float((f * spec).sum() / (spec.sum() or 1e-12)), 1)

    return {
        "focus_hz": [focus_low, focus_high],
        "suggested_eq": moves[:8],
        "dynamics": {"reference": dr, "target": dt, "advice": comp},
        "spectral_centroid_hz": {"reference": centroid(ref_m, sr_r), "target": centroid(tgt_m, sr_t)},
        "loudness_lufs": {"reference": _lufs(ref, sr_r), "target": _lufs(tgt, sr_t)},
        "stereo": {"reference": _stereo(ref), "target": _stereo(tgt)},
        "bands": bands,
        "note": "difference_db > 0 means the reference has more energy there than the target (boost); "
                "levels are relative to each file's own average, so a full mix reference also "
                "contains other instruments above ~300 Hz: give a bass stem or a bass-heavy section when possible.",
    }


def _stereo(data):
    """Width of a section: L/R correlation (1 = mono) and side level relative to mid."""
    if data.shape[1] < 2:
        return {"correlation": 1.0, "side_to_mid_db": None, "note": "mono"}
    l, r = data[:, 0], data[:, 1]
    corr = float(np.corrcoef(l, r)[0, 1]) if l.std() > 0 and r.std() > 0 else 1.0
    mid, side = (l + r) / 2, (l - r) / 2
    return {"correlation": round(corr, 3),
            "side_to_mid_db": round(db(np.sqrt(np.mean(side ** 2))) - db(np.sqrt(np.mean(mid ** 2))), 1)}


def _lufs(data, sr):
    try:
        import pyloudnorm as pyln
        if len(data) > sr * 0.5:
            return round(float(pyln.Meter(sr).integrated_loudness(data)), 2)
    except ImportError:
        pass
    return None
