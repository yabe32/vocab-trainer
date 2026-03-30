import csv
import hashlib
import json
import os
import random
import shutil
import threading
import time
from pathlib import Path

from flask import Flask, redirect, render_template, request, session, url_for

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-this-in-production")

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_VOKABEL_DATEI = BASE_DIR / "data" / "vokabeln.csv"
SEED_VOKABEL_DATEI = BASE_DIR / "data" / "vokabeln.seed.csv"
LEGACY_VOKABEL_DATEI = BASE_DIR / "vokabeln.csv"
AUDIO_CACHE_DIR = BASE_DIR / "static" / "audio_cache"
AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
DATA_LOCK = threading.Lock()
TTS_LOCK = threading.Lock()
FIELDNAMES = ["fremdsprache", "deutsch", "deklination", "lektion", "richtig", "falsch"]
TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "alloy")
TTS_DELAY_SECONDS = float(os.getenv("TTS_DELAY_SECONDS", "0.8"))
TTS_MAX_NEW_PER_RUN = int(os.getenv("TTS_MAX_NEW_PER_RUN", "50"))
_OPENAI_CLIENT = None
RUNTIME_SECRETS_FILE = BASE_DIR / "data" / "runtime_secrets.json"
TTS_BUILD_LOCK_FILE = BASE_DIR / "data" / "tts_build.lock"


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
    return rel_path if abs_path.exists() else None


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


def lade_vokabeln_full():
    vokabeln = []
    if not VOKABEL_DATEI.exists():
        return vokabeln

    with VOKABEL_DATEI.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row = {k: (v or "") for k, v in row.items()}
            row["richtig"] = _to_int(row.get("richtig", 0))
            row["falsch"] = _to_int(row.get("falsch", 0))
            vokabeln.append(row)
    return vokabeln


def speichere_vokabeln_full(vokabeln):
    with DATA_LOCK:
        with VOKABEL_DATEI.open("w", newline="", encoding="utf-8") as f:
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


def alle_lektionen(vokabeln):
    return sorted({v.get("lektion", "") for v in vokabeln if v.get("lektion", "")})


def _make_uid(v):
    return f"{v.get('fremdsprache','')}|{v.get('deutsch','')}|{v.get('lektion','')}"


def _apply_scoring(uid, mode, user_answer, master):
    normalized = user_answer.strip().lower()
    for v in master:
        if _make_uid(v) != uid:
            continue

        if mode == "deklination":
            expected = (v.get("deklination") or "").strip().lower()
        else:
            expected = (v.get("deutsch") or "").strip().lower()

        correct = normalized == expected
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


def _build_queue(vokabeln, mode, selected_lektionen, block_size, block_selection, repetitions):
    targets = [v for v in vokabeln if v.get("lektion") in selected_lektionen]

    if mode == "fehler":
        targets = [v for v in targets if _to_int(v.get("falsch", 0)) > 0]
        targets = sorted(targets, key=lambda v: _to_int(v.get("falsch", 0)), reverse=True)

    if mode == "abschreiben":
        return [{"uid": _make_uid(v), "display": v} for v in targets]

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
            for block_idx in selected_block_indices:
                for v in blocks[block_idx]:
                    queue.append({"uid": _make_uid(v), "display": v})
        return queue

    return [{"uid": _make_uid(v), "display": v} for v in targets]


