import contextlib
import csv
import hashlib
import io
import json
import os
import random
import re
import secrets
import shutil
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path

from flask import Flask, redirect, render_template, request, send_file, session, url_for

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-this-in-production")
ADMIN_ACCESS_CODE = os.getenv("ADMIN_ACCESS_CODE", "3647")
LEARNER_ACCESS_CODE = os.getenv("LEARNER_ACCESS_CODE", "12321")

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_VOKABEL_DATEI = BASE_DIR / "data" / "vokabeln.csv"
SEED_VOKABEL_DATEI = BASE_DIR / "data" / "vokabeln.seed.csv"
LEGACY_VOKABEL_DATEI = BASE_DIR / "vokabeln.csv"
IMPORTS_DIR = BASE_DIR / "data" / "imports"
IMPORTS_DIR.mkdir(parents=True, exist_ok=True)
AUDIO_CACHE_DIR = BASE_DIR / "static" / "audio_cache"
AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
DATA_LOCK = threading.Lock()
TTS_LOCK = threading.Lock()
FIELDNAMES = ["fremdsprache", "deutsch", "deklination", "lektion", "richtig", "falsch"]
DEFAULT_SOURCE_ID = "__default__"
ALLOWED_MODES = {"kartei", "block", "auto_audio", "durchlauf"}
TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "alloy")
TTS_DELAY_SECONDS = float(os.getenv("TTS_DELAY_SECONDS", "0.8"))
TTS_MAX_NEW_PER_RUN = int(os.getenv("TTS_MAX_NEW_PER_RUN", "0"))
_OPENAI_CLIENT = None
RUNTIME_SECRETS_FILE = BASE_DIR / "data" / "runtime_secrets.json"
TTS_BUILD_LOCK_FILE = BASE_DIR / "data" / "tts_build.lock"
PERF_HISTORY_FILE = BASE_DIR / "data" / "performance_history.json"
PERF_WINDOW_SIZE = 30
DIFFICULTY_LEVELS = [1, 2, 3, 4, 5]
DIFFICULTY_LABELS = {
    1: "Leicht",
    2: "Eher leicht",
    3: "Mittel",
    4: "Schwierig",
    5: "Sehr schwierig",
}
ADMIN_ONLY_ENDPOINTS = {
    "audio_files",
    "build_tts_cache",
    "set_api_key",
    "add_vocab",
    "delete_vocab",
    "delete_lesson",
    "export_csv",
    "export_audio_zip",
    "import_csv",
}


def _resolve_vokabel_datei():
    env_path = os.getenv("VOKABEL_DATEI")
    if env_path:
        p = Path(env_path).expanduser()
        if not p.is_absolute():
            p = (BASE_DIR / p).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    DEFAULT_VOKABEL_DATEI.parent.mkdir(parents=True, exist_ok=True)

    if not DEFAULT_VOKABEL_DATEI.exists():
        if LEGACY_VOKABEL_DATEI.exists():
            shutil.copy2(LEGACY_VOKABEL_DATEI, DEFAULT_VOKABEL_DATEI)
        elif SEED_VOKABEL_DATEI.exists():
            shutil.copy2(SEED_VOKABEL_DATEI, DEFAULT_VOKABEL_DATEI)

    return DEFAULT_VOKABEL_DATEI


VOKABEL_DATEI = _resolve_vokabel_datei()

def _sanitize_import_name(name):
    stem = Path(name or "import").stem
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return cleaned or "import"


def _is_path_within(parent, child):
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def _resolve_csv_path(csv_path=None):
    if csv_path is None:
        return VOKABEL_DATEI
    p = Path(csv_path)
    if not p.is_absolute():
        p = (BASE_DIR / p).resolve()
    else:
        p = p.resolve()
    return p


def _source_id_for_path(csv_path):
    p = _resolve_csv_path(csv_path)
    if p == VOKABEL_DATEI:
        return DEFAULT_SOURCE_ID
    if _is_path_within(IMPORTS_DIR, p):
        return p.name
    return DEFAULT_SOURCE_ID


def _resolve_source_from_id(source_id):
    src = (source_id or DEFAULT_SOURCE_ID).strip()
    if src == DEFAULT_SOURCE_ID:
        return VOKABEL_DATEI
    candidate = (IMPORTS_DIR / src).resolve()
    if not _is_path_within(IMPORTS_DIR, candidate):
        return VOKABEL_DATEI
    if candidate.suffix.lower() != ".csv":
        return VOKABEL_DATEI
    if not candidate.exists():
        return VOKABEL_DATEI
    return candidate


def _available_sources():
    out = [
        {
            "id": DEFAULT_SOURCE_ID,
            "label": f"Standard ({VOKABEL_DATEI.name})",
            "path": str(VOKABEL_DATEI),
        }
    ]
    for p in sorted(IMPORTS_DIR.glob("*.csv"), key=lambda x: x.name.lower()):
        out.append({"id": p.name, "label": f"Import: {p.name}", "path": str(p)})
    return out


def _load_learning_prefs():
    defaults = {
        "mode": "kartei",
        "block_size": 5,
        "repetitions": 1,
        "block_selection": "alle",
        "timer_enabled": False,
        "timer_seconds": 1,
        "repeats_per_word": 5,
        "total_rounds": 3,
        "with_declension_answer": False,
        "show_declension_inline": False,
        "audio_enabled": True,
        "difficulty_filter_enabled": False,
        "selected_difficulties": [],
        "selected_lektionen": [],
        "selected_uids": [],
        "source_id": DEFAULT_SOURCE_ID,
    }
    prefs = dict(defaults)
    raw = session.get("learning_prefs") or {}
    if isinstance(raw, dict):
        prefs.update(raw)
    prefs["mode"] = prefs.get("mode") if prefs.get("mode") in ALLOWED_MODES else "kartei"
    prefs["block_size"] = _safe_positive_int(prefs.get("block_size"), 5)
    prefs["repetitions"] = _safe_positive_int(prefs.get("repetitions"), 1)
    prefs["timer_seconds"] = _safe_positive_int(prefs.get("timer_seconds"), 1)
    prefs["repeats_per_word"] = _safe_positive_int(prefs.get("repeats_per_word"), 5)
    prefs["total_rounds"] = _safe_positive_int(prefs.get("total_rounds"), 3)
    prefs["block_selection"] = (str(prefs.get("block_selection") or "alle").strip().lower() or "alle")
    prefs["with_declension_answer"] = bool(prefs.get("with_declension_answer"))
    prefs["show_declension_inline"] = bool(prefs.get("show_declension_inline"))
    prefs["audio_enabled"] = bool(prefs.get("audio_enabled", True))
    prefs["timer_enabled"] = bool(prefs.get("timer_enabled", False))
    prefs["difficulty_filter_enabled"] = bool(prefs.get("difficulty_filter_enabled", False))
    prefs["selected_lektionen"] = [str(x) for x in (prefs.get("selected_lektionen") or []) if str(x).strip()]
    prefs["selected_uids"] = [str(x) for x in (prefs.get("selected_uids") or []) if str(x).strip()]
    selected_diff = []
    for x in (prefs.get("selected_difficulties") or []):
        try:
            n = int(x)
        except (TypeError, ValueError):
            continue
        if n in DIFFICULTY_LEVELS:
            selected_diff.append(n)
    prefs["selected_difficulties"] = sorted(set(selected_diff))
    prefs["source_id"] = str(prefs.get("source_id") or DEFAULT_SOURCE_ID)
    return prefs


