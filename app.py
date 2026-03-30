import csv
import os
import random
import threading
from pathlib import Path

from flask import Flask, redirect, render_template, request, session, url_for

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-this-in-production")

BASE_DIR = Path(__file__).resolve().parent
VOKABEL_DATEI = BASE_DIR / "vokabeln.csv"
DATA_LOCK = threading.Lock()
FIELDNAMES = ["fremdsprache", "deutsch", "deklination", "lektion", "richtig", "falsch"]


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
    return render_template(
        "index.html",
        lektionen=alle_lektionen(vokabeln),
        total=len(vokabeln),
        error=None if vokabeln else "vokabeln.csv wurde nicht gefunden oder ist leer.",
        status=status,
        message=message,
    )


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

    return render_template(
        "quiz.html",
        mode=mode,
        current=idx + 1,
        total=len(queue),
        vokabel=v,
        uid=item["uid"],
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

    return render_template(
        "feedback.html",
        correct=correct,
        expected=expected,
        user_answer=user_answer,
        mode=mode,
        can_mark_correct=(not correct),
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