@app.get("/")
def index():
    vokabeln = lade_vokabeln_full()
    status = request.args.get("status")
    message = request.args.get("message")
    has_runtime_key = bool(_load_runtime_secrets().get("OPENAI_API_KEY", "").strip())
    tts_ready = bool(_get_effective_openai_api_key()) and (_get_openai_client() is not None)
    return render_template(
        "index.html",
        lektionen=alle_lektionen(vokabeln),
        total=len(vokabeln),
        error=None if vokabeln else f"{VOKABEL_DATEI} wurde nicht gefunden oder ist leer.",
        status=status,
        message=message,
        tts_ready=tts_ready,
        has_runtime_key=has_runtime_key,
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
    fremdsprache = (request.form.get("fremdsprache") or "").strip()
    deutsch = (request.form.get("deutsch") or "").strip()
    deklination = (request.form.get("deklination") or "").strip()
    lektion = (request.form.get("lektion") or "").strip()

    if not fremdsprache or not deutsch or not lektion:
        return redirect(
            url_for(
                "index",
                status="error",
                message="Bitte Fremdsprache, Deutsch und Lektion ausfuellen.",
            )
        )

    master = lade_vokabeln_full()
    new_uid = _make_uid(
        {"fremdsprache": fremdsprache, "deutsch": deutsch, "lektion": lektion}
    )
    for v in master:
        if _make_uid(v) == new_uid:
            return redirect(
                url_for(
                    "index",
                    status="error",
                    message="Diese Vokabel existiert bereits in der gewaehlten Lektion.",
                )
            )

    master.append(
        {
            "fremdsprache": fremdsprache,
            "deutsch": deutsch,
            "deklination": deklination,
            "lektion": lektion,
            "richtig": 0,
            "falsch": 0,
        }
    )
    speichere_vokabeln_full(master)

    return redirect(
        url_for(
            "index",
            status="ok",
            message=f"Vokabel gespeichert: {fremdsprache} -> {deutsch}",
        )
    )


@app.post("/start")
def start():
    vokabeln = lade_vokabeln_full()
    if not vokabeln:
        return redirect(url_for("index"))

    mode = request.form.get("mode", "kartei")
    selected_lektionen = request.form.getlist("lektionen")
    if not selected_lektionen:
        selected_lektionen = alle_lektionen(vokabeln)

    block_size = _safe_positive_int(request.form.get("block_size", "5"), 5)
    repetitions = _safe_positive_int(request.form.get("repetitions", "1"), 1)
    block_selection = request.form.get("block_selection", "alle").strip().lower() or "alle"

    queue = _build_queue(vokabeln, mode, selected_lektionen, block_size, block_selection, repetitions)
    random.shuffle(queue)

    session["state"] = {
        "mode": mode,
        "selected_lektionen": selected_lektionen,
        "queue": queue,
        "index": 0,
        "wrong": [],
        "last_feedback": None,
    }

    return redirect(url_for("quiz"))


@app.get("/quiz")
def quiz():
    state = session.get("state")
    if not state:
        return redirect(url_for("index"))

    queue = state.get("queue", [])
    idx = state.get("index", 0)

    if idx >= len(queue):
        return redirect(url_for("summary"))

    item = queue[idx]
    v = item["display"]
    mode = state.get("mode", "kartei")
    audio_rel = _cached_audio_rel_path(item["uid"], "lat", v.get("fremdsprache", ""))
    question_audio_url = url_for("static", filename=audio_rel) if audio_rel else None

    return render_template(
        "quiz.html",
        mode=mode,
        current=idx + 1,
        total=len(queue),
        vokabel=v,
        uid=item["uid"],
        question_audio_url=question_audio_url,
    )


@app.post("/answer")
def answer():
    state = session.get("state")
    if not state:
        return redirect(url_for("index"))

    mode = state.get("mode", "kartei")
    queue = state.get("queue", [])
    idx = state.get("index", 0)

    if idx >= len(queue):
        return redirect(url_for("summary"))

    uid = request.form.get("uid", "")
    user_answer = request.form.get("answer", "")

    if mode == "abschreiben":
        state["index"] = idx + 1
        state["last_feedback"] = None
        session["state"] = state
        return redirect(url_for("quiz"))

    master = lade_vokabeln_full()
    correct, expected = _apply_scoring(uid, mode, user_answer, master)
    speichere_vokabeln_full(master)

    if not correct:
        state.setdefault("wrong", []).append(
            {
                "question_idx": idx,
                "frage": queue[idx]["display"].get("fremdsprache", ""),
                "expected": expected,
                "answer": user_answer,
            }
        )

    state["last_feedback"] = {
        "uid": uid,
        "was_wrong": (not correct),
        "question_idx": idx,
    }
    state["index"] = idx + 1
    session["state"] = state

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
    )


@app.get("/next")
def next_question():
    return redirect(url_for("quiz"))


@app.post("/mark_correct")
def mark_correct():
    state = session.get("state")
    if not state:
        return redirect(url_for("index"))

    last_feedback = state.get("last_feedback") or {}
    if not last_feedback.get("was_wrong"):
        return redirect(url_for("next_question"))

    uid = last_feedback.get("uid", "")
    question_idx = last_feedback.get("question_idx")

    master = lade_vokabeln_full()
    for v in master:
        if _make_uid(v) != uid:
            continue
        v["falsch"] = max(0, _to_int(v.get("falsch", 0)) - 1)
        v["richtig"] = _to_int(v.get("richtig", 0)) + 1
        break
    speichere_vokabeln_full(master)

    wrong = state.get("wrong", [])
    state["wrong"] = [w for w in wrong if w.get("question_idx") != question_idx]
    state["last_feedback"] = {"uid": uid, "was_wrong": False, "question_idx": question_idx}
    session["state"] = state

    return redirect(url_for("next_question"))


@app.get("/summary")
def summary():
    state = session.get("state")
    if not state:
        return redirect(url_for("index"))

    wrong = state.get("wrong", [])
    total = len(state.get("queue", []))
    wrong_count = len(wrong)
    correct_count = max(0, total - wrong_count)

    return render_template(
        "summary.html",
        mode=state.get("mode", "kartei"),
        total=total,
        correct_count=correct_count,
        wrong=wrong,
    )


@app.post("/reset")
def reset_state():
    session.pop("state", None)
    return redirect(url_for("index"))


@app.post("/back")
def back_to_selection():
    session.pop("state", None)
    return redirect(url_for("index"))


if __name__ == "__main__":
    port = _safe_positive_int(os.getenv("PORT", "8090"), 8090)
    app.run(host="0.0.0.0", port=port, debug=True)