def _normalize_text(value):
    return " ".join((value or "").strip().lower().split())


def _load_perf_history():
    if not PERF_HISTORY_FILE.exists():
        return {}
    try:
        data = json.loads(PERF_HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for uid, values in data.items():
        if not isinstance(uid, str) or not isinstance(values, list):
            continue
        cleaned = []
        for v in values[-PERF_WINDOW_SIZE:]:
            if v in (0, 1):
                cleaned.append(v)
            elif isinstance(v, bool):
                cleaned.append(1 if v else 0)
        out[uid] = cleaned
    return out


def _save_perf_history(history):
    PERF_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    PERF_HISTORY_FILE.write_text(json.dumps(history), encoding="utf-8")


def _record_performance_result(uid, correct):
    if not uid:
        return
    with DATA_LOCK:
        history = _load_perf_history()
        arr = list(history.get(uid, []))
        arr.append(1 if correct else 0)
        history[uid] = arr[-PERF_WINDOW_SIZE:]
        _save_perf_history(history)


def _replace_last_performance_result(uid, correct):
    if not uid:
        return
    with DATA_LOCK:
        history = _load_perf_history()
        arr = list(history.get(uid, []))
        if arr:
            arr[-1] = 1 if correct else 0
        else:
            arr = [1 if correct else 0]
        history[uid] = arr[-PERF_WINDOW_SIZE:]
        _save_perf_history(history)


def _remove_performance_history_for_uids(uids):
    uids = {u for u in (uids or []) if u}
    if not uids:
        return
    with DATA_LOCK:
        history = _load_perf_history()
        changed = False
        for uid in uids:
            if uid in history:
                del history[uid]
                changed = True
        if changed:
            _save_perf_history(history)


def _difficulty_level_for_vocab(v, recent_history):
    history = list(recent_history or [])
    attempts_total = _to_int(v.get("richtig", 0)) + _to_int(v.get("falsch", 0))
    if not history and attempts_total <= 0:
        return 3

    short = history[-10:] if history else []
    long = history[-PERF_WINDOW_SIZE:] if history else []

    if short:
        acc_short = sum(short) / len(short)
    else:
        acc_short = 0.5
    if long:
        acc_long = sum(long) / len(long)
    else:
        acc_long = 0.5

    wrong_last5 = 0
    if long:
        wrong_last5 = sum(1 for x in long[-5:] if x == 0)

    recent_difficulty = (1.0 - (0.7 * acc_short + 0.3 * acc_long))
    recent_difficulty = 0.75 * recent_difficulty + 0.25 * (wrong_last5 / 5.0)

    global_wrong = _to_int(v.get("falsch", 0))
    global_right = _to_int(v.get("richtig", 0))
    global_attempts = global_right + global_wrong
    global_error = (global_wrong / global_attempts) if global_attempts > 0 else 0.5

    confidence = min(1.0, len(long) / 12.0)
    score = confidence * recent_difficulty + (1.0 - confidence) * global_error

    if score <= 0.15:
        return 1
    if score <= 0.35:
        return 2
    if score <= 0.55:
        return 3
    if score <= 0.75:
        return 4
    return 5


def _difficulty_levels_for_vocab(vokabeln):
    history = _load_perf_history()
    levels = {}
    for v in vokabeln:
        uid = _make_uid(v)
        levels[uid] = _difficulty_level_for_vocab(v, history.get(uid, []))
    return levels


def _difficulty_counts(vokabeln, levels):
    counts = {lvl: 0 for lvl in DIFFICULTY_LEVELS}
    for v in vokabeln:
        lvl = levels.get(_make_uid(v), 3)
        counts[lvl] = counts.get(lvl, 0) + 1
    return counts


def _expected_answer(v, with_declension_answer=False):
    deutsch = (v.get("deutsch") or "").strip()
    deklination = (v.get("deklination") or "").strip()
    if with_declension_answer and deklination:
        return f"{deutsch} {deklination}".strip()
    return deutsch


def _load_runtime_secrets():
    if not RUNTIME_SECRETS_FILE.exists():
        return {}
    try:
        with RUNTIME_SECRETS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_runtime_secrets(data):
    RUNTIME_SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with RUNTIME_SECRETS_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f)


def _get_effective_openai_api_key():
    runtime_key = _load_runtime_secrets().get("OPENAI_API_KEY", "").strip()
    if runtime_key:
        return runtime_key
    return os.getenv("OPENAI_API_KEY", "").strip()


def _get_openai_client():
    global _OPENAI_CLIENT
    if OpenAI is None:
        return None
    if _OPENAI_CLIENT is not None:
        return _OPENAI_CLIENT

    api_key = _get_effective_openai_api_key()
    if not api_key:
        return None

    _OPENAI_CLIENT = OpenAI(api_key=api_key)
    return _OPENAI_CLIENT


def _audio_rel_path(uid, kind, text):
    digest = hashlib.sha1(f"{uid}|{kind}|{text}".encode("utf-8")).hexdigest()[:20]
    return f"audio_cache/{kind}_{digest}.mp3"


def _cached_audio_rel_path(uid, kind, text):
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    rel_path = _audio_rel_path(uid, kind, cleaned)
    abs_path = BASE_DIR / "static" / rel_path
    return rel_path if abs_path.exists() else None


def _delete_audio_files_for_vocab(v):
    deleted = 0
    uid = _make_uid(v)
    for kind, text in (("lat", v.get("fremdsprache", "")), ("de", v.get("deutsch", ""))):
        cleaned = (text or "").strip()
        if not cleaned:
            continue
        rel_path = _audio_rel_path(uid, kind, cleaned)
        abs_path = BASE_DIR / "static" / rel_path
        if abs_path.exists():
            try:
                abs_path.unlink()
                deleted += 1
            except Exception:
                pass
    return deleted


def _ensure_tts_audio(uid, kind, text):
    cleaned = (text or "").strip()
    if not cleaned:
        return None

    rel_path = _audio_rel_path(uid, kind, cleaned)
    abs_path = BASE_DIR / "static" / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)

    if abs_path.exists():
        return rel_path

    client = _get_openai_client()
    if client is None:
        return None

    with TTS_LOCK:
        if abs_path.exists():
            return rel_path
        try:
            response = client.audio.speech.create(
                model=TTS_MODEL,
                voice=TTS_VOICE,
                input=cleaned,
            )
            response.stream_to_file(str(abs_path))
            return rel_path
        except Exception:
            return None


def _generate_tts_audio_sync(uid, kind, text):
    """Generate exactly one audio file synchronously (blocking)."""
    rel_path = _ensure_tts_audio(uid, kind, text)
    if not rel_path:
        return None
    abs_path = BASE_DIR / "static" / rel_path
    if not abs_path.exists():
        return None
    if abs_path.stat().st_size <= 0:
        return None
    return rel_path


def _build_tts_cache(vokabeln):
    created = 0
    existing = 0
    failed = 0

    for v in vokabeln:
        uid = _make_uid(v)
        for kind, text in (("lat", v.get("fremdsprache", "")), ("de", v.get("deutsch", ""))):
            cleaned = (text or "").strip()
            if not cleaned:
                continue

            rel_path = _audio_rel_path(uid, kind, cleaned)
            abs_path = BASE_DIR / "static" / rel_path
            if abs_path.exists():
                existing += 1
                continue

            # Strictly sequential: one blocking API call, wait until file is written,
            # then continue with the next word.
            made = _generate_tts_audio_sync(uid, kind, cleaned)
            if made:
                created += 1
                time.sleep(max(0.0, TTS_DELAY_SECONDS))
                if TTS_MAX_NEW_PER_RUN > 0 and created >= TTS_MAX_NEW_PER_RUN:
                    return created, existing, failed
            else:
                failed += 1

    return created, existing, failed


def _acquire_tts_build_lock():
    TTS_BUILD_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(TTS_BUILD_LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return True
    except FileExistsError:
        return False


def _release_tts_build_lock():
    try:
        TTS_BUILD_LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def lade_vokabeln_full(csv_path=None):
    csv_file = _resolve_csv_path(csv_path)
    vokabeln = []
    if not csv_file.exists():
        return vokabeln

    with csv_file.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row = {k: (v or "") for k, v in row.items()}
            row["richtig"] = _to_int(row.get("richtig", 0))
            row["falsch"] = _to_int(row.get("falsch", 0))
            vokabeln.append(row)
    return vokabeln


def _write_vokabeln(csv_path, vokabeln):
    csv_file = _resolve_csv_path(csv_path)
    csv_file.parent.mkdir(parents=True, exist_ok=True)
    with csv_file.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for v in vokabeln:
            writer.writerow(
                {
                    "fremdsprache": v.get("fremdsprache", ""),
                    "deutsch": v.get("deutsch", ""),
                    "deklination": v.get("deklination", ""),
                    "lektion": v.get("lektion", ""),
                    "richtig": _to_int(v.get("richtig", 0)),
                    "falsch": _to_int(v.get("falsch", 0)),
                }
            )


def speichere_vokabeln_full(vokabeln, csv_path=None):
    with DATA_LOCK:
        _write_vokabeln(csv_path, vokabeln)




@contextlib.contextmanager
def _locked_vocab_update(csv_path=None):
    with DATA_LOCK:
        path = _resolve_csv_path(csv_path)
        master = lade_vokabeln_full(path)
        yield master
        _write_vokabeln(path, master)


def alle_lektionen(vokabeln):
    return sorted({v.get("lektion", "") for v in vokabeln if v.get("lektion", "")})


def _make_uid(v):
    return f"{v.get('fremdsprache','')}|{v.get('deutsch','')}|{v.get('lektion','')}"


def _apply_scoring(uid, user_answer, master, with_declension_answer=False):
    normalized = _normalize_text(user_answer)
    for v in master:
        if _make_uid(v) != uid:
            continue

        expected = _expected_answer(v, with_declension_answer=with_declension_answer)
        expected_normalized = _normalize_text(expected)
        correct = normalized == expected_normalized
        if correct:
            v["richtig"] = _to_int(v.get("richtig", 0)) + 1
        else:
            v["falsch"] = _to_int(v.get("falsch", 0)) + 1
        return correct, expected

    return False, ""


def _safe_positive_int(value, default_value):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default_value
    return n if n > 0 else default_value


def _current_role():
    return session.get("role")


def _is_admin():
    return _current_role() == "admin"


def _home_endpoint():
    return "index" if _is_admin() else "learn_home"


def _csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


@app.context_processor
def _inject_csrf():
    return {"csrf_token": _csrf_token}


def _filter_targets(
    vokabeln,
    selected_lektionen,
    selected_uids,
    difficulty_filter_enabled=False,
    selected_difficulties=None,
    difficulty_levels=None,
):
    targets = [v for v in vokabeln if v.get("lektion") in selected_lektionen]
    if selected_uids:
        selected_uids_set = set(selected_uids)
        targets = [v for v in targets if _make_uid(v) in selected_uids_set]
    if difficulty_filter_enabled:
        selected_difficulties = set(selected_difficulties or DIFFICULTY_LEVELS)
        difficulty_levels = difficulty_levels or {}
        targets = [v for v in targets if difficulty_levels.get(_make_uid(v), 3) in selected_difficulties]
    return targets


def _build_queue(
    vokabeln,
    mode,
    selected_lektionen,
    selected_uids,
    block_size,
    block_selection,
    repetitions,
    difficulty_filter_enabled=False,
    selected_difficulties=None,
    difficulty_levels=None,
):
    targets = _filter_targets(
        vokabeln=vokabeln,
        selected_lektionen=selected_lektionen,
        selected_uids=selected_uids,
        difficulty_filter_enabled=difficulty_filter_enabled,
        selected_difficulties=selected_difficulties,
        difficulty_levels=difficulty_levels,
    )

    if mode == "block":
        if not targets:
            return []

        blocks = [targets[i : i + block_size] for i in range(0, len(targets), block_size)]

        if block_selection == "alle":
            selected_block_indices = list(range(len(blocks)))
        else:
            selected_block_indices = []
            for item in (block_selection or "").split(","):
                item = item.strip()
                if item.isdigit():
                    idx = int(item) - 1
                    if 0 <= idx < len(blocks):
                        selected_block_indices.append(idx)

            if not selected_block_indices:
                selected_block_indices = list(range(len(blocks)))

        queue = []
        for _ in range(repetitions):
            round_items = []
            for block_idx in selected_block_indices:
                for v in blocks[block_idx]:
                    round_items.append({"uid": _make_uid(v), "display": v})
            random.shuffle(round_items)
            queue.extend(round_items)
        return queue

    return [{"uid": _make_uid(v), "display": v} for v in targets]


def _select_words_by_blocks(
    vokabeln,
    selected_lektionen,
    selected_uids,
    block_size,
    block_selection,
    difficulty_filter_enabled=False,
    selected_difficulties=None,
    difficulty_levels=None,
):
    targets = _filter_targets(
        vokabeln=vokabeln,
        selected_lektionen=selected_lektionen,
        selected_uids=selected_uids,
        difficulty_filter_enabled=difficulty_filter_enabled,
        selected_difficulties=selected_difficulties,
        difficulty_levels=difficulty_levels,
    )
    if not targets:
        return []

    blocks = [targets[i : i + block_size] for i in range(0, len(targets), block_size)]

    if (block_selection or "").strip().lower() == "alle":
        selected_block_indices = list(range(len(blocks)))
    else:
        selected_block_indices = []
        for item in (block_selection or "").split(","):
            item = item.strip()
            if item.isdigit():
                idx = int(item) - 1
                if 0 <= idx < len(blocks):
                    selected_block_indices.append(idx)
        if not selected_block_indices:
            selected_block_indices = list(range(len(blocks)))

    words = []
    for block_idx in selected_block_indices:
        words.extend(blocks[block_idx])
    return words


def _build_auto_audio_playlist(
    vokabeln,
    selected_lektionen,
    selected_uids,
    block_size,
    block_selection,
    repeats_per_word,
    total_rounds,
    difficulty_filter_enabled=False,
    selected_difficulties=None,
    difficulty_levels=None,
):
    words = _select_words_by_blocks(
        vokabeln=vokabeln,
        selected_lektionen=selected_lektionen,
        selected_uids=selected_uids,
        block_size=block_size,
        block_selection=block_selection,
        difficulty_filter_enabled=difficulty_filter_enabled,
        selected_difficulties=selected_difficulties,
        difficulty_levels=difficulty_levels,
    )
    playlist = []
    playable_words = 0
    skipped_words = 0

    for _round in range(total_rounds):
        for v in words:
            uid = _make_uid(v)
            lat_rel = _cached_audio_rel_path(uid, "lat", v.get("fremdsprache", ""))
            de_rel = _cached_audio_rel_path(uid, "de", v.get("deutsch", ""))

            if not lat_rel or not de_rel:
                skipped_words += 1
                continue

            playable_words += 1
            lat_url = url_for("static", filename=lat_rel)
            de_url = url_for("static", filename=de_rel)

            for _ in range(repeats_per_word):
                playlist.append(
                    {
                        "url": lat_url,
                        "label": v.get("fremdsprache", ""),
                        "type": "Fremdwort",
                        "lesson": v.get("lektion", ""),
                    }
                )
                playlist.append(
                    {
                        "url": de_url,
                        "label": v.get("deutsch", ""),
                        "type": "Deutsch",
                        "lesson": v.get("lektion", ""),
                    }
                )

    return playlist, playable_words, skipped_words, len(words)


def _build_kartei_state(queue):
    boxes = {}
    wrong_counts = {}
    round_queue = []
    for item in queue:
        uid = item.get("uid", "")
        if not uid or uid in boxes:
            continue
        boxes[uid] = 1
        wrong_counts[uid] = 0
        round_queue.append(uid)
    random.shuffle(round_queue)
    return {
        "boxes": boxes,
        "wrong_counts": wrong_counts,
        "round_queue": round_queue,
        "round_index": 0,
        "asked_total": 0,
    }


def _kartei_prepare_current_item(state):
    kartei = state.get("kartei") or {}
    boxes = {str(k): _to_int(v) for k, v in (kartei.get("boxes") or {}).items() if str(k).strip()}
    if not boxes:
        return None, None

    if all(min(5, max(1, box)) >= 5 for box in boxes.values()):
        return None, None

    pool = state.get("queue", [])
    pool_by_uid = {item.get("uid", ""): item for item in pool if item.get("uid")}
    round_queue = [str(x) for x in (kartei.get("round_queue") or []) if str(x).strip() in boxes]
    round_index = _to_int(kartei.get("round_index", 0))
    if round_index < 0:
        round_index = 0

    if round_index >= len(round_queue):
        round_queue = [uid for uid, box in boxes.items() if min(5, max(1, box)) < 5]
        random.shuffle(round_queue)
        round_index = 0

    while round_index < len(round_queue) and round_queue[round_index] not in pool_by_uid:
        round_index += 1

    if round_index >= len(round_queue):
        return None, None

    uid = round_queue[round_index]
    item = pool_by_uid[uid]
    kartei["boxes"] = boxes
    kartei["round_queue"] = round_queue
    kartei["round_index"] = round_index
    kartei["wrong_counts"] = {str(k): _to_int(v) for k, v in (kartei.get("wrong_counts") or {}).items() if str(k).strip()}
    kartei["asked_total"] = _to_int(kartei.get("asked_total", 0))
    state["kartei"] = kartei
    info = {
        "current_box": min(5, max(1, _to_int(boxes.get(uid, 1)))),
        "open_words": sum(1 for box in boxes.values() if min(5, max(1, box)) < 5),
        "mastered_words": sum(1 for box in boxes.values() if min(5, max(1, box)) >= 5),
        "total_words": len(boxes),
        "round_current": round_index + 1,
        "round_total": len(round_queue),
    }
    return item, info


@app.before_request
def _protect_routes():
    endpoint = request.endpoint or ""
    if endpoint.startswith("static"):
        return None

    if request.method == "POST":
        sent_token = (request.form.get("_csrf_token") or "").strip()
        expected_token = session.get("_csrf_token", "")
        if not sent_token or not expected_token or not secrets.compare_digest(sent_token, expected_token):
            return "Bad Request: Ungueltiges CSRF-Token. Bitte Seite neu laden.", 400

    if endpoint in {"access_gate", "submit_access_code"}:
        return None

    role = _current_role()
    if role not in {"admin", "learner"}:
        return redirect(url_for("access_gate"))

    if role != "admin" and endpoint in ADMIN_ONLY_ENDPOINTS:
        return redirect(url_for("learn_home"))

    return None


@app.get("/")
def index():
    if not _is_admin():
        return redirect(url_for("learn_home"))

    prefs = _load_learning_prefs()
    source_path = _resolve_source_from_id(prefs.get("source_id"))
    selected_source_id = _source_id_for_path(source_path)
    vokabeln = lade_vokabeln_full(source_path)
    difficulty_levels = _difficulty_levels_for_vocab(vokabeln)
    difficulty_counts = _difficulty_counts(vokabeln, difficulty_levels)
    lektionen = alle_lektionen(vokabeln)
    valid_uids = {_make_uid(v) for v in vokabeln}
    prefs["selected_lektionen"] = [l for l in prefs.get("selected_lektionen", []) if l in lektionen]
    prefs["selected_uids"] = [u for u in prefs.get("selected_uids", []) if u in valid_uids]
    prefs["selected_difficulties"] = [d for d in prefs.get("selected_difficulties", []) if d in DIFFICULTY_LEVELS]
    prefs["source_id"] = selected_source_id
    status = request.args.get("status")
    message = request.args.get("message")
    has_runtime_key = bool(_load_runtime_secrets().get("OPENAI_API_KEY", "").strip())
    tts_ready = bool(_get_effective_openai_api_key()) and (_get_openai_client() is not None)
    all_vocab = [
        {
            "uid": _make_uid(v),
            "fremdsprache": v.get("fremdsprache", ""),
            "deutsch": v.get("deutsch", ""),
            "deklination": v.get("deklination", ""),
            "lektion": v.get("lektion", ""),
            "difficulty": difficulty_levels.get(_make_uid(v), 3),
        }
        for v in vokabeln
    ]
    return render_template(
        "index.html",
        lektionen=lektionen,
        total=len(vokabeln),
        error=None if vokabeln else f"{source_path} wurde nicht gefunden oder ist leer.",
        status=status,
        message=message,
        tts_ready=tts_ready,
        has_runtime_key=has_runtime_key,
        prefs=prefs,
        sources=_available_sources(),
        selected_source_id=selected_source_id,
        all_vocab=all_vocab,
        difficulty_levels=DIFFICULTY_LEVELS,
        difficulty_labels=DIFFICULTY_LABELS,
        difficulty_counts=difficulty_counts,
    )


@app.get("/learn")
def learn_home():
    prefs = _load_learning_prefs()
    source_path = _resolve_source_from_id(prefs.get("source_id"))
    selected_source_id = _source_id_for_path(source_path)
    vokabeln = lade_vokabeln_full(source_path)
    difficulty_levels = _difficulty_levels_for_vocab(vokabeln)
    difficulty_counts = _difficulty_counts(vokabeln, difficulty_levels)
    lektionen = alle_lektionen(vokabeln)
    valid_uids = {_make_uid(v) for v in vokabeln}
    prefs["selected_lektionen"] = [l for l in prefs.get("selected_lektionen", []) if l in lektionen]
    prefs["selected_uids"] = [u for u in prefs.get("selected_uids", []) if u in valid_uids]
    prefs["selected_difficulties"] = [d for d in prefs.get("selected_difficulties", []) if d in DIFFICULTY_LEVELS]
    prefs["source_id"] = selected_source_id
    status = request.args.get("status")
    message = request.args.get("message")
    all_vocab = [
        {
            "uid": _make_uid(v),
            "fremdsprache": v.get("fremdsprache", ""),
            "deutsch": v.get("deutsch", ""),
            "deklination": v.get("deklination", ""),
            "lektion": v.get("lektion", ""),
            "difficulty": difficulty_levels.get(_make_uid(v), 3),
        }
        for v in vokabeln
    ]
    return render_template(
        "learn.html",
        lektionen=lektionen,
        total=len(vokabeln),
        error=None if vokabeln else f"{source_path} wurde nicht gefunden oder ist leer.",
        status=status,
        message=message,
        prefs=prefs,
        sources=_available_sources(),
        selected_source_id=selected_source_id,
        all_vocab=all_vocab,
        difficulty_levels=DIFFICULTY_LEVELS,
        difficulty_labels=DIFFICULTY_LABELS,
        difficulty_counts=difficulty_counts,
    )


@app.get("/access")
def access_gate():
    if _current_role() in {"admin", "learner"}:
        return redirect(url_for(_home_endpoint()))
    return render_template("access_gate.html", error=None)


@app.post("/access")
def submit_access_code():
    code = (request.form.get("access_code") or "").strip()
    if code == ADMIN_ACCESS_CODE:
        session["role"] = "admin"
        return redirect(url_for("index"))
    if code == LEARNER_ACCESS_CODE:
        session["role"] = "learner"
        return redirect(url_for("learn_home"))
    return render_template("access_gate.html", error="Falscher Code.")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("access_gate"))


@app.get("/manage_vocab")
def manage_vocab():
    source_id = request.args.get("source_id") or _load_learning_prefs().get("source_id", DEFAULT_SOURCE_ID)
    source_path = _resolve_source_from_id(source_id)
    vokabeln = lade_vokabeln_full(source_path)
    grouped = {}
    for v in vokabeln:
        lek = v.get("lektion", "")
        grouped.setdefault(lek, []).append(v)
    sorted_lessons = sorted(grouped.keys(), key=lambda x: str(x))
    return render_template(
        "manage_vocab.html",
        grouped=grouped,
        lessons=sorted_lessons,
        total=len(vokabeln),
        can_edit=_is_admin() and source_path == VOKABEL_DATEI,
        source_id=_source_id_for_path(source_path),
        sources=_available_sources(),
    )


@app.post("/delete_vocab")
def delete_vocab():
    uid = (request.form.get("uid") or "").strip()
    if not uid:
        return redirect(url_for("manage_vocab"))

    master = lade_vokabeln_full()
    to_delete = [v for v in master if _make_uid(v) == uid]
    kept = [v for v in master if _make_uid(v) != uid]

    if not to_delete:
        return redirect(url_for("index", status="error", message="Vokabel nicht gefunden."))

    audio_deleted = 0
    for v in to_delete:
        audio_deleted += _delete_audio_files_for_vocab(v)

    speichere_vokabeln_full(kept)
    _remove_performance_history_for_uids([_make_uid(v) for v in to_delete])
    return redirect(
        url_for(
            "index",
            status="ok",
            message=f"Vokabel geloescht. Audios entfernt: {audio_deleted}",
        )
    )


@app.post("/delete_lesson")
def delete_lesson():
    lektion = (request.form.get("lektion") or "").strip()
    if not lektion:
        return redirect(url_for("manage_vocab"))

    master = lade_vokabeln_full()
    to_delete = [v for v in master if (v.get("lektion") or "") == lektion]
    kept = [v for v in master if (v.get("lektion") or "") != lektion]

    if not to_delete:
        return redirect(url_for("index", status="error", message="Lektion nicht gefunden."))

    audio_deleted = 0
    for v in to_delete:
        audio_deleted += _delete_audio_files_for_vocab(v)

    speichere_vokabeln_full(kept)
    _remove_performance_history_for_uids([_make_uid(v) for v in to_delete])
    return redirect(
        url_for(
            "index",
            status="ok",
            message=f"Lektion {lektion} geloescht ({len(to_delete)} Woerter). Audios entfernt: {audio_deleted}",
        )
    )


@app.get("/audio_files")
def audio_files():
    files = []
    for path in sorted(AUDIO_CACHE_DIR.glob("*.mp3"), key=lambda p: p.name.lower()):
        stat = path.stat()
        files.append(
            {
                "name": path.name,
                "size_kb": round(stat.st_size / 1024, 1),
                "audio_url": url_for("static", filename=f"audio_cache/{path.name}"),
            }
        )

    return render_template("audio_files.html", files=files, total=len(files))


@app.get("/export_csv")
def export_csv():
    if not VOKABEL_DATEI.exists():
        return redirect(url_for("index", status="error", message="CSV nicht gefunden."))
    return send_file(
        str(VOKABEL_DATEI),
        as_attachment=True,
        download_name=f"vokabeln_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        mimetype="text/csv",
    )


@app.get("/export_audio_zip")
def export_audio_zip():
    memory = io.BytesIO()
    with zipfile.ZipFile(memory, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(AUDIO_CACHE_DIR.glob("*.mp3"), key=lambda x: x.name.lower()):
            zf.write(p, arcname=p.name)
    memory.seek(0)
    return send_file(
        memory,
        as_attachment=True,
        download_name=f"audio_cache_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
        mimetype="application/zip",
    )


@app.post("/import_csv")
def import_csv():
    upload = request.files.get("csv_file")
    if upload is None or not upload.filename:
        return redirect(url_for("index", status="error", message="Bitte eine CSV-Datei auswaehlen."))

    safe_stem = _sanitize_import_name(upload.filename)
    target_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_stem}.csv"
    target_path = (IMPORTS_DIR / target_name).resolve()
    if not _is_path_within(IMPORTS_DIR, target_path):
        return redirect(url_for("index", status="error", message="Ungueltiger Dateiname."))

    try:
        text = upload.read().decode("utf-8-sig")
    except Exception:
        return redirect(url_for("index", status="error", message="CSV konnte nicht als UTF-8 gelesen werden."))

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return redirect(url_for("index", status="error", message="CSV ohne Header."))

    norm_fields = {str(x).strip().lower(): x for x in reader.fieldnames}
    required = ["fremdsprache", "deutsch", "lektion"]
    if any(r not in norm_fields for r in required):
        return redirect(
            url_for(
                "index",
                status="error",
                message="CSV braucht Header: fremdsprache,deutsch,lektion (deklination optional).",
            )
        )

    rows = []
    for row in reader:
        fremd = (row.get(norm_fields["fremdsprache"]) or "").strip()
        de = (row.get(norm_fields["deutsch"]) or "").strip()
        lek = (row.get(norm_fields["lektion"]) or "").strip()
        dekl = (row.get(norm_fields.get("deklination", "")) or "").strip() if "deklination" in norm_fields else ""
        if not fremd and not de and not lek and not dekl:
            continue
        if not fremd or not de or not lek:
            continue
        rows.append(
            {
                "fremdsprache": fremd,
                "deutsch": de,
                "deklination": dekl,
                "lektion": lek,
                "richtig": 0,
                "falsch": 0,
            }
        )

    if not rows:
        return redirect(url_for("index", status="error", message="CSV enthaelt keine gueltigen Zeilen."))

    speichere_vokabeln_full(rows, csv_path=target_path)
    prefs = _load_learning_prefs()
    prefs["source_id"] = target_path.name
    session["learning_prefs"] = prefs

    return redirect(
        url_for(
            "index",
            status="ok",
            message=f"CSV importiert: {target_path.name} ({len(rows)} Eintraege).",
        )
    )


@app.post("/build_tts_cache")
def build_tts_cache():
    vokabeln = lade_vokabeln_full()
    if not vokabeln:
        return redirect(url_for("index", status="error", message="Keine Vokabeln gefunden."))

    if _get_openai_client() is None:
        return redirect(
            url_for(
                "index",
                status="error",
                message="OPENAI_API_KEY fehlt. Bitte in .env setzen und App neu starten.",
            )
        )

    if not _acquire_tts_build_lock():
        return redirect(
            url_for(
                "index",
                status="error",
                message="Audio-Cache-Build laeuft bereits. Bitte kurz warten und erneut versuchen.",
            )
        )

    try:
        created, existing, failed = _build_tts_cache(vokabeln)
    finally:
        _release_tts_build_lock()

    msg = (
        "Audio-Cache fertig: "
        f"neu={created}, bereits da={existing}, fehlgeschlagen={failed}, "
        f"delay={TTS_DELAY_SECONDS}s, max-neu-pro-lauf={TTS_MAX_NEW_PER_RUN}"
    )
    status = "ok" if failed == 0 else "error"
    return redirect(url_for("index", status=status, message=msg))


@app.post("/set_api_key")
def set_api_key():
    global _OPENAI_CLIENT

    api_key = (request.form.get("openai_api_key") or "").strip()
    secrets = _load_runtime_secrets()

    if not api_key:
        if "OPENAI_API_KEY" in secrets:
            del secrets["OPENAI_API_KEY"]
            _save_runtime_secrets(secrets)
        _OPENAI_CLIENT = None
        return redirect(url_for("index", status="ok", message="Browser-API-Key wurde entfernt."))

    secrets["OPENAI_API_KEY"] = api_key
    _save_runtime_secrets(secrets)
    _OPENAI_CLIENT = None
    return redirect(url_for("index", status="ok", message="API-Key im Browser gespeichert."))


@app.post("/add_vocab")
def add_vocab():
    fremd_list = request.form.getlist("fremdsprache[]") or [request.form.get("fremdsprache", "")]
    deutsch_list = request.form.getlist("deutsch[]") or [request.form.get("deutsch", "")]
    dekl_list = request.form.getlist("deklination[]") or [request.form.get("deklination", "")]
    lektion_list = request.form.getlist("lektion[]") or [request.form.get("lektion", "")]

    max_len = max(len(fremd_list), len(deutsch_list), len(dekl_list), len(lektion_list))
    rows = []
    for i in range(max_len):
        fremd = (fremd_list[i] if i < len(fremd_list) else "").strip()
        deutsch = (deutsch_list[i] if i < len(deutsch_list) else "").strip()
        deklination = (dekl_list[i] if i < len(dekl_list) else "").strip()
        lektion = (lektion_list[i] if i < len(lektion_list) else "").strip()
        if not fremd and not deutsch and not deklination and not lektion:
            continue
        rows.append(
            {
                "fremdsprache": fremd,
                "deutsch": deutsch,
                "deklination": deklination,
                "lektion": lektion,
                "richtig": 0,
                "falsch": 0,
            }
        )

    if not rows:
        return redirect(url_for("index", status="error", message="Keine Vokabeln uebergeben."))

    if any((not r["fremdsprache"] or not r["deutsch"] or not r["lektion"]) for r in rows):
        return redirect(
            url_for(
                "index",
                status="error",
                message="Bitte in jeder Zeile Fremdsprache, Deutsch und Lektion ausfuellen.",
            )
        )

    added = 0
    duplicates = 0
    with _locked_vocab_update() as master:
        existing_uids = {_make_uid(v) for v in master}
        input_uids = set()
        for row in rows:
            uid = _make_uid(row)
            if uid in existing_uids or uid in input_uids:
                duplicates += 1
                continue
            master.append(row)
            existing_uids.add(uid)
            input_uids.add(uid)
            added += 1

    if added == 0:
        return redirect(url_for("index", status="error", message="Alle Eintraege waren Duplikate."))

    msg = f"{added} Vokabel(n) gespeichert."
    if duplicates:
        msg += f" Duplikate uebersprungen: {duplicates}."
    return redirect(url_for("index", status="ok", message=msg))


@app.post("/start")
def start():
    prefs = _load_learning_prefs()
    source_id = (request.form.get("source_id") or prefs.get("source_id") or DEFAULT_SOURCE_ID).strip()
    source_path = _resolve_source_from_id(source_id)
    source_id = _source_id_for_path(source_path)

    vokabeln = lade_vokabeln_full(source_path)
    if not vokabeln:
        return redirect(url_for(_home_endpoint(), status="error", message=f"Quelle ist leer: {source_path.name}"))
    difficulty_levels = _difficulty_levels_for_vocab(vokabeln)

    mode = (request.form.get("mode") or prefs.get("mode") or "kartei").strip().lower()
    if mode not in ALLOWED_MODES:
        mode = "kartei"

    selected_lektionen = request.form.getlist("lektionen")
    lessons = alle_lektionen(vokabeln)
    if not selected_lektionen:
        selected_lektionen = [x for x in prefs.get("selected_lektionen", []) if x in lessons]
    if not selected_lektionen:
        selected_lektionen = lessons

    valid_uids = {_make_uid(v) for v in vokabeln}
    selected_uids = [u for u in request.form.getlist("selected_uids") if u in valid_uids]

    block_size = _safe_positive_int(request.form.get("block_size", str(prefs.get("block_size", 5))), 5)
    repetitions = _safe_positive_int(request.form.get("repetitions", str(prefs.get("repetitions", 1))), 1)
    timer_seconds = _safe_positive_int(request.form.get("timer_seconds", str(prefs.get("timer_seconds", 1))), 1)
    repeats_per_word = _safe_positive_int(request.form.get("repeats_per_word", str(prefs.get("repeats_per_word", 5))), 5)
    total_rounds = _safe_positive_int(request.form.get("total_rounds", str(prefs.get("total_rounds", 3))), 3)
    block_selection = request.form.get("block_selection", prefs.get("block_selection", "alle")).strip().lower() or "alle"

    with_declension_answer = request.form.get("with_declension_answer") == "on"
    show_declension_inline = request.form.get("show_declension_inline") == "on"
    audio_enabled = request.form.get("audio_enabled") == "on"
    timer_enabled = request.form.get("timer_enabled") == "on"
    difficulty_filter_enabled = request.form.get("difficulty_filter_enabled") == "on"
    selected_difficulties = []
    for raw in request.form.getlist("selected_difficulties"):
        try:
            lvl = int(raw)
        except (TypeError, ValueError):
            continue
        if lvl in DIFFICULTY_LEVELS:
            selected_difficulties.append(lvl)
    selected_difficulties = sorted(set(selected_difficulties))
    if difficulty_filter_enabled and not selected_difficulties:
        selected_difficulties = list(DIFFICULTY_LEVELS)

    session["learning_prefs"] = {
        "mode": mode,
        "block_size": block_size,
        "repetitions": repetitions,
        "block_selection": block_selection,
        "timer_enabled": timer_enabled,
        "timer_seconds": timer_seconds,
        "repeats_per_word": repeats_per_word,
        "total_rounds": total_rounds,
        "with_declension_answer": with_declension_answer,
        "show_declension_inline": show_declension_inline,
        "audio_enabled": audio_enabled,
        "difficulty_filter_enabled": difficulty_filter_enabled,
        "selected_difficulties": selected_difficulties,
        "selected_lektionen": selected_lektionen,
        "selected_uids": selected_uids,
        "source_id": source_id,
    }

    if mode == "auto_audio":
        playlist, playable_words, skipped_words, selected_words = _build_auto_audio_playlist(
            vokabeln=vokabeln,
            selected_lektionen=selected_lektionen,
            selected_uids=selected_uids,
            block_size=block_size,
            block_selection=block_selection,
            repeats_per_word=repeats_per_word,
            total_rounds=total_rounds,
            difficulty_filter_enabled=difficulty_filter_enabled,
            selected_difficulties=selected_difficulties,
            difficulty_levels=difficulty_levels,
        )
        if not playlist:
            return redirect(
                url_for(
                    _home_endpoint(),
                    status="error",
                    message="Keine abspielbaren Audios gefunden. Bitte erst Audio-Cache erzeugen.",
                )
            )
        return render_template(
            "auto_audio.html",
            playlist=playlist,
            selected_words=selected_words,
            playable_words=playable_words,
            skipped_words=skipped_words,
            repeats_per_word=repeats_per_word,
            total_rounds=total_rounds,
        )

    if mode == "durchlauf":
        queue = _build_queue(
            vokabeln=vokabeln,
            mode="block",
            selected_lektionen=selected_lektionen,
            selected_uids=selected_uids,
            block_size=block_size,
            block_selection=block_selection,
            repetitions=repetitions,
            difficulty_filter_enabled=difficulty_filter_enabled,
            selected_difficulties=selected_difficulties,
            difficulty_levels=difficulty_levels,
        )
        if not queue:
            return redirect(
                url_for(
                    _home_endpoint(),
                    status="error",
                    message="Keine Vokabeln fuer den Durchlauf gefunden.",
                )
            )
        return render_template(
            "through_mode.html",
            queue=queue,
            timer_enabled=timer_enabled,
            timer_seconds=timer_seconds,
            show_declension_inline=show_declension_inline,
        )

    queue = _build_queue(
        vokabeln=vokabeln,
        mode=mode,
        selected_lektionen=selected_lektionen,
        selected_uids=selected_uids,
        block_size=block_size,
        block_selection=block_selection,
        repetitions=repetitions,
        difficulty_filter_enabled=difficulty_filter_enabled,
        selected_difficulties=selected_difficulties,
        difficulty_levels=difficulty_levels,
    )
    if mode != "block":
        random.shuffle(queue)
    if not queue:
        return redirect(
            url_for(
                _home_endpoint(),
                status="error",
                message="Keine Vokabeln fuer die Auswahl gefunden.",
            )
        )

    state = {
        "mode": mode,
        "source_id": source_id,
        "selected_lektionen": selected_lektionen,
        "selected_uids": selected_uids,
        "difficulty_filter_enabled": difficulty_filter_enabled,
        "selected_difficulties": selected_difficulties,
        "with_declension_answer": with_declension_answer,
        "show_declension_inline": show_declension_inline,
        "audio_enabled": audio_enabled,
        "queue": queue,
        "index": 0,
        "wrong": [],
        "last_feedback": None,
    }
    if mode == "kartei":
        state["kartei"] = _build_kartei_state(queue)
    session["state"] = state

    return redirect(url_for("quiz"))


@app.get("/quiz")
def quiz():
    state = session.get("state")
    if not state:
        return redirect(url_for(_home_endpoint()))

    mode = state.get("mode", "kartei")
    if mode == "kartei":
        item, kartei_info = _kartei_prepare_current_item(state)
        session["state"] = state
        if item is None:
            return redirect(url_for("summary"))
        idx = _to_int((state.get("kartei") or {}).get("round_index", 0))
        current = kartei_info.get("round_current", idx + 1)
        total = kartei_info.get("round_total", len((state.get("kartei") or {}).get("round_queue", [])))
    else:
        kartei_info = None
        queue = state.get("queue", [])
        idx = state.get("index", 0)
        if idx >= len(queue):
            return redirect(url_for("summary"))
        item = queue[idx]
        current = idx + 1
        total = len(queue)

    v = item["display"]
    audio_enabled = bool(state.get("audio_enabled", True))
    question_audio_url = None
    if audio_enabled:
        audio_rel = _cached_audio_rel_path(item["uid"], "lat", v.get("fremdsprache", ""))
        question_audio_url = url_for("static", filename=audio_rel) if audio_rel else None

    answer_hint = "Gib die deutsche Bedeutung ein."
    if state.get("with_declension_answer"):
        answer_hint = "Gib erst die Uebersetzung, dann Leerzeichen, dann die Deklination ein."

    return render_template(
        "quiz.html",
        mode=mode,
        current=current,
        total=total,
        vokabel=v,
        uid=item["uid"],
        question_audio_url=question_audio_url,
        audio_enabled=audio_enabled,
        show_declension_inline=bool(state.get("show_declension_inline", False)),
        answer_hint=answer_hint,
        kartei_info=kartei_info,
    )


@app.post("/answer")
def answer():
    state = session.get("state")
    if not state:
        return redirect(url_for(_home_endpoint()))

    mode = state.get("mode", "kartei")
    queue = state.get("queue", [])
    idx = state.get("index", 0)

    if mode != "kartei" and idx >= len(queue):
        return redirect(url_for("summary"))

    uid = request.form.get("uid", "")
    user_answer = request.form.get("answer", "")

    source_path = _resolve_source_from_id(state.get("source_id"))
    with _locked_vocab_update(source_path) as master:
        correct, expected = _apply_scoring(
            uid,
            user_answer,
            master,
            with_declension_answer=bool(state.get("with_declension_answer", False)),
        )
    _record_performance_result(uid, correct)

    current_display = None
    if mode == "kartei":
        kartei = state.get("kartei") or {}
        boxes = {str(k): _to_int(v) for k, v in (kartei.get("boxes") or {}).items() if str(k).strip()}
        wrong_counts = {str(k): _to_int(v) for k, v in (kartei.get("wrong_counts") or {}).items() if str(k).strip()}
        round_queue = [str(x) for x in (kartei.get("round_queue") or []) if str(x).strip()]
        round_index = _to_int(kartei.get("round_index", 0))
        if uid not in boxes:
            return redirect(url_for("quiz"))
        prev_box = min(5, max(1, _to_int(boxes.get(uid, 1))))
        pool_by_uid = {item.get("uid", ""): item for item in queue if item.get("uid")}
        current_display = (pool_by_uid.get(uid) or {}).get("display", {})

        if correct:
            boxes[uid] = min(5, prev_box + 1)
        else:
            boxes[uid] = prev_box
            wrong_counts[uid] = _to_int(wrong_counts.get(uid, 0)) + 1

        question_idx = _to_int(kartei.get("asked_total", 0))
        if not correct:
            state.setdefault("wrong", []).append(
                {
                    "question_idx": question_idx,
                    "frage": current_display.get("fremdsprache", ""),
                    "expected": expected,
                    "answer": user_answer,
                }
            )

        kartei["boxes"] = boxes
        kartei["wrong_counts"] = wrong_counts
        if round_index < len(round_queue):
            kartei["round_index"] = round_index + 1
        else:
            kartei["round_index"] = round_index
        kartei["asked_total"] = question_idx + 1
        state["kartei"] = kartei
        state["index"] = kartei["asked_total"]
    if mode != "kartei" and not correct:
        state.setdefault("wrong", []).append(
            {
                "question_idx": idx,
                "frage": queue[idx]["display"].get("fremdsprache", ""),
                "expected": expected,
                "answer": user_answer,
            }
        )
        if mode == "block" and idx < len(queue):
            # Keine Extra-Frage erzeugen: nur eine spaetere, ohnehin geplante
            # Wiederholung dieses Worts direkt nach vorne ziehen.
            current_uid = queue[idx]["uid"]
            next_same_idx = None
            for pos in range(idx + 1, len(queue)):
                if queue[pos]["uid"] == current_uid:
                    next_same_idx = pos
                    break
            if next_same_idx is not None and next_same_idx != idx + 1:
                moved = queue.pop(next_same_idx)
                queue.insert(idx + 1, moved)

    state["last_feedback"] = {
        "uid": uid,
        "was_wrong": (not correct),
        "question_idx": idx if mode != "kartei" else _to_int((state.get("kartei") or {}).get("asked_total", 1)) - 1,
        "kartei_prev_box": (prev_box if mode == "kartei" else None),
    }
    if mode != "kartei":
        state["index"] = idx + 1
    session["state"] = state

    answer_audio_url = None
    if bool(state.get("audio_enabled", True)):
        if mode == "kartei":
            translation_text = (current_display or {}).get("deutsch", "")
        else:
            translation_text = queue[idx]["display"].get("deutsch", "")
        answer_audio_rel = _cached_audio_rel_path(uid, "de", translation_text)
        answer_audio_url = url_for("static", filename=answer_audio_rel) if answer_audio_rel else None

    return render_template(
        "feedback.html",
        correct=correct,
        expected=expected,
        user_answer=user_answer,
        mode=mode,
        can_mark_correct=(not correct),
        answer_audio_url=answer_audio_url,
        audio_enabled=bool(state.get("audio_enabled", True)),
    )


@app.get("/next")
def next_question():
    return redirect(url_for("quiz"))


@app.post("/mark_correct")
def mark_correct():
    state = session.get("state")
    if not state:
        return redirect(url_for(_home_endpoint()))

    last_feedback = state.get("last_feedback") or {}
    if not last_feedback.get("was_wrong"):
        return redirect(url_for("next_question"))

    uid = last_feedback.get("uid", "")
    question_idx = last_feedback.get("question_idx")

    source_path = _resolve_source_from_id(state.get("source_id"))
    with _locked_vocab_update(source_path) as master:
        for v in master:
            if _make_uid(v) != uid:
                continue
            v["falsch"] = max(0, _to_int(v.get("falsch", 0)) - 1)
            v["richtig"] = _to_int(v.get("richtig", 0)) + 1
            break
    _replace_last_performance_result(uid, True)

    wrong = state.get("wrong", [])
    state["wrong"] = [w for w in wrong if w.get("question_idx") != question_idx]
    if state.get("mode") == "kartei":
        kartei = state.get("kartei") or {}
        boxes = {str(k): _to_int(v) for k, v in (kartei.get("boxes") or {}).items() if str(k).strip()}
        wrong_counts = {str(k): _to_int(v) for k, v in (kartei.get("wrong_counts") or {}).items() if str(k).strip()}
        prev_box = min(5, max(1, _to_int(last_feedback.get("kartei_prev_box", boxes.get(uid, 1)))))
        if uid in boxes:
            boxes[uid] = min(5, max(_to_int(boxes.get(uid, 1)), prev_box + 1))
        if uid in wrong_counts:
            wrong_counts[uid] = max(0, _to_int(wrong_counts.get(uid, 0)) - 1)
        kartei["boxes"] = boxes
        kartei["wrong_counts"] = wrong_counts
        state["kartei"] = kartei
    state["last_feedback"] = {"uid": uid, "was_wrong": False, "question_idx": question_idx}
    session["state"] = state

    return redirect(url_for("next_question"))


@app.get("/summary")
def summary():
    state = session.get("state")
    if not state:
        return redirect(url_for(_home_endpoint()))

    mode = state.get("mode", "kartei")
    wrong = state.get("wrong", [])
    total = len(state.get("queue", []))
    wrong_count = len(wrong)
    correct_count = max(0, total - wrong_count)
    kartei_wrong_by_word = []
    if mode == "kartei":
        kartei = state.get("kartei") or {}
        boxes = {str(k): _to_int(v) for k, v in (kartei.get("boxes") or {}).items() if str(k).strip()}
        wrong_counts = {str(k): _to_int(v) for k, v in (kartei.get("wrong_counts") or {}).items() if str(k).strip()}
        total = len(boxes)
        correct_count = sum(1 for box in boxes.values() if min(5, max(1, box)) >= 5)
        wrong_count = sum(max(0, _to_int(v)) for v in wrong_counts.values())
        pool_by_uid = {item.get("uid", ""): item.get("display", {}) for item in state.get("queue", []) if item.get("uid")}
        entries = []
        for uid, cnt in wrong_counts.items():
            if cnt <= 0:
                continue
            display = pool_by_uid.get(uid, {})
            entries.append(
                {
                    "fremdsprache": display.get("fremdsprache", uid),
                    "deutsch": display.get("deutsch", ""),
                    "count": cnt,
                }
            )
        kartei_wrong_by_word = sorted(entries, key=lambda x: (-_to_int(x.get("count", 0)), x.get("fremdsprache", "").lower()))

    return render_template(
        "summary.html",
        mode=mode,
        total=total,
        correct_count=correct_count,
        wrong_count=wrong_count,
        wrong=wrong,
        is_kartei=(mode == "kartei"),
        kartei_wrong_by_word=kartei_wrong_by_word,
    )


@app.post("/reset")
def reset_state():
    session.pop("state", None)
    return redirect(url_for(_home_endpoint()))


@app.post("/back")
def back_to_selection():
    session.pop("state", None)
    return redirect(url_for(_home_endpoint()))


if __name__ == "__main__":
    port = _safe_positive_int(os.getenv("PORT", "8090"), 8090)
    app.run(host="0.0.0.0", port=port, debug=False)
