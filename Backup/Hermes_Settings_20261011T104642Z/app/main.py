"""FastAPI-App: Web-GUI + OAuth-Callback + Steuerung."""
from __future__ import annotations

import os
import re as _re
import secrets
import subprocess
import threading
import time
from datetime import datetime
from urllib.parse import quote as _q

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.concurrency import run_in_threadpool

from .preview import enabled as preview_enabled, install_network_guard, PreviewMiddleware, PreviewNetworkBlocked, MESSAGE, status as preview_status
install_network_guard()

from . import csv_import, db, default_docs, deployment, fanvue, memory, openrouter, poller

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

app = FastAPI(title="MP CreatorStudio")
app.add_middleware(PreviewMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SECRET_KEY", "dev-secret-change-me"),
)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


# ---------------------------------------------------------- Jinja-Hilfsfilter
def _fmt_ts(ts):
    if not ts:
        return "–"
    return datetime.fromtimestamp(float(ts)).strftime("%d.%m.%Y %H:%M:%S")


def _fmt_isodate(s):
    if not s:
        return "–"
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        return str(s)[:10]


def _fmt_message_time(value):
    from datetime import timezone
    from zoneinfo import ZoneInfo
    if not value:
        return "Zeitpunkt nicht verfügbar"
    try:
        stamp = (datetime.fromtimestamp(float(value), timezone.utc)
                 if isinstance(value, (int, float)) else
                 datetime.fromisoformat(str(value).replace("Z", "+00:00")))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(ZoneInfo("Europe/Berlin")).strftime("%d.%m.%Y · %H:%M %Z")
    except (ValueError, TypeError, OverflowError):
        return "Zeitpunkt nicht verfügbar"


templates.env.filters["message_time"] = _fmt_message_time
templates.env.filters["ts"] = _fmt_ts
templates.env.filters["fdate"] = _fmt_isodate


@app.on_event("startup")
def _startup() -> None:
    from tools.backup import backup
    backup(os.path.dirname(db.DATA_DIR))
    db.init_db()
    # Worker starts after the AutoPost has initialized successfully.


# --------------------------------------------------------------------- Kontext
def _static_version() -> str:
    """Juengster Zeitstempel im static-Ordner als Cache-Buster.

    Ohne das liefert der Browser nach einem Deploy weiter die ALTEN statischen
    Dateien zum neuen HTML aus - das Layout bricht dann auf schwer
    nachvollziehbare Weise. Bewusst ueber den ganzen Ordner, damit auch ein
    getauschtes Logo eine neue URL bekommt, nicht nur geaendertes CSS.
    """
    static_dir = os.path.join(BASE_DIR, "static")
    try:
        newest = max(os.path.getmtime(os.path.join(static_dir, f))
                     for f in os.listdir(static_dir) if not f.startswith("."))
        return str(int(newest))
    except (OSError, ValueError):
        return "0"


def _base_ctx(request: Request) -> dict:
    return {
        "request": request,
        "workspace_area": ("settings" if request.url.path == "/system" or
                           request.url.path.startswith(("/settings/shared", "/upload")) else "autochat"),
        "css_v": _static_version(),
        "connected": fanvue.is_connected(),
        "running": db.get_setting("bot_running", False) and not preview_enabled() and not deployment.pending(),
        "deployment_pending": deployment.pending(),
        "preview": preview_enabled(),
        "mode": "Mitlesen / Vorschläge" if preview_enabled() else db.get_setting("mode", "approval"),
        "pending_count": db.count_drafts("pending"),
        "tokens": db.get_tokens(),
        # Ohne eigene Konto-Kennung kann der Bot eigene Nachrichten nicht von
        # Fan-Nachrichten unterscheiden - das verfaelscht jeden Prompt.
        "account_missing": fanvue.is_connected() and not fanvue.account_uuid(),
        "poller_status": preview_status() if preview_enabled() else poller.status(),
    }


# ------------------------------------------------------------------ Dashboard
def _display(name_map, uuid):
    handle, display = name_map.get(uuid, ("", ""))
    return {"uuid": uuid, "handle": handle, "display_name": display or handle or uuid[:8]}


# ----------------------------------------------------------------- Upload
@app.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request, msg: str = "", err: str = ""):
    from . import publisher
    ctx = _base_ctx(request)
    ctx["s"] = db.all_settings()
    ctx["st"] = publisher.status()
    ctx["problems"] = publisher.guard() if not ctx["st"].get("error") else []
    ctx["msg"] = msg
    ctx["err"] = err
    request.session.setdefault('upload_csrf', secrets.token_urlsafe(32))
    ctx['upload_csrf'] = request.session['upload_csrf']
    return templates.TemplateResponse("upload.html", ctx)


@app.get("/api/upload-status")
def api_upload_status():
    from . import publisher
    st = publisher.status()
    st["problems"] = publisher.guard() if not st.get("error") else []
    return st


@app.post("/upload/settings")
async def upload_settings(request: Request):
    form = await request.form()
    _check_upload_csrf(request, form)
    for key in ("git_remote_url", "git_branch", "git_user_name", "git_user_email",
                "git_commit_default"):
        if key in form:
            db.set_setting(key, str(form[key]).strip())
    # Leeres Token-Feld bedeutet "unverändert lassen", nicht "löschen" -
    # sonst wuerde ein versehentlicher Speichern-Klick den Token entfernen.
    token = str(form.get("github_token", "")).strip()
    if token:
        db.set_setting("github_token", token)
    elif str(form.get("clear_token", "")) == "1":
        db.set_setting("github_token", "")
    db.log("info", "upload", "Upload-Einstellungen gespeichert")
    return RedirectResponse(f"/upload?msg={_q('Einstellungen gespeichert')}", status_code=303)


@app.post("/upload/publish")
async def upload_publish(request: Request):
    from . import publisher
    form = await request.form()
    _check_upload_csrf(request, form)
    message = str(form.get("message", "")).strip() or db.get_setting("git_commit_default", "Aktueller Stand")
    tag = str(form.get("tag", "")).strip()
    do_push = str(form.get("push", "1")) == "1"
    res = await run_in_threadpool(publisher.publish, message, tag=tag, do_push=do_push)
    if res.get("ok"):
        return RedirectResponse(f"/upload?msg={_q(' · '.join(res['log']))}", status_code=303)
    detail = res.get("error", "Unbekannter Fehler")
    if res.get("problems"):
        detail += " — " + " ".join(res["problems"])
    return RedirectResponse(f"/upload?err={_q(detail[:400])}", status_code=303)


def _check_upload_csrf(request, form):
    expected = request.session.get('upload_csrf', '')
    supplied = str(form.get('upload_csrf', ''))
    if not expected or not secrets.compare_digest(expected, supplied):
        raise HTTPException(403, 'Bitte die Upload-Seite neu öffnen und erneut versuchen.')


@app.get("/api/update-check")
def api_update_check(force: bool = False):
    """Aktueller Update-Zustand. force=1 fragt das Repository neu ab."""
    from . import updater
    state = updater.check(fetch=True) if force else updater.cached_state()
    state.setdefault("current", updater.current_version()["version"])
    return state


@app.get("/system", response_class=HTMLResponse)
def system_page(request: Request, sys: str = "", sys_err: str = ""):
    ctx = _base_ctx(request)
    ctx.update(sys=sys, sys_err=sys_err)
    return templates.TemplateResponse("system.html", ctx)


@app.post("/system/update")
def system_update():
    """Installiert die neueste markierte Version und startet den Dienst neu."""
    from urllib.parse import quote as _q
    from . import updater
    ok, msg = updater.install()
    if ok:
        return RedirectResponse("/system?sys=update", status_code=303)
    return RedirectResponse(f"/system?sys_err={_q(msg[:200])}", status_code=303)


@app.get("/api/sysinfo")
def api_sysinfo():
    """Server-Auslastung als JSON – die System-Seite aktualisiert damit ohne Reload."""
    from . import sysinfo
    snap = sysinfo.snapshot(data_path=db.DATA_DIR, db_path=db.DB_PATH)
    snap["mem_used_h"] = sysinfo.human_bytes(snap.get("mem_used"))
    snap["mem_total_h"] = sysinfo.human_bytes(snap.get("mem_total"))
    snap["disk_used_h"] = sysinfo.human_bytes(snap.get("disk_used"))
    snap["disk_total_h"] = sysinfo.human_bytes(snap.get("disk_total"))
    snap["proc_rss_h"] = sysinfo.human_bytes(snap.get("proc_rss"))
    snap["db_bytes_h"] = sysinfo.human_bytes(snap.get("db_bytes"))
    snap["uptime_h"] = sysinfo.human_uptime(snap.get("uptime"))
    return snap


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, sys: str = "", sys_err: str = ""):
    # Links aus älteren Versionen weiterhin zum Ergebnis der System-Aktion führen.
    if sys or sys_err:
        return RedirectResponse(f"/system?sys={_q(sys)}&sys_err={_q(sys_err)}", status_code=303)
    ctx = _base_ctx(request)
    ctx["logs"] = db.list_logs(limit=15)
    ctx["sent_count"] = db.count_drafts("sent")
    ctx["failed_count"] = db.count_drafts("failed")

    # Zeitraeume (lokale Mitternacht)
    now = datetime.now()
    today = datetime(now.year, now.month, now.day).timestamp()
    tomorrow = today + 86400
    yesterday = today - 86400
    name_map = db.chat_name_map()

    # Gesamt heute / gestern
    ctx["today"] = {
        "messages": db.sent_message_count(today, tomorrow),
        "offers": db.offers_sent_count(today, tomorrow),
        "purchases": db.purchases_count(today, tomorrow),
        "revenue": db.revenue_between(today, tomorrow),
        "api_cost": db.api_cost_between(today, tomorrow),
    }
    ctx["yesterday"] = {
        "messages": db.sent_message_count(yesterday, today),
        "offers": db.offers_sent_count(yesterday, today),
        "purchases": db.purchases_count(yesterday, today),
        "revenue": db.revenue_between(yesterday, today),
        "api_cost": db.api_cost_between(yesterday, today),
    }

    # Top-Subscriber heute (nach Kaeufen, dann Angeboten, dann Nachrichten)
    act = db.activity_by_user(today, tomorrow)
    top = sorted(act.items(),
                 key=lambda kv: (kv[1]["purchases"], kv[1]["offers"], kv[1]["messages"]),
                 reverse=True)[:10]
    ctx["top_today"] = [{**_display(name_map, u), **a} for u, a in top if
                        (a["messages"] or a["offers"] or a["purchases"])]

    # PPV-Kontingent pro Fan (wie viele aktive Sets noch anbietbar)
    enabled_names = {f["name"] for f in db.enabled_ppv_folders()}
    total_enabled = len(enabled_names)
    folders = db.offer_folders_by_user(
        float(db.get_setting("ppv_offer_reset_days", 0) or 0))
    low, exhausted = [], []
    for uuid, sets in folders.items():
        offered_enabled = sets["offered"] & enabled_names
        remaining = total_enabled - len(offered_enabled)
        purchased = len(sets["purchased"])
        entry = {**_display(name_map, uuid), "remaining": remaining, "purchased": purchased,
                 "offered": len(sets["offered"])}
        if remaining <= 0:
            exhausted.append(entry)
        elif remaining < 3:
            low.append(entry)
    low.sort(key=lambda e: (e["remaining"], -e["purchased"]))
    exhausted.sort(key=lambda e: -e["purchased"])
    ctx["low_quota"] = low
    ctx["exhausted_quota"] = exhausted
    ctx["total_enabled"] = total_enabled
    return templates.TemplateResponse("dashboard.html", ctx)


# ------------------------------------------------------------- Test-Modus
def _run_test(message: str, notes: str, img_analysis: Optional[dict] = None) -> dict:
    """Trockenlauf der kompletten Bot-Pipeline OHNE zu senden oder State zu aendern.
    img_analysis: Ergebnis von openrouter.analyze_incoming_image fuer ein hochgeladenes
    Testbild (oder None)."""
    import re as _r
    from . import guardrails, ppv_engine
    res: dict = {"message": message, "notes": notes, "classifier": {}, "decision": {},
                 "reply": "", "ppv": None, "diagnostics": [], "image": img_analysis}
    system_prompt = db.get_setting("system_prompt", "")
    ppv_on = db.get_setting("ppv_enabled", False)
    has_photo = img_analysis is not None
    explicit_image = bool(img_analysis and (img_analysis.get("nude") or img_analysis.get("penis")))
    classifier = {}
    if ppv_on and db.get_setting("ppv_use_llm_classifier", True):
        classifier = openrouter.classify_message(message, notes)
    res["classifier"] = classifier
    hist = [{"text": message, "sender": {"uuid": "fan"}}]

    # Bild-Instruktionen fuer die normale Antwort (wie im Live-Betrieb), falls kein PPV
    def _image_prompt_suffix() -> str:
        if not img_analysis:
            return ""
        desc = img_analysis.get("description", "") or "ein Bild"
        if img_analysis.get("woman") and not explicit_image:
            return "\n\n" + db.get_setting("incoming_image_woman_prompt", "")
        return "\n\n" + db.get_setting("incoming_image_react_prompt", "").replace(
            "{beschreibung}", desc)

    if ppv_on:
        # Kein Cooldown/Aufwaermlimit im Test
        state = {"last_ppv_at": None, "outbound_since_ppv": 99, "unpurchased_streak": 0,
                 "sexual_streak": 0}
        decision = ppv_engine.evaluate(state, message, has_photo, classifier, time.time(),
                                       fan_msg_count=99, explicit_image=explicit_image)
        res["decision"] = decision
        prefs = decision.get("preferences", [])
        # Diagnose je aktivem Set
        wanted_kind = decision.get("wanted_kind", "")
        for f in db.enabled_ppv_folders():
            all_tags = [t.strip() for t in (f["tags"] or "").split(",") if t.strip()]
            all_tags += db.folder_all_media_tags(f["name"])
            score = ppv_engine._score_folder(", ".join(all_tags), prefs)
            ro = bool(f["request_only"])
            kind = ppv_engine._row_media_kind(f)
            res["diagnostics"].append({
                "name": f["name"], "request_only": ro, "score": score,
                "media_kind": kind,
                "eligible": ((not ro) or decision.get("request_unlock", False))
                            and ppv_engine.kind_matches(kind, wanted_kind),
            })
        res["diagnostics"].sort(key=lambda d: (-d["score"], d["name"]))

        if decision["send"]:
            folder = ppv_engine.select_set("__TEST__", prefs,
                                           allow_request_only=decision.get("request_unlock", False),
                                           wanted_kind=wanted_kind)
            if folder and db.get_setting("ppv_confirm_enabled", True):
                pruef = openrouter.confirm_ppv(hist, "me", decision.get("reason", ""),
                                               set_name=folder["name"],
                                               price_cents=folder.get("price_cents", 0))
                res["confirm"] = pruef
                if not pruef["ok"]:
                    folder = None
            if folder:
                payload = ppv_engine.build_payload(folder)
                sales = db.get_setting("ppv_sales_prompt", "")
                ctx_tags = (folder.get("tags") or "") or "exklusiver Content"
                sys = (f"{system_prompt}\n\n{sales}\n\n"
                       f"WICHTIG – SPRACHE: Antworte in DERSELBEN Sprache, die der Fan verwendet. "
                       f"Wechsle NIEMALS die Sprache, auch nicht wegen dieser deutschen Anweisung. "
                       f"Der Content ist bereits als Anhang dabei. "
                       f"Inhalt/Stimmung: {ctx_tags}. Schreibe NUR eine kurze Anbahnungs-Nachricht, "
                       f"ohne Preis, ohne das Wort 'Set'.")
                txt = poller._generate(sys, hist, "me", notes, [], task="caption", retry_delay=0)
                txt = _r.sub(r"(?i)\bsets?\b", "PPV", txt)
                txt, _ = guardrails.check_outgoing(txt, has_media=True)
                res["ppv"] = {"folder": folder["name"],
                              "price_cents": folder["price_cents"],
                              "media": len(payload["media_uuids"]) if payload else 0,
                              "text": txt}
            else:
                res["ppv"] = {"folder": None}
        else:
            sys = system_prompt
            if db.get_setting("anti_ai_enabled", True):
                sys += "\n\n" + db.get_setting("anti_ai_rules", "")
            if decision.get("free_request"):
                sys += "\n\n" + db.get_setting("ppv_freecontent_prompt", "")
            sys += _image_prompt_suffix()
            txt = poller._generate(sys, hist, "me", notes, [], retry_delay=0)
            txt, _ = guardrails.check_outgoing(txt)
            res["reply"] = txt
    else:
        sys = system_prompt
        if db.get_setting("anti_ai_enabled", True):
            sys += "\n\n" + db.get_setting("anti_ai_rules", "")
        sys += _image_prompt_suffix()
        txt = poller._generate(sys, hist, "me", notes, [], retry_delay=0)
        txt, _ = guardrails.check_outgoing(txt)
        res["reply"] = txt
    return res


@app.get("/test", response_class=HTMLResponse)
def test_page(request: Request):
    ctx = _base_ctx(request)
    ctx["result"] = None
    ctx["message"] = ""
    ctx["notes"] = ""
    return templates.TemplateResponse("test.html", ctx)


@app.post("/test", response_class=HTMLResponse)
async def test_run(request: Request):
    import base64 as _b64
    form = await request.form()
    message = (form.get("message", "") or "").strip()
    notes = (form.get("notes", "") or "").strip()
    # Optionales Testbild -> mit dem echten Vision-LLM analysieren
    img_analysis = None
    upload = form.get("photo")
    if upload is not None and getattr(upload, "filename", ""):
        data = await upload.read()
        if data:
            ctype = getattr(upload, "content_type", "") or "image/jpeg"
            data_url = f"data:{ctype};base64,{_b64.b64encode(data).decode('ascii')}"
            img_analysis = openrouter.analyze_incoming_image(data_url)
    ctx = _base_ctx(request)
    ctx["message"] = message
    ctx["notes"] = notes
    ctx["error"] = None
    # Test laeuft, sobald Text ODER Bild vorhanden ist
    try:
        ctx["result"] = _run_test(message, notes, img_analysis) if (message or img_analysis) else None
    except Exception as exc:  # noqa: BLE001 – Fehler sichtbar machen statt 500
        ctx["result"] = None
        ctx["error"] = str(exc)
        db.log("error", "test", "Testlauf fehlgeschlagen", str(exc))
    return templates.TemplateResponse("test.html", ctx)


# ------------------------------------------------------------- Chat-Export
EXPORT_DIR = os.path.join(os.path.dirname(BASE_DIR), "exports")


def _safe_filename(s: str) -> str:
    s = _re.sub(r"[^a-zA-Z0-9_.-]+", "_", s or "").strip("_")
    return s or "chat"


def _export_lines_from_api(msgs: list, me_uuid: str) -> list[str]:
    zeilen = []
    for m in msgs:
        sender = (m.get("sender") or {}).get("uuid", "")
        who = "Creator" if sender == me_uuid else "Fan"
        ts = str(m.get("sentAt") or "")[:16].replace("T", " ")
        text = (m.get("text") or "").replace("\n", " ").strip()
        tags = []
        if m.get("pricing"):
            try:
                price = (m.get("pricing") or {}).get("USD", {}).get("price")
                tags.append(f"PPV ${price/100:.2f}" if price else "PPV")
            except (TypeError, ValueError):
                tags.append("PPV")
            tags.append("gekauft" if m.get("purchasedAt") else "nicht gekauft")
        if m.get("hasMedia"):
            tags.append(str(m.get("mediaType") or "media"))
        tagstr = f" [{', '.join(tags)}]" if tags else ""
        zeilen.append(f"- {ts} **{who}**{tagstr}: {text}")
    return zeilen


def _export_lines_from_db(user_uuid: str) -> list[str]:
    """Ausweichquelle: die lokal mitgeschriebenen Nachrichten."""
    zeilen = []
    for r in db.messages_chronological(user_uuid):
        who = "Fan" if r["direction"] == "in" else "Creator"
        stamp = r["sent_at"] or r["seen_at"]
        ts = datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M") if stamp else ""
        tags = []
        # "SINGLE_RECIPIENT" steht an JEDER normalen Nachricht und macht den
        # Export nur unleserlich - interessant sind die Ausnahmen (z.B. TIP).
        if r["msg_type"] and str(r["msg_type"]).upper() not in ("SINGLE_RECIPIENT", "MESSAGE"):
            tags.append(str(r["msg_type"]))
        if r["has_media"]:
            tags.append("media")
        tagstr = f" [{', '.join(tags)}]" if tags else ""
        text = (r["text"] or "").replace("\n", " ").strip()
        zeilen.append(f"- {ts} **{who}**{tagstr}: {text}")
    return zeilen


def _write_chat_export(user_uuid: str, handle: str, display: str, me_uuid: str) -> tuple[str, int]:
    """Holt den Chatverlauf und schreibt ihn als Markdown nach exports/. Rueckgabe: (Pfad, Anzahl).

    Erst ueber die Fanvue-API (vollstaendig), bei einem Ausfall aus der lokalen
    messages-Tabelle. Fanvue antwortet zeitweise mit 503; ein Export, der dann
    gar nichts liefert, ist unnoetig - die Nachrichten liegen ja groesstenteils
    schon hier. Die Quelle steht im Kopf der Datei, damit niemand einen
    unvollstaendigen Auszug fuer den ganzen Verlauf haelt.
    """
    os.makedirs(EXPORT_DIR, exist_ok=True)
    quelle = "Fanvue-API (vollständiger Verlauf)"
    try:
        msgs = fanvue.full_message_history(user_uuid)
        koerper = _export_lines_from_api(msgs, me_uuid)
    except Exception as exc:  # noqa: BLE001 - Ausweichquelle statt Abbruch
        koerper = _export_lines_from_db(user_uuid)
        if not koerper:
            raise
        quelle = (f"lokal mitgeschrieben – Fanvue war nicht erreichbar ({str(exc)[:120]}). "
                  f"Kann unvollständig sein.")
        db.log("warn", "system",
               f"Chat-Export aus lokalem Verlauf ({handle or user_uuid})", str(exc)[:200])
    lines = [f"# Chat mit {display or handle or user_uuid} (@{handle})",
             f"UUID: {user_uuid}",
             f"Quelle: {quelle}",
             f"Exportiert: {datetime.now().strftime('%d.%m.%Y %H:%M')} · "
             f"{len(koerper)} Nachrichten", ""] + koerper
    path = os.path.join(EXPORT_DIR, _safe_filename(handle or user_uuid) + ".md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path, len(koerper)


@app.post("/chats/{user_uuid}/export")
def chat_export(user_uuid: str, back: str = Form("")):
    """Einen einzelnen Chatverlauf nach exports/ schreiben.

    `back` gesetzt = der Knopf kam von der Subs-Liste, dann geht es auch dorthin
    zurueck. Ohne `back` bleibt es beim alten Weg ueber die PPV-Unterseite.
    """
    from urllib.parse import quote as _q
    me_uuid = fanvue.account_uuid() if fanvue.is_connected() else ""
    chat = db.get_chat(user_uuid)
    try:
        path, n = _write_chat_export(user_uuid, chat["handle"] if chat else "",
                                     chat["display_name"] if chat else "", me_uuid)
        db.log("info", "system", f"Chat exportiert: {os.path.basename(path)} ({n} Nachrichten)")
        if back:
            return _chats_back(back, f"Chat exportiert: exports/{os.path.basename(path)} "
                                     f"({n} Nachrichten)")
        return RedirectResponse(f"/chats/{user_uuid}/ppv?exported={n}", status_code=303)
    except Exception as exc:  # noqa: BLE001
        db.log("error", "system", "Chat-Export fehlgeschlagen", str(exc))
        if back:
            return _chats_back(back, f"Export fehlgeschlagen: {str(exc)[:150]}")
        return RedirectResponse(f"/chats/{user_uuid}/ppv?err={_q(str(exc)[:200])}", status_code=303)


_export_state: dict = {"running": False, "done": 0, "total": 0, "error": None}


def export_status():
    return dict(_export_state)


def _export_group_worker(members: list, me_uuid: str):
    try:
        _export_state.update(running=True, done=0, total=len(members), error=None)
        for m in members:
            try:
                _write_chat_export(m["uuid"], m.get("handle", ""), m.get("display_name", ""), me_uuid)
            except Exception:  # noqa: BLE001
                pass
            _export_state["done"] += 1
            time.sleep(0.2)
        db.log("info", "system", f"Gruppen-Chat-Export fertig: {_export_state['done']} Chats")
    except Exception as exc:  # noqa: BLE001
        _export_state["error"] = str(exc)
    finally:
        _export_state["running"] = False


@app.post("/chats/export-all")
def chat_export_all():
    if not fanvue.is_connected() or _export_state["running"]:
        return RedirectResponse("/chats?exportstarted=0", status_code=303)
    custom_list = db.get_setting("chat_custom_list_id", "")
    try:
        if custom_list:
            members = _fetch_list_members(custom_list)
        else:
            members = _fetch_group_members(filter_=db.get_setting("chat_filter", "unread"))
    except Exception:  # noqa: BLE001
        members = _fetch_group_members(custom_list_id=custom_list)
    me_uuid = fanvue.account_uuid()
    threading.Thread(target=_export_group_worker, args=(members, me_uuid),
                     name="chat-export", daemon=True).start()
    return RedirectResponse("/chats?exportstarted=1", status_code=303)


@app.get("/chats/export-status")
def chat_export_status():
    return export_status()


@app.get("/reports", response_class=HTMLResponse)
def reports(request: Request, inactive_days: int = 7):
    ctx = _base_ctx(request)
    name_map = db.chat_name_map()

    # 7-Tage-Trend (heute + 6 Tage zurueck)
    now = datetime.now()
    today = datetime(now.year, now.month, now.day).timestamp()
    trend = []
    for i in range(6, -1, -1):
        start = today - i * 86400
        end = start + 86400
        trend.append({
            "date": datetime.fromtimestamp(start).strftime("%d.%m."),
            "messages": db.sent_message_count(start, end),
            "offers": db.offers_sent_count(start, end),
            "purchases": db.purchases_count(start, end),
            "revenue_cents": db.revenue_between(start, end),
        })
    ctx["trend"] = trend
    ctx["trend_max_rev"] = max([t["revenue_cents"] for t in trend] + [1])

    # Conversion pro Set / Top-Sets / nie gekauft
    conv = db.set_conversion()
    ctx["by_revenue"] = sorted(conv, key=lambda c: -c["revenue_cents"])[:15]
    ctx["by_conversion"] = sorted([c for c in conv if c["offered"] >= 3],
                                  key=lambda c: -c["conversion"])[:15]
    ctx["never_bought"] = sorted([c for c in conv if c["purchased"] == 0],
                                 key=lambda c: -c["offered"])[:20]

    # Nicht-Kaeufer (>=3 Angebote, 0 Kaeufe)
    ctx["non_buyers"] = [{**_display(name_map, n["user_uuid"]), "offered": n["offered"]}
                         for n in db.non_buyers(3)][:20]

    # KPIs
    ctx["kpi"] = db.revenue_kpis()

    # Inaktive Zahler (Kaeufer, die seit X Tagen nichts geschrieben haben)
    last_in = db.last_inbound_map()
    cutoff = time.time() - inactive_days * 86400
    inactive = []
    for b in db.buyers_with_spend():
        li = last_in.get(b["user_uuid"])
        if li is not None and li < cutoff:
            inactive.append({**_display(name_map, b["user_uuid"]),
                             "revenue_cents": b["revenue_cents"], "purchases": b["purchases"],
                             "last_inbound_at": li})
    inactive.sort(key=lambda e: -e["revenue_cents"])
    ctx["inactive_payers"] = inactive[:25]
    ctx["inactive_days"] = inactive_days
    return templates.TemplateResponse("reports.html", ctx)


@app.post("/toggle-running")
def toggle_running(request: Request):
    new = not db.get_setting("bot_running", False)
    db.set_setting("bot_running", new)
    db.log("info", "system", f"Bot {'gestartet' if new else 'pausiert'} (Master-Schalter)")
    return RedirectResponse("/", status_code=303)


# ------------------------------------------------------------------- Doku
def _render_markdown(md: str) -> str:
    """Kompakter, abhängigkeitsfreier Markdown->HTML-Renderer für die Doku."""
    import html as _h
    import re as _re

    def inline(s: str) -> str:
        s = _h.escape(s)
        s = _re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        s = _re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
        s = _re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
                    r'<a href="\2" target="_blank" rel="noopener">\1</a>', s)
        return s

    out: list[str] = []
    para: list[str] = []
    list_type = None
    in_code = False
    code_buf: list[str] = []

    def flush_para():
        nonlocal para
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>")
            para = []

    def close_list():
        nonlocal list_type
        if list_type:
            out.append(f"</{list_type}>")
            list_type = None

    for raw in (md or "").replace("\r\n", "\n").split("\n"):
        if raw.strip().startswith("```"):
            if in_code:
                out.append("<pre><code>" + _h.escape("\n".join(code_buf)) + "</code></pre>")
                code_buf, in_code = [], False
            else:
                flush_para(); close_list(); in_code = True
            continue
        if in_code:
            code_buf.append(raw); continue
        s = raw.strip()
        if not s:
            flush_para(); close_list(); continue
        m = _re.match(r"^(#{1,4})\s+(.*)$", s)
        if m:
            flush_para(); close_list()
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>" + inline(m.group(2)) + f"</h{lvl}>")
            continue
        m = _re.match(r"^[-*]\s+(.*)$", s)
        if m:
            flush_para()
            if list_type != "ul":
                close_list(); out.append("<ul>"); list_type = "ul"
            out.append("<li>" + inline(m.group(1)) + "</li>")
            continue
        m = _re.match(r"^\d+\.\s+(.*)$", s)
        if m:
            flush_para()
            if list_type != "ol":
                close_list(); out.append("<ol>"); list_type = "ol"
            out.append("<li>" + inline(m.group(1)) + "</li>")
            continue
        if list_type:
            close_list()
        para.append(s)
    if in_code:
        out.append("<pre><code>" + _h.escape("\n".join(code_buf)) + "</code></pre>")
    flush_para(); close_list()
    return "\n".join(out)


def _doku_markdown() -> str:
    return db.get_setting("doku_markdown", "") or default_docs.DEFAULT_DOKU


@app.get("/doku", response_class=HTMLResponse)
def doku_page(request: Request, saved: str = ""):
    ctx = _base_ctx(request)
    ctx["doku_html"] = _render_markdown(_doku_markdown())
    ctx["saved"] = saved
    return templates.TemplateResponse("doku.html", ctx)


@app.get("/doku/edit", response_class=HTMLResponse)
def doku_edit(request: Request):
    ctx = _base_ctx(request)
    ctx["doku_markdown"] = _doku_markdown()
    return templates.TemplateResponse("doku_edit.html", ctx)


@app.post("/doku/edit")
async def doku_save(request: Request):
    form = await request.form()
    db.set_setting("doku_markdown", form.get("doku_markdown", ""))
    db.log("info", "system", "Doku bearbeitet")
    return RedirectResponse("/doku?saved=1", status_code=303)


@app.post("/doku/reset")
def doku_reset():
    db.set_setting("doku_markdown", "")
    db.log("info", "system", "Doku auf Standard zurückgesetzt")
    return RedirectResponse("/doku?saved=1", status_code=303)


# ------------------------------------------------------------- System-Steuerung
def _run_admin(action: str) -> None:
    """Ruft das Root-Helferskript per sudo auf (eng begrenzt in sudoers)."""
    subprocess.run(["sudo", "-n", "/usr/local/bin/fanvue-admin", action],
                   check=True, capture_output=True, timeout=10)


@app.post("/system/restart-service")
def system_restart():
    from urllib.parse import quote as _q
    try:
        _run_admin("restart-service")
        db.log("info", "system", "Dienst-Neustart über GUI ausgelöst")
        return RedirectResponse("/system?sys=restart", status_code=303)
    except Exception as exc:  # noqa: BLE001
        detail = getattr(exc, "stderr", b"")
        detail = detail.decode(errors="replace") if isinstance(detail, bytes) else str(exc)
        db.log("error", "system", "Dienst-Neustart fehlgeschlagen", detail or str(exc))
        return RedirectResponse(f"/system?sys_err={_q((detail or str(exc))[:200])}", status_code=303)


@app.post("/system/reboot")
def system_reboot():
    from urllib.parse import quote as _q
    try:
        _run_admin("reboot")
        db.log("warn", "system", "Server-Neustart (reboot) über GUI ausgelöst")
        return RedirectResponse("/system?sys=reboot", status_code=303)
    except Exception as exc:  # noqa: BLE001
        detail = getattr(exc, "stderr", b"")
        detail = detail.decode(errors="replace") if isinstance(detail, bytes) else str(exc)
        db.log("error", "system", "Server-Neustart fehlgeschlagen", detail or str(exc))
        return RedirectResponse(f"/system?sys_err={_q((detail or str(exc))[:200])}", status_code=303)


# ------------------------------------------------------------- Freigabe-Queue
_draft_context_cache = {}


def _draft_context(user_uuid: str, inbound_uuid: str, n: int, me_uuid: str,
                   *, allow_remote: bool = True) -> tuple[list[dict], object]:
    """Anchor by message UUID, never by repeated text or draft creation time."""
    rows = [dict(r) for r in db.messages_tail_chronological(user_uuid, 500)]
    if not any(r["msg_uuid"] == inbound_uuid for r in rows) and allow_remote:
        try:
            messages = list(reversed(fanvue.list_messages(user_uuid, size=50, mark_as_read=False).get("data", [])))
            rows = [{"msg_uuid": m.get("uuid"), "direction": "out" if
                     (m.get("sender") or {}).get("uuid") == me_uuid else "in",
                     "text": m.get("text") or "", "has_media": m.get("hasMedia"),
                     "sent_at": memory._msg_epoch(m)} for m in messages]
        except Exception:
            pass
    anchor = next((i for i, r in enumerate(rows) if r["msg_uuid"] == inbound_uuid), None)
    if anchor is None:
        return _draft_context_cache.get((user_uuid, inbound_uuid, n, me_uuid), ([], None))
    context = rows[max(0, anchor - max(0, n)):anchor]
    result = [{"who": "Ich" if r["direction"] == "out" else "Fan", "text": r["text"],
             "media": r["has_media"], "sent_at": r["sent_at"]} for r in context], rows[anchor]["sent_at"]
    if len(_draft_context_cache) >= 512:
        _draft_context_cache.pop(next(iter(_draft_context_cache)))
    _draft_context_cache[(user_uuid, inbound_uuid, n, me_uuid)] = result
    return result


@app.get("/queue", response_class=HTMLResponse)
def queue(request: Request, msg: str = ""):
    ctx = _base_ctx(request)
    ctx["msg"] = msg
    drafts = db.list_drafts(status="pending", limit=100)
    ctx["drafts"] = drafts
    ctx["max_regen"] = int(db.get_setting("draft_max_regen", 10))
    n = int(db.get_setting("queue_context_messages", 2))
    me_uuid = fanvue.account_uuid()
    contexts, incoming_times = {}, {}
    for d in drafts:
        contexts[d["id"]], incoming_times[d["id"]] = _draft_context(
            d["user_uuid"], d["inbound_uuid"], n, me_uuid,
            allow_remote=not request.headers.get("X-CreatorStudio-Refresh") and fanvue.is_connected())
    ctx.update(contexts=contexts, incoming_times=incoming_times)
    return templates.TemplateResponse("queue.html", ctx)


def _mark_edited(draft_id: int, edited_text: str) -> None:
    """Uebernimmt den Text und merkt sich, ob der Mensch ihn veraendert hat.
    Ein als bearbeitet markierter Draft wird nie automatisch ueberschrieben."""
    new = (edited_text or "").strip()
    if not new:
        return
    draft = db.get_draft(draft_id)
    was = (draft["edited_text"] or draft["generated_text"] or "").strip() if draft else ""
    db.update_draft(draft_id, edited_text=new,
                    **({"user_edited": 1} if new != was else {}))


@app.post("/queue/{draft_id}/approve")
def approve_draft(draft_id: int, edited_text: str = Form("")):
    _mark_edited(draft_id, edited_text)
    ok = poller.send_draft_now(draft_id)
    msg = ""
    if not ok:
        d = db.get_draft(draft_id)
        # Haeufigster Fall: der Fan hat zwischenzeitlich geschrieben -> nicht gesendet
        if d and d["stale_note"]:
            msg = f"Nicht gesendet – {d['stale_note']}"
        elif d and d["error"]:
            msg = f"Nicht gesendet – {d['error']}"
        else:
            msg = "Senden fehlgeschlagen – siehe Logs"
    return RedirectResponse(f"/queue?msg={_q(msg)}" if msg else "/queue", status_code=303)


@app.post("/queue/{draft_id}/reject")
def reject_draft(draft_id: int):
    db.update_draft(draft_id, status="rejected")
    return RedirectResponse("/queue", status_code=303)


@app.post("/queue/{draft_id}/save")
def save_draft(draft_id: int, edited_text: str = Form("")):
    _mark_edited(draft_id, edited_text)
    return RedirectResponse("/queue", status_code=303)


@app.get("/api/ppv-sets")
def api_ppv_sets(draft_id: int = 0):
    """Aktive PPV-Sets zur Auswahl in der Freigabe-Queue.

    Vorschaubilder werden bewusst NICHT mitgeliefert: die Fanvue-URLs sind
    kurzlebig und das Laden von 50 Ordnern auf einmal waere langsam. Die
    Oberflaeche holt sie einzeln und nur fuer sichtbare Sets nach.
    """
    offered: set = set()
    purchased: set = set()
    if draft_id:
        d = db.get_draft(draft_id)
        if d:
            purchased = set(db.ppv_purchased_sets(d["user_uuid"]))
            # abgelaufene Angebote nicht mehr als "angeboten" markieren
            offered = db.ppv_blocked_sets(
                d["user_uuid"],
                float(db.get_setting("ppv_offer_reset_days", 0) or 0)) - purchased
    sets = []
    for row in db.enabled_ppv_folders():
        sets.append({
            "name": row["name"],
            "price_cents": row["price_cents"],
            "media_kind": (row["media_kind"] or "image"),
            "request_only": bool(row["request_only"]),
            "tags": row["tags"] or "",
            "offered": row["name"] in offered,
            "purchased": row["name"] in purchased,
        })
    return {"sets": sets}


@app.post("/queue/{draft_id}/ppv/attach")
async def queue_ppv_attach(draft_id: int, request: Request):
    """Haengt ein PPV-Set an einen wartenden Entwurf."""
    import json as _json
    from . import ppv_engine
    form = await request.form()
    folder_name = str(form.get("folder", "")).strip()
    regen = str(form.get("regen", "1")) == "1"

    draft = db.get_draft(draft_id)
    if not draft or draft["status"] != "pending":
        return {"ok": False, "error": "Entwurf nicht (mehr) offen."}
    row = db.get_ppv_folder(folder_name)
    if not row:
        return {"ok": False, "error": f"Set '{folder_name}' nicht gefunden."}

    payload = ppv_engine.build_payload({
        "name": row["name"], "price_cents": row["price_cents"],
        "preview_media_uuid": row["preview_media_uuid"], "tags": row["tags"] or "",
    })
    if not payload:
        return {"ok": False, "error": "Set enthält keine sendbaren Medien."}

    db.update_draft(
        draft_id, is_ppv=1, ppv_folder=payload["folder"],
        ppv_media_uuids=_json.dumps(payload["media_uuids"]),
        ppv_price_cents=payload["price_cents"],
        ppv_preview_uuid=payload["preview_uuid"],
        # Ein angehaengtes PPV soll nicht ungefragt rausgehen - der Mensch hat
        # es gerade bewusst ausgewaehlt und will den Text sehen.
        auto_send=0, scheduled_send_at=None,
    )
    db.log("info", "ppv", f"PPV '{payload['folder']}' manuell an Entwurf #{draft_id} gehängt",
           f"{len(payload['media_uuids'])} Medien · ${payload['price_cents']/100:.2f}")

    if regen:
        # is_ppv steht jetzt auf 1 -> die Neugenerierung schreibt eine
        # Bildunterschrift statt einer normalen Chat-Antwort.
        poller.regenerate_draft(draft_id, reason="PPV manuell angehängt", interactive=True)
    d = db.get_draft(draft_id)
    return {"ok": True, "folder": payload["folder"],
            "price_cents": payload["price_cents"],
            "media": len(payload["media_uuids"]),
            "text": (d["edited_text"] or d["generated_text"] or "") if d else ""}


@app.post("/queue/{draft_id}/ppv/remove")
def queue_ppv_remove(draft_id: int):
    """Entfernt das PPV aus einem Entwurf – der Text bleibt stehen."""
    draft = db.get_draft(draft_id)
    if not draft:
        return {"ok": False, "error": "Entwurf nicht gefunden."}
    db.update_draft(draft_id, is_ppv=0, ppv_folder=None, ppv_media_uuids=None,
                    ppv_price_cents=None, ppv_preview_uuid=None)
    db.log("info", "ppv", f"PPV aus Entwurf #{draft_id} entfernt", draft["ppv_folder"] or "")
    return {"ok": True}


@app.post("/queue/{draft_id}/regenerate")
def regenerate_draft(draft_id: int):
    """Manuelles „Neu generieren“ aus der Queue.

    Nutzt denselben Pfad wie die automatische Selbstheilung (poller), damit hier
    ebenfalls Anti-AI-Regeln, Namens-Filter, Sprach-Anker und Zeitkontext greifen.
    Der Klick hebt die Bearbeitungs-Sperre bewusst auf – der Mensch will ja
    ausdruecklich einen neuen Text.
    """
    draft = db.get_draft(draft_id)
    if not draft:
        return RedirectResponse("/queue", status_code=303)
    try:
        db.update_draft(draft_id, user_edited=0)
        poller.regenerate_draft(draft_id, reason="manuell angestossen", interactive=True)
    except Exception as exc:  # noqa: BLE001
        db.log("error", "generate", "Regenerate fehlgeschlagen", str(exc))
    return RedirectResponse("/queue", status_code=303)


# --------------------------------------------------------------------- Chats
def _fetch_group_members(filter_: str = "", custom_list_id: str = "", cap: int = 5000) -> list[dict]:
    """Chats einer Chat-Gruppe (Filter) aus Fanvue laden (nur Konversationen)."""
    return _cached(f"group:{filter_}:{custom_list_id}:{cap}",
                   lambda: _fetch_group_members_live(filter_, custom_list_id, cap))


def _fetch_group_members_live(filter_: str = "", custom_list_id: str = "",
                              cap: int = 5000) -> list[dict]:
    members: list[dict] = []
    page = 1
    # Groessere Seiten = halb so viele Aufrufe. Mag die API die Groesse nicht,
    # wird beim ersten Fehlschlag dauerhaft auf 50 zurueckgeschaltet.
    size = _CHAT_PAGE_SIZE[0]
    while page <= 120 and len(members) < cap:
        try:
            res = fanvue.list_chats(filter_=filter_, size=size, page=page,
                                    custom_list_id=custom_list_id)
        except fanvue.FanvueError as exc:
            # Nur zuruecknehmen, wenn die API die SEITENGROESSE ablehnt.
            # Bei 401/403/429/5xx liegt es an etwas anderem - das muss
            # durchschlagen, sonst verschleiert ein Rueckfall den echten Fehler.
            if size == 50 or exc.status not in (400, 413, 422):
                raise
            db.log("info", "system", "Fanvue mag Seitengroesse 100 nicht – nutze 50", "")
            _CHAT_PAGE_SIZE[0] = size = 50
            continue
        res = res if isinstance(res, dict) else {}
        for entry in res.get("data", []):
            u = entry.get("user", {})
            if u.get("uuid"):
                members.append({"uuid": u["uuid"], "handle": u.get("handle", ""),
                                "display_name": u.get("displayName", "")})
        if not res.get("pagination", {}).get("hasMore"):
            break
        page += 1
    return members


def _parse_insight(ins: dict) -> Optional[dict]:
    """Wandelt eine Fanvue-Insight-Antwort in kompakte Felder (Cent-Betraege)."""
    if not ins or ins.get("error"):
        return None
    sp = ins.get("spending", {}) or {}
    sub = ins.get("subscription", {}) or {}
    sources = {}
    for k, v in (sp.get("sources") or {}).items():
        sources[k] = int((v or {}).get("total", 0) or 0)
    return {
        "status": ins.get("status", ""),
        "total_cents": int((sp.get("total") or {}).get("total", 0) or 0),
        "max_cents": int((sp.get("maxSinglePayment") or {}).get("total", 0) or 0),
        "last_purchase": sp.get("lastPurchaseAt") or "",
        "sources": sources,
        "created_at": sub.get("createdAt") or "",
        "renews_at": sub.get("renewsAt") or "",
        "auto_renew": bool(sub.get("autoRenewalEnabled", False)),
    }


_insights_cache: dict[str, dict] = {}  # uuid -> {"data": parsed|None, "ts": float}


def _insights_for(uuids: list[str], ttl: float = 600) -> dict[str, dict]:
    """Insights fuer viele Fans, mit In-Memory-Cache. Fehler (z.B. fehlender
    Scope) werden verschluckt -> dann einfach keine Daten."""
    now = time.time()
    result: dict[str, dict] = {}
    missing: list[str] = []
    for u in uuids:
        c = _insights_cache.get(u)
        if c and (now - c["ts"]) < ttl:
            if c["data"]:
                result[u] = c["data"]
        else:
            missing.append(u)
    if missing and fanvue.is_connected():
        try:
            raw = fanvue.fan_insights_bulk(missing)
            for u in missing:
                parsed = _parse_insight(raw.get(u))
                _insights_cache[u] = {"data": parsed, "ts": now}
                if parsed:
                    result[u] = parsed
        except Exception:  # noqa: BLE001
            pass
    return result


# Fanvue liefert Chat-/Mitgliederlisten nur seitenweise (50 pro Aufruf). Bei 600+
# Fans sind das gut ein Dutzend nacheinander laufender HTTP-Aufrufe - genau das
# macht die Subs-Seite langsam. Weil sich die Liste selbst zwischen zwei Klicks
# praktisch nie aendert, wird sie kurz zwischengespeichert: Ansicht wechseln,
# blaettern, sortieren und Formulare abschicken laufen dann ohne einen einzigen
# API-Aufruf. Der Knopf "Neu laden" leert den Zwischenspeicher.
_CHAT_PAGE_SIZE = [100]      # veraenderlich: faellt bei Bedarf auf 50 zurueck
_list_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()
_cache_inflight: set[str] = set()
# Alter der Listen, die DIESE Anfrage benutzt hat. Thread-lokal, weil mehrere
# Seitenaufrufe gleichzeitig laufen koennen und sonst das Alter einer fremden
# Ansicht gemeldet wuerde.
_cache_seen = threading.local()


def _seen() -> dict[str, float]:
    if not hasattr(_cache_seen, "ages"):
        _cache_seen.ages = {}
    return _cache_seen.ages


def _cache_ttl() -> float:
    try:
        return float(db.get_setting("chats_cache_seconds", 120) or 0)
    except (TypeError, ValueError):
        return 120.0


def _cache_store(key: str, data: list[dict]) -> None:
    _list_cache[key] = {"ts": time.time(), "data": list(data)}
    try:
        db.set_api_cache(key, data)
    except Exception as exc:  # noqa: BLE001 - Zwischenspeichern darf nie stoeren
        db.log("warn", "system", "Fan-Liste nicht zwischenspeicherbar", str(exc))


def _cache_refresh_async(key: str, loader) -> None:
    """Liste im Hintergrund erneuern. Der Aufrufer wartet nicht darauf.

    Pro Schluessel laeuft hoechstens ein Durchlauf: sonst starten drei schnelle
    Klicks drei parallele Serien von API-Aufrufen.
    """
    with _cache_lock:
        if key in _cache_inflight:
            return
        _cache_inflight.add(key)

    def lauf() -> None:
        try:
            _cache_store(key, loader())
        except Exception as exc:  # noqa: BLE001 - Hintergrund darf nie hochkommen
            db.log("warn", "system", "Fan-Liste konnte nicht aktualisiert werden", str(exc))
        finally:
            with _cache_lock:
                _cache_inflight.discard(key)

    threading.Thread(target=lauf, daemon=True, name="fanlist-refresh").start()


def _cached(key: str, loader) -> list[dict]:
    """Liste aus dem Zwischenspeicher, sonst holen.

    Wichtig ist der mittlere Fall: liegt etwas vor, das zu alt ist, wird es
    TROTZDEM sofort ausgeliefert und nur im Hintergrund erneuert. Die Seite
    wartet also nie auf Fanvue - hoechstens beim allerersten Aufruf, wenn noch
    gar nichts da ist. Eine ein, zwei Minuten alte Fan-Liste ist hier
    unproblematisch: es geht um Namen, nicht um Betraege.
    """
    ttl = _cache_ttl()
    if ttl <= 0:
        return loader()

    eintrag = _list_cache.get(key)
    if eintrag is None:
        gespeichert = db.get_api_cache(key)          # ueberlebt den Neustart
        if gespeichert is not None and isinstance(gespeichert["data"], list):
            eintrag = {"ts": gespeichert["updated_at"], "data": gespeichert["data"]}
            _list_cache[key] = eintrag

    if eintrag is not None:
        alter = time.time() - eintrag["ts"]
        _seen()[key] = alter
        if alter >= ttl:
            _cache_refresh_async(key, loader)
        return list(eintrag["data"])

    # Nichts vorhanden - einmal blockierend holen (nur beim allerersten Mal)
    data = loader()
    _cache_store(key, data)
    _seen()[key] = 0.0
    return data


def _cache_clear() -> None:
    _list_cache.clear()
    _seen().clear()
    try:
        db.clear_api_cache("group:")
        db.clear_api_cache("list:")
    except Exception:  # noqa: BLE001
        pass


def _cache_age() -> Optional[float]:
    """Alter der aeltesten Liste, die diese Antwort benutzt hat."""
    ages = _seen()
    return max(ages.values()) if ages else None


def _cache_busy() -> bool:
    with _cache_lock:
        return bool(_cache_inflight)


def _fetch_list_members(list_uuid: str, cap: int = 5000) -> list[dict]:
    """ALLE Mitglieder einer Custom List (auch ohne bestehenden Chat)."""
    return _cached(f"list:{list_uuid}:{cap}", lambda: _fetch_list_members_live(list_uuid, cap))


def _fetch_list_members_live(list_uuid: str, cap: int = 5000) -> list[dict]:
    members: list[dict] = []
    page = 1
    while page <= 120 and len(members) < cap:
        res = fanvue.list_custom_list_members(list_uuid, page=page, size=50)
        for u in res.get("data", []):
            if u.get("uuid") and not u.get("isCreator"):
                members.append({"uuid": u["uuid"], "handle": u.get("handle", ""),
                                "display_name": u.get("displayName", "")})
        if not res.get("pagination", {}).get("hasMore"):
            break
        page += 1
    return members


_CHAT_FILTER_LABELS = {
    "unread": "Ungelesen", "not_answered": "Unbeantwortet", "online": "Gerade online",
    "not_muted": "Nicht stummgeschaltet", "subscribers": "Abonnenten",
    "recent_subscribers": "Kürzlich abonniert", "followers": "Follower",
    "on_free_trial": "In Gratis-Testphase", "spenders": "Zahlende Fans",
    "spent_more_than_50": "High-Spender", "has_tipped": "Haben schon getippt",
    "": "kein Filter",
}


# Wie viele Fan-Karten pro Seite. Jede Karte kostet DB-Abfragen (PPV-Statistik,
# Gedaechtnis, Insights) - ohne Seiten wuerden bei 600+ Fans alle auf einmal
# aufgebaut, und die Seite braeuchte spuerbar lange.
CHATS_PER_PAGE = 10


@app.get("/chats", response_class=HTMLResponse)
def chats(request: Request, view: str = "subscribers", q: str = "", msg: str = "",
          page: int = 1, sort: str = "name", refresh: int = 0):
    _seen().clear()
    if refresh:
        _cache_clear()
    ctx = _base_ctx(request)
    ctx["error"] = None
    ctx["q"] = q
    ctx["msg"] = msg
    prio1_list = db.get_setting("chat_custom_list_id", "")
    prio2_list = db.get_setting("prio2_custom_list_id", "")
    p1_name = db.get_setting("chat_custom_list_name", "") or "Prio 1"
    p2_name = db.get_setting("prio2_custom_list_name", "") or "Prio 2"
    ctx["view"] = view
    ctx["sort"] = sort if sort in ("name", "chat") else "name"
    ctx["p1_name"] = p1_name
    ctx["p2_name"] = p2_name
    ctx["has_prio2"] = bool(prio2_list)

    def _members_of(list_id: str) -> list[dict]:
        if not list_id:
            return []
        try:
            return _fetch_list_members(list_id)
        except fanvue.FanvueError as exc:
            ctx["error"] = (f"Mitglieder-Liste nicht verfügbar ({exc}); "
                            "zeige nur Fans mit bestehender Konversation.")
            return _fetch_group_members(custom_list_id=list_id)

    # Woher stammt die angezeigte Liste? Wird unter dem Dropdown eingeblendet,
    # damit eine kurze Liste nicht wie ein Fehler aussieht.
    quelle = ""
    members: list[dict] = []
    if view == "all":
        quelle = "alle in der Datenbank erfassten Fans"
    elif fanvue.is_connected():
        try:
            if view == "subscribers":
                members = _fetch_group_members(filter_="subscribers")
                quelle = "Fanvue-Gruppe „Abonnenten“"
            elif view == "followers":
                members = _fetch_group_members(filter_="followers")
                quelle = "Fanvue-Gruppe „Follower“"
            elif view == "prio2":
                members = [{**m, "group": "Prio 2"} for m in _members_of(prio2_list)]
                quelle = f"Fanvue-Liste „{p2_name}“"
            elif view == "both":
                seen: dict[str, dict] = {}
                for m in _members_of(prio1_list):
                    seen[m["uuid"]] = {**m, "group": "Prio 1"}
                for m in _members_of(prio2_list):
                    if m["uuid"] not in seen:
                        seen[m["uuid"]] = {**m, "group": "Prio 2"}
                members = list(seen.values())
                quelle = f"Fanvue-Listen „{p1_name}“ + „{p2_name}“"
            else:  # prio1 (Standard)
                if prio1_list:
                    members = [{**m, "group": "Prio 1"} for m in _members_of(prio1_list)]
                    quelle = f"Fanvue-Liste „{p1_name}“"
                else:
                    # Keine Prio-1-Liste hinterlegt -> es zaehlt der Gruppenfilter
                    # aus den Einstellungen. Der kann sehr eng sein (z. B.
                    # "Unbeantwortet" zeigt fast nichts, wenn der Bot mitkommt).
                    filt = db.get_setting("chat_filter", "unread")
                    members = _fetch_group_members(filter_=filt)
                    ctx["no_prio1_list"] = True
                    ctx["chat_filter_label"] = _CHAT_FILTER_LABELS.get(filt, filt or "kein Filter")
                    quelle = (f"Gruppenfilter „{ctx['chat_filter_label']}“ "
                              "(keine Prio-1-Liste hinterlegt)")
        except Exception as exc:  # noqa: BLE001
            ctx["error"] = str(exc)
    if not members:
        # Fallback: bereits erfasste Chats aus der DB
        members = [{"uuid": c["user_uuid"], "handle": c["handle"] or "",
                    "display_name": c["display_name"] or ""} for c in db.list_chats()]
        if view != "all":
            quelle = (quelle + " – leer, daher alle erfassten Fans") if quelle \
                else "alle in der Datenbank erfassten Fans"
    ctx["quelle"] = quelle
    ctx["db_total"] = len(db.list_chats())

    # --- Suche serverseitig -------------------------------------------------
    # Frueher filterte JavaScript die fertige Liste im Browser. Mit Seiten waere
    # das wertlos: gesucht wuerde nur in den 10 gerade sichtbaren Fans.
    such = (q or "").strip().lower()
    if such:
        members = [m for m in members
                   if such in (m.get("display_name") or "").lower()
                   or such in (m.get("handle") or "").lower()]

    # --- Sortierung ---------------------------------------------------------
    if ctx["sort"] == "chat":
        # Zuletzt geschrieben zuerst; wer nie geschrieben hat, ganz nach hinten.
        letzte = db.last_inbound_map()
        members.sort(key=lambda m: (-(letzte.get(m["uuid"]) or 0),
                                    (m.get("display_name") or m.get("handle") or "").lower()))
    else:
        members.sort(key=lambda m: ((m.get("display_name") or m.get("handle") or "~").lower(),
                                    (m.get("handle") or "").lower()))

    # --- Seiten -------------------------------------------------------------
    gesamt = len(members)
    seiten = max(1, (gesamt + CHATS_PER_PAGE - 1) // CHATS_PER_PAGE)
    page = max(1, min(int(page or 1), seiten))
    members = members[(page - 1) * CHATS_PER_PAGE: page * CHATS_PER_PAGE]
    ctx["page"] = page
    ctx["pages"] = seiten
    ctx["total"] = gesamt
    ctx["per_page"] = CHATS_PER_PAGE
    ctx["von"] = 0 if not gesamt else (page - 1) * CHATS_PER_PAGE + 1
    ctx["bis"] = min(page * CHATS_PER_PAGE, gesamt)
    # Rueckweg fuer alle Formulare auf der Seite: nach einer Aktion landet man
    # wieder in derselben Ansicht, Sortierung, Suche und auf derselben Seite -
    # sonst wirft einen jede Kleinigkeit zurueck auf Seite 1.
    ctx["backq"] = (f"view={_q(view)}&sort={_q(ctx['sort'])}"
                    f"&q={_q(q or '')}&page={page}")
    alter = _cache_age()
    ctx["cache_age"] = int(alter) if alter is not None else None
    ctx["cache_busy"] = _cache_busy()

    enriched = []
    insights = _insights_for([m["uuid"] for m in members])
    # Gedaechtnis fuer ALLE Fans in EINER Abfrage. Pro Fan-Karte einzeln zu lesen
    # waere bei langen Listen spuerbar - _insights_for macht es aus demselben
    # Grund genauso.
    memories = memory.get_many([m["uuid"] for m in members])
    # Ebenso die Zahl der gespeicherten Fan-Nachrichten - eine Abfrage statt
    # einer je Karte.
    msg_counts = db.fan_message_counts([m["uuid"] for m in members])
    memory_on = bool(db.get_setting("memory_enabled", False))
    for m in members:
        db.upsert_chat(m["uuid"], m["handle"], m["display_name"])
        row = db.get_chat(m["uuid"])
        enriched.append({
            "uuid": m["uuid"],
            "handle": m["handle"] or (row["handle"] if row else ""),
            "display_name": m["display_name"] or (row["display_name"] if row else ""),
            "bot_enabled": bool(row["bot_enabled"]) if row else True,
            "mode_override": (row["mode_override"] if row else "") or "",
            "persona_override": (row["persona_override"] if row else "") or "",
            "notes": (row["notes"] if row else "") or "",
            "memory": memories.get(m["uuid"]),
            "memory_block": memory.render_block(mem=memories.get(m["uuid"])),
            # Dreistufiger Schalter je Fan: '' folgt global, 'on'/'off' stechen.
            "memory_mode": ((row["memory_mode"] if row else "") or ""),
            "memory_active": memory.enabled_for(row),
            "memory_long": memory.by_category(memories.get(m["uuid"]) or {}),
            "stats": db.ppv_offer_stats(m["uuid"]),
            # Nur was lokal gespeichert ist - der Wert springt nach einem
            # Einlesen hoch und zeigt damit nebenbei, ob es schon gelaufen ist.
            "msg_count": msg_counts.get(m["uuid"], 0),
            "backfill_state": ((row["backfill_state"] if row else "") or ""),
            "backfill_at": (row["backfill_at"] if row else None),
            "backfill_note": ((row["backfill_note"] if row else "") or ""),
            "insight": insights.get(m["uuid"]),
            "group": m.get("group", ""),
            "last_inbound_uuid": (row["last_inbound_uuid"] if row else "") or "",
            "last_inbound_at": (row["last_inbound_at"] if row else None),
            # Wartet dieser Fan auf eine Antwort? Die letzte Fan-Nachricht ist
            # neuer als alles, was ihm je gesendet wurde - dann ist die
            # Verarbeitungsmarke vermutlich zu weit vorgerueckt.
            "waiting": _chat_is_waiting(row) if row else False,
        })
    ctx["chats"] = enriched
    ctx["has_insights"] = bool(insights)
    ctx["ppv_enabled"] = db.get_setting("ppv_enabled", False)
    ctx["memory_on"] = memory_on
    ctx["memory_consolidate_on"] = bool(db.get_setting("memory_consolidate_enabled", False))
    ctx["memory_max_chars"] = int(db.get_setting("memory_max_chars", 1200) or 1200)
    ctx["memory_categories"] = memory.CATEGORIES
    ctx["memory_max_short"] = int(db.get_setting("memory_max_short", 12) or 12)
    ctx["memory_short_max_age_days"] = int(db.get_setting("memory_short_max_age_days", 21) or 21)
    return templates.TemplateResponse("chats.html", ctx)


@app.get("/chats/{user_uuid}/ppv", response_class=HTMLResponse)
def chat_ppv_page(request: Request, user_uuid: str, synced: str = "", matched: str = "",
                  err: str = "", exported: str = ""):
    ctx = _base_ctx(request)
    chat = db.get_chat(user_uuid)
    ctx["chat"] = chat
    ctx["user_uuid"] = user_uuid
    # Fanvue-Insights (Ausgaben/Abo) fuer diesen Fan
    ctx["insight"] = None
    if fanvue.is_connected():
        try:
            ctx["insight"] = _parse_insight(fanvue.get_fan_insights(user_uuid))
        except Exception:  # noqa: BLE001
            ctx["insight"] = None
    ctx["sync_synced"] = synced
    ctx["sync_matched"] = matched
    ctx["sync_err"] = err
    ctx["exported"] = exported
    ctx["stats"] = db.ppv_offer_stats(user_uuid)

    offers = db.list_ppv_offers(user_uuid)
    purchased = set(db.ppv_purchased_sets(user_uuid)) | {o["folder"] for o in offers if o["purchased"]}
    # Nur noch gesperrte Sets gelten als "angeboten" - abgelaufene Angebote
    # fallen laut Einstellung zurueck auf "nicht angeboten".
    reset_days = float(db.get_setting("ppv_offer_reset_days", 0) or 0)
    offered = db.ppv_blocked_sets(user_uuid, reset_days) - purchased
    dates = db.folder_offer_dates(user_uuid)

    # Alle verkaufbaren Sets + zusaetzlich bereits angebotene (auch wenn inzwischen deaktiviert)
    names = [f["name"] for f in db.enabled_ppv_folders()]
    prices = {f["name"]: f["price_cents"] for f in db.enabled_ppv_folders()}
    for o in offers:
        if o["folder"] not in names:
            names.append(o["folder"])
            prices.setdefault(o["folder"], o["price_cents"])

    table = []
    for name in names:
        if name in purchased:
            status, value = "gekauft", "bought"
        elif name in offered:
            status, value = "angeboten", "offered"
        else:
            status, value = "nicht angeboten", "not_offered"
        d = dates.get(name, {})
        table.append({
            "name": name,
            "price_cents": prices.get(name, 0),
            "status": status,
            "status_value": value,
            # wurde frueher angeboten, ist aber durch die Frist wieder frei
            "expired": bool(value == "not_offered" and d.get("offered_at")),
            "offered_at": d.get("offered_at"),
            "purchased_at": d.get("purchased_at"),
        })
    ctx["table"] = table
    return templates.TemplateResponse("chat_ppv.html", ctx)


@app.post("/chats/{user_uuid}/ppv/status")
async def chat_ppv_status(user_uuid: str, request: Request):
    form = await request.form()
    folder = form.get("folder_name", "")
    status = form.get("status", "")  # 'bought' | 'offered' | 'not_offered'
    if folder:
        chat = db.get_chat(user_uuid)
        handle = (chat["handle"] if chat else "") or ""
        fcfg = db.get_ppv_folder(folder)
        price = (fcfg["price_cents"] if fcfg else 0) or 0
        if status == "bought":
            db.ensure_offer(user_uuid, handle, folder, price)
            db.add_ppv_offered(user_uuid, folder)
            db.add_ppv_purchased(user_uuid, folder)
            db.set_folder_offers_purchased(user_uuid, folder, True, time.time())
            db.update_ppv_state(user_uuid, unpurchased_streak=0, last_ppv_at=None,
                                outbound_since_ppv=int(db.get_setting("ppv_cooldown_outbound", 2)))
            db.log("info", "send", f"Set '{folder}' als gekauft markiert ({user_uuid[:8]})")
        elif status == "offered":
            db.ensure_offer(user_uuid, handle, folder, price)
            db.add_ppv_offered(user_uuid, folder)
            db.remove_ppv_purchased(user_uuid, folder)
            db.set_folder_offers_purchased(user_uuid, folder, False)
            db.log("info", "send", f"Set '{folder}' als angeboten markiert ({user_uuid[:8]})")
        else:  # not_offered -> Reset: kann wieder angeboten werden
            db.remove_ppv_purchased(user_uuid, folder)
            db.remove_ppv_offered(user_uuid, folder)
            db.delete_offers_for_folder(user_uuid, folder)
            db.log("info", "send", f"Set '{folder}' zurückgesetzt – wieder anbietbar ({user_uuid[:8]})")
    return RedirectResponse(f"/chats/{user_uuid}/ppv", status_code=303)


@app.post("/chats/{user_uuid}/ppv/sync-purchases")
def chat_ppv_sync(user_uuid: str):
    """Liest die Kaufhistorie des Fans aus Fanvue und markiert gekaufte PPV-Sets.
    Ordnet gekaufte, bezahlte Nachrichten ueber ihre Medien-UUIDs den Vault-Ordnern zu."""
    from urllib.parse import quote as _q
    if not fanvue.is_connected():
        return RedirectResponse(f"/chats/{user_uuid}/ppv?err={_q('Nicht mit Fanvue verbunden')}",
                                status_code=303)
    me_uuid = fanvue.account_uuid()
    try:
        purchases = fanvue.collect_purchased_ppv(user_uuid, me_uuid)
        # Medien-Index ueber ALLE PPV-Ordner (nicht nur aktivierte), zwischengespeichert
        folder_media = fanvue.media_folder_index(ppv_only=True)
        matched = set()
        for p in purchases:
            for folder, uuids in folder_media.items():
                if p["media_uuids"] & uuids:
                    db.add_ppv_purchased(user_uuid, folder)
                    db.set_folder_offers_purchased(user_uuid, folder, True, time.time())
                    matched.add(folder)
                    break
        if matched:
            db.update_ppv_state(user_uuid, unpurchased_streak=0, last_ppv_at=None,
                                outbound_since_ppv=int(db.get_setting("ppv_cooldown_outbound", 2)))
        db.log("info", "send",
               f"Kauf-Sync {user_uuid[:8]}: {len(purchases)} Käufe gefunden, "
               f"{len(matched)} Sets zugeordnet", ", ".join(sorted(matched)))
        return RedirectResponse(
            f"/chats/{user_uuid}/ppv?synced={len(purchases)}&matched={len(matched)}",
            status_code=303)
    except Exception as exc:  # noqa: BLE001
        db.log("error", "send", "Kauf-Sync fehlgeschlagen", str(exc))
        return RedirectResponse(f"/chats/{user_uuid}/ppv?err={_q(str(exc)[:200])}", status_code=303)


@app.post("/ppv/subscriber/{user_uuid}/purchased")
async def ppv_mark_purchased(user_uuid: str, request: Request):
    form = await request.form()
    folder = form.get("folder_name", "")
    if folder:
        db.add_ppv_purchased(user_uuid, folder)
        db.mark_offer_purchased_by_folder(user_uuid, folder, time.time())
        # Kauf setzt Cooldown/Streak zurueck
        db.update_ppv_state(user_uuid, unpurchased_streak=0, last_ppv_at=None,
                            outbound_since_ppv=int(db.get_setting("ppv_cooldown_outbound", 2)))
        db.log("info", "send", f"Set '{folder}' manuell als gekauft markiert ({user_uuid[:8]})")
    return RedirectResponse("/chats", status_code=303)


@app.post("/ppv/subscriber/{user_uuid}/reset-offered")
def ppv_reset_offered(user_uuid: str):
    db.reset_ppv_offered(user_uuid)
    db.log("info", "system", f"Angebotene Sets zurueckgesetzt ({user_uuid[:8]})")
    return RedirectResponse("/chats", status_code=303)


@app.post("/chats/{user_uuid}/update")
def update_chat_route(user_uuid: str, bot_enabled: str = Form(""),
                      # Ein NICHT angehaktes Kaestchen schickt der Browser gar
                      # nicht mit - das Feld fehlt dann im Formular. Mit "on" als
                      # Vorgabewert kam beim Abhaken also derselbe Wert an wie
                      # beim Anhaken, und der Bot schaltete sich sofort wieder
                      # ein. Vorgabe muss leer sein: fehlt das Feld, ist es aus.
                      mode_override: str = Form(""), persona_override: str = Form(""),
                      notes: str = Form(""), back: str = Form("")):
    db.update_chat(
        user_uuid,
        bot_enabled=1 if bot_enabled == "on" else 0,
        mode_override=mode_override or None,
        persona_override=persona_override.strip() or None,
        notes=notes.strip() or None,
    )
    return _chats_back(back, "Gespeichert")


# ------------------------------------------------------------- Gedaechtnis
# Alles, was hier von Hand eingetragen wird, bekommt src="manual" und ist damit
# vor der naechtlichen Verdichtung geschuetzt.

def _chats_back(back: str, msg: str = "") -> RedirectResponse:
    """Zurueck zur Subs-Seite - mit Ansicht, Sortierung, Suche UND Seite.

    Der Wert kommt aus einem Formularfeld und wird deshalb nicht uebernommen,
    sondern neu zusammengesetzt: nur bekannte Parameter, sauber kodiert. Sonst
    liesse sich ueber ein manipuliertes Feld eine fremde Zieladresse einschleusen.
    """
    from urllib.parse import parse_qs
    roh = parse_qs((back or "").lstrip("?&"), keep_blank_values=False)
    teile = []
    for key in ("view", "sort", "q", "page"):
        wert = (roh.get(key) or [""])[0].strip()
        if wert:
            teile.append(f"{key}={_q(wert)}")
    if msg:
        teile.append(f"msg={_q(msg)}")
    return RedirectResponse("/chats" + ("?" + "&".join(teile) if teile else ""),
                            status_code=303)


def _memory_back(back: str, msg: str) -> RedirectResponse:
    return _chats_back(back, msg)


@app.post("/chats/{user_uuid}/memory/add")
def memory_add_route(user_uuid: str, layer: str = Form("long"), value: str = Form(""),
                     due: str = Form(""), category: str = Form(""),
                     is_open: str = Form(""), back: str = Form("")):
    if memory.add_manual(user_uuid, layer, value, due, category=category,
                         is_open=bool(is_open)):
        return _memory_back(back, "Eintrag gespeichert")
    return _memory_back(back, "Eintrag war leer")


@app.post("/chats/{user_uuid}/memory/promote")
def memory_promote_route(user_uuid: str, index: int = Form(-1),
                         category: str = Form(""), back: str = Form("")):
    """Eine Kurzzeit-Notiz ins Langzeitgedaechtnis heben, bevor sie verfaellt."""
    if memory.promote(user_uuid, index, category):
        return _memory_back(back, "Ins Langzeitgedächtnis übernommen")
    return _memory_back(back, "Notiz nicht gefunden")


@app.post("/chats/{user_uuid}/memory/mode")
def memory_mode_route(user_uuid: str, mode: str = Form(""), back: str = Form("")):
    """Gedaechtnis je Fan: Standard / immer an / immer aus.

    Damit laesst sich die Funktion an einzelnen Fans erproben, ohne sie fuer
    alle scharfzuschalten.
    """
    gesetzt = memory.set_mode(user_uuid, mode)
    return _memory_back(back, {
        "on": "Gedächtnis für diesen Fan eingeschaltet",
        "off": "Gedächtnis für diesen Fan ausgeschaltet",
    }.get(gesetzt, "Gedächtnis folgt wieder der globalen Einstellung"))


@app.post("/chats/{user_uuid}/memory/delete")
def memory_delete_route(user_uuid: str, layer: str = Form(""), index: int = Form(-1),
                        back: str = Form("")):
    if memory.delete_entry(user_uuid, layer, index):
        return _memory_back(back, "Eintrag gelöscht")
    return _memory_back(back, "Eintrag nicht gefunden")


@app.post("/chats/{user_uuid}/memory/close")
def memory_close_route(user_uuid: str, index: int = Form(-1), back: str = Form("")):
    if memory.close_loop(user_uuid, index):
        return _memory_back(back, "Faden als erledigt markiert")
    return _memory_back(back, "Faden nicht gefunden")


@app.post("/chats/{user_uuid}/memory/run")
def memory_run_route(user_uuid: str, back: str = Form("")):
    """Verdichtung sofort ausloesen - unabhaengig von Schwelle und Mindestabstand.

    Das ist der Knopf fuer die Abnahme: Gedaechtnis vorher/nachher vergleichen,
    ohne auf den naechsten Zyklus warten zu muessen.
    """
    try:
        ok, note = memory.consolidate(user_uuid, force=True)
    except Exception as exc:  # noqa: BLE001
        return _memory_back(back, f"Verdichtung fehlgeschlagen: {exc}")
    return _memory_back(back, f"Gedächtnis aktualisiert – {note}" if ok
                        else f"Nicht aktualisiert – {note}")


@app.post("/chats/{user_uuid}/memory/backfill")
def memory_backfill_route(user_uuid: str, name: str = Form(""), back: str = Form("")):
    """Kompletten Chatverlauf nachtraeglich einlesen und verdichten.

    Laeuft im Hintergrund: bei mehreren tausend Nachrichten dauert allein das
    Holen aus Fanvue eine gute Minute, dazu kommen Dutzende LLM-Aufrufe. Eine
    HTTP-Antwort so lange offen zu halten waere ein Timeout mit Ansage.
    """
    if not fanvue.is_connected():
        return _chats_back(back, "Nicht mit Fanvue verbunden")
    ok, note = memory.start_backfill(user_uuid, name)
    return _chats_back(back, f"Chat wird eingelesen – {note}" if ok
                       else f"Nicht gestartet – {note}")


@app.get("/chats/memory/backfill-status")
def memory_backfill_status():
    return memory.backfill_status()


@app.post("/chats/{user_uuid}/memory/clear")
def memory_clear_route(user_uuid: str, back: str = Form("")):
    """Gedaechtnis, Protokoll und Rohnachrichten dieses Fans restlos loeschen."""
    db.delete_memory(user_uuid)
    db.log("info", "memory", f"Gedächtnis für {user_uuid[:8]} gelöscht", "")
    return _memory_back(back, "Gedächtnis gelöscht")


def _chat_is_waiting(row) -> bool:
    """Fan-Nachricht neuer als die letzte gesendete Antwort und kein offener Entwurf?

    Genau das ist der Zustand, in dem jemand haengt: der Bot haelt die Nachricht
    fuer verarbeitet, obwohl nie eine Antwort rausging.
    """
    try:
        li = row["last_inbound_at"]
        if not li:
            return False
        if db.has_open_draft(row["user_uuid"]):
            return False        # wartet regulaer in der Freigabe
        letzte = db.last_sent_at(row["user_uuid"])
        return not letzte or letzte < li
    except Exception:  # noqa: BLE001
        return False


@app.post("/chats/{user_uuid}/reset-inbound")
def chat_reset_inbound(user_uuid: str, back: str = Form("")):
    """Setzt die Verarbeitungsmarke zurueck.

    Der Bot merkt sich in last_inbound_uuid, bis zu welcher Nachricht er einen
    Chat bearbeitet hat. Ist dort faelschlich eine Nachricht vermerkt, die nie
    beantwortet wurde (etwa weil die Generierung an einem Netzwerk-Timeout
    scheiterte oder ein Entwurf verworfen wurde), bleibt der Fan fuer immer
    ohne Antwort. Nach dem Zuruecksetzen greift der naechste Durchlauf die
    letzte Fan-Nachricht wieder auf.
    """
    chat = db.get_chat(user_uuid)
    if not chat:
        return _chats_back(back, "Chat nicht gefunden")
    if db.has_open_draft(user_uuid):
        return _chats_back(
            back, "Es gibt bereits einen offenen Entwurf – bitte zuerst freigeben oder verwerfen")
    db.update_chat(user_uuid, last_inbound_uuid=None)
    name = chat["display_name"] or chat["handle"] or user_uuid[:8]
    db.log("info", "system", f"Verarbeitungsmarke für {name} zurückgesetzt",
           f"war: {chat['last_inbound_uuid']}")
    return _chats_back(
        back, f"{name}: letzte Nachricht wird beim nächsten Durchlauf erneut beantwortet")


@app.post("/chats/{user_uuid}/reactivate")
def reactivate_now_route(user_uuid: str, handle: str = Form(""), display_name: str = Form(""),
                         back: str = Form("")):
    """Manuell eine Reaktivierung fuer diesen Fan ausloesen (landet in der Freigabe)."""
    if not fanvue.is_connected():
        return _chats_back(back, "Nicht mit Fanvue verbunden")
    if db.has_open_draft(user_uuid):
        return _chats_back(back, "Es gibt bereits einen offenen Entwurf für diesen Fan")
    db.upsert_chat(user_uuid, handle, display_name)
    chat = db.get_chat(user_uuid)
    # "Bot aus" muss auch die Handausloesung sperren. Der automatische Lauf
    # filtert bot_enabled schon in der Abfrage (db.due_reactivation_chats);
    # ohne diese Sperre waere der Knopf das Schlupfloch, durch das ein
    # stillgelegter Chat doch wieder angeschrieben wird.
    if chat is not None and not chat["bot_enabled"]:
        return _chats_back(back, "Bot ist für diesen Fan aus – keine Reaktivierung erstellt")
    me_uuid = fanvue.account_uuid()
    folder = (db.get_setting("reactivation_folder", "") or "").strip()
    try:
        poller._create_reactivation_draft(chat, me_uuid, folder, manual=True)
        db.update_chat(user_uuid, last_reactivation_at=time.time())
        # Auch die Handausloesung zaehlt als Versuch - sonst umgeht sie die
        # Abbruchgrenze und der Fan bekommt doch wieder unbegrenzt Anschreiben.
        db.bump_reactivation_streak(user_uuid)
        msg = "Reaktivierung erstellt – sie liegt jetzt in der Freigabe-Queue"
    except Exception as exc:  # noqa: BLE001
        db.log("error", "generate",
               f"Manuelle Reaktivierung fehlgeschlagen ({handle or user_uuid})", str(exc))
        msg = f"Reaktivierung fehlgeschlagen: {exc}"
    return _chats_back(back, msg)


# ------------------------------------------------------------------ Settings
_SHARED_FANVUE_KEYS = frozenset({"fanvue_client_id", "fanvue_client_secret", "fanvue_redirect_uri", "fanvue_api_version"})
_SHARED_UPDATE_KEYS = frozenset({"update_check_enabled", "update_notify_telegram", "update_check_interval_hours"})
_SHARED_TELEGRAM_KEYS = frozenset({"telegram_bot_token", "telegram_chat_id", "app_base_url"})
_SHARED_SETTINGS_KEYS = _SHARED_FANVUE_KEYS | _SHARED_UPDATE_KEYS | _SHARED_TELEGRAM_KEYS


@app.get("/settings/shared", response_class=HTMLResponse)
def shared_settings_page(request: Request, saved: str = ""):
    ctx = _base_ctx(request)
    ctx.update(s=db.all_settings(), saved=saved)
    return templates.TemplateResponse("shared_settings.html", ctx)


@app.post("/settings/shared/fanvue")
async def save_shared_fanvue(request: Request):
    form = await request.form()
    for key in _SHARED_FANVUE_KEYS:
        if key in form:
            db.set_setting(key, str(form[key]).strip())
    db.log("info", "system", "Gemeinsame Fanvue-Einstellungen gespeichert")
    return RedirectResponse("/settings/shared?saved=fanvue#fanvue", status_code=303)


@app.post("/settings/shared/telegram")
async def save_shared_telegram(request: Request):
    form = await request.form()
    for key in _SHARED_TELEGRAM_KEYS:
        if key in form:
            db.set_setting(key, str(form[key]).strip())
    db.log("info", "system", "Gemeinsame Telegram-Verbindung gespeichert")
    return RedirectResponse("/settings/shared?saved=telegram#telegram", status_code=303)


@app.post("/settings/shared/updates")
async def save_shared_updates(request: Request):
    form = await request.form()
    try:
        interval = int(form.get("update_check_interval_hours", ""))
        if not 1 <= interval <= 168:
            raise ValueError
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Prüfabstand muss zwischen 1 und 168 Stunden liegen.")
    for key in _SHARED_UPDATE_KEYS - {"update_check_interval_hours"}:
        db.set_setting(key, key in form)
    db.set_setting("update_check_interval_hours", interval)
    db.log("info", "system", "Gemeinsame Update-Einstellungen gespeichert")
    return RedirectResponse("/settings/shared?saved=updates#updates", status_code=303)


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    ctx = _base_ctx(request)
    ctx["s"] = db.all_settings()
    # Eigene Fanvue-Listen live laden (nur wenn verbunden)
    ctx["custom_lists"] = []
    ctx["custom_lists_error"] = None
    # Vault-Ordner fuer die Auswahlfelder (Danke-Bild, Reaktivierungs-Selfies).
    # Leer lassen, wenn Fanvue nicht erreichbar ist - die Vorlage zeigt dann ein
    # Textfeld, damit ein bereits gesetzter Ordner nicht beim Speichern verloren geht.
    ctx["vault_folders"] = []
    if fanvue.is_connected():
        try:
            ctx["custom_lists"] = fanvue.list_custom_lists()
        except Exception as exc:  # noqa: BLE001
            ctx["custom_lists_error"] = str(exc)
        try:
            ctx["vault_folders"] = sorted(
                {str(f.get("name") or "") for f in fanvue.list_vault_folders() if f.get("name")})
        except Exception:  # noqa: BLE001 - Auswahlfeld ist Komfort, kein Muss
            ctx["vault_folders"] = []
    return templates.TemplateResponse("settings.html", ctx)


@app.post("/settings/shared/telegram-test")
@app.post("/settings/telegram-test")
async def settings_telegram_test(request: Request):
    """Testnachricht senden. Nutzt die Werte aus dem Formular, damit man vor
    dem Speichern probieren kann."""
    from . import notify
    form = await request.form()
    token = str(form.get("token", "")).strip() or db.get_setting("telegram_bot_token", "")
    chat_id = str(form.get("chat_id", "")).strip() or str(db.get_setting("telegram_chat_id", ""))
    try:
        bot = notify.get_me(token=token)
        notify.send_or_raise(
            "✅ <b>MP CreatorStudio</b>\nDie Telegram-Verbindung steht. "
            "Welche Benachrichtigungen du erhältst, legst du in den Einstellungen fest.",
            chat_id=chat_id, token=token)
        return {"ok": True,
                "msg": f"Testnachricht gesendet über @{bot.get('username', '?')}."}
    except notify.TelegramError as exc:
        return {"ok": False, "msg": str(exc)}


@app.post("/settings/shared/telegram-chatid")
@app.post("/settings/telegram-chatid")
async def settings_telegram_chatid(request: Request):
    """Chat-ID aus den letzten Bot-Updates ermitteln."""
    from . import notify
    form = await request.form()
    token = str(form.get("token", "")).strip() or db.get_setting("telegram_bot_token", "")
    try:
        chat_id, info = notify.discover_chat_id(token=token)
        if chat_id:
            return {"ok": True, "chat_id": chat_id, "msg": f"Gefunden: {info}"}
        return {"ok": False, "msg": info}
    except notify.TelegramError as exc:
        return {"ok": False, "msg": str(exc)}


@app.post("/settings/keyword-test")
async def settings_keyword_test(request: Request):
    """Probiert die Stichwort-Liste an einem Beispieltext aus.

    Nutzt die Liste aus dem Formular, damit man schon vor dem Speichern sieht,
    ob ein Wort wirklich trifft - und ob es faelschlich in harmlosen Woertern
    steckt.
    """
    from . import guardrails
    form = await request.form()
    text = str(form.get("text", ""))
    roh = str(form.get("keywords", ""))
    worte, gesehen = [], set()
    for teil in roh.replace("\n", ",").split(","):
        w = teil.strip()
        if w and w.lower() not in gesehen:
            gesehen.add(w.lower())
            worte.append(w)
    hits = [w for w in worte if guardrails._alert_pattern(w).search(text)]
    return {"ok": True, "hits": hits, "count": len(worte)}


@app.post("/settings/time-preview")
async def settings_time_preview(request: Request):
    """Live-Vorschau des Zeitkontext-Blocks.

    Nimmt Schedule und Zeitzone aus dem Formular entgegen (auch ungespeichert)
    und erlaubt optional einen fiktiven Zeitpunkt zum Durchtesten.
    """
    from . import persona_context
    from datetime import datetime, timedelta

    form = await request.form()
    schedule = str(form.get("schedule", ""))
    tz_name = str(form.get("timezone", "")).strip()

    try:
        tz = None
        if tz_name:
            try:
                from zoneinfo import ZoneInfo
                tz = ZoneInfo(tz_name)
            except Exception:  # noqa: BLE001
                return {"error": f"Unbekannte Zeitzone: {tz_name!r} – "
                                 f"gültig ist z.B. 'Europe/Berlin'."}
        dt = datetime.now(tz) if tz else datetime.now()

        # Optionaler fiktiver Zeitpunkt zum Testen
        weekday = str(form.get("weekday", "")).strip()
        clock = str(form.get("time", "")).strip()
        if weekday != "":
            dt += timedelta(days=(int(weekday) - dt.weekday()))
        if clock:
            hh, _, mm = clock.partition(":")
            dt = dt.replace(hour=int(hh), minute=int(mm or 0), second=0, microsecond=0)

        block = persona_context.build_context_block(dt, schedule_text=schedule, force=True)
        rules = persona_context.parse_schedule(schedule)
        valid_lines = sum(1 for ln in schedule.splitlines()
                          if ln.strip() and not ln.strip().startswith("#"))
        hint = ""
        if valid_lines and not rules:
            hint = "\n\n⚠ Keine Zeile konnte gelesen werden – Format prüfen."
        elif len(rules) < valid_lines:
            hint = (f"\n\n⚠ {valid_lines - len(rules)} von {valid_lines} Zeilen "
                    f"wurden nicht verstanden und ignoriert.")
        return {"block": block + hint}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Vorschau fehlgeschlagen: {exc}"}


_INT_KEYS = {
    "poll_interval_seconds", "max_chats_per_cycle", "reply_cooldown_seconds",
    "active_hour_start", "active_hour_end", "send_delay_min_seconds",
    "send_delay_max_seconds", "max_tokens", "history_messages", "queue_context_messages",
    "generation_retries", "generation_retry_delay", "max_reply_chars",
    "draft_recheck_interval_seconds", "draft_max_regen", "bracket_max_regen",
    "update_check_interval_hours",
    "ppv_confirm_context_messages",
    "ppv_intent_threshold", "ppv_min_fan_messages", "ppv_sexual_streak_trigger", "ppv_cooldown_minutes",
    "ppv_cooldown_outbound", "ppv_cooldown_long_minutes", "ppv_unpurchased_threshold",
    "ppv_max_media_per_set", "ppv_thumb_cache_hours", "ppv_offer_reset_days",
    "reactivation_inactive_hours", "reactivation_cooldown_days", "reactivation_max_per_cycle",
    "reactivation_delay_min_minutes", "reactivation_delay_max_minutes",
    "reactivation_max_attempts", "reactivation_max_silence_days",
    "prio2_interval_minutes", "prio2_jitter_minutes",
    "memory_max_chars", "memory_trigger_messages", "memory_quiet_hours",
    "memory_min_interval_hours", "memory_min_fan_messages", "memory_max_per_cycle",
    "memory_sweep_hour", "memory_max_long", "memory_max_short",
    "memory_short_max_age_days",
    "messages_retention_days", "chats_cache_seconds",
    "memory_backfill_max_pages", "memory_backfill_chunk", "memory_loop_max_age_days",
    "memory_backfill_max_messages", "memory_backfill_per_hour",
}
_FLOAT_KEYS = {"temperature", "memory_backfill_delay"}
_BOOL_KEYS = {"active_hours_enabled", "ppv_enabled", "ppv_use_llm_classifier",
              "ppv_confirm_enabled", "ppv_confirm_fail_open",
              "anti_ai_enabled", "reactivation_enabled", "incoming_image_enabled",
              "ppv_caption_use_vision", "ppv_block_on_distress", "tip_thanks_enabled",
              "time_context_enabled", "timestamps_in_history", "time_guard_enabled",
              "draft_recheck_enabled", "draft_regen_on_stale", "telegram_enabled",
              "update_check_enabled", "update_notify_telegram",
              "alert_keywords_enabled",
              "memory_enabled", "memory_consolidate_enabled", "messages_store_enabled",
              "memory_reactivation_use_loops"}


@app.post("/settings")
async def save_settings(request: Request):
    form = await request.form()
    for key in db.DEFAULT_SETTINGS:
        # Shared controls have their own forms. Omitted checkboxes here must
        # never disable the updater or overwrite the shared connection.
        if key == "bot_running" or key in _SHARED_SETTINGS_KEYS:
            continue
        if key in _BOOL_KEYS:
            db.set_setting(key, key in form)
            continue
        if key not in form:
            continue
        val = form[key]
        if key in _INT_KEYS:
            try:
                val = int(val)
            except ValueError:
                continue
        elif key in _FLOAT_KEYS:
            try:
                val = float(val)
            except ValueError:
                continue
        db.set_setting(key, val)
    # Namen der gewaehlten eigenen Liste zur Anzeige aufloesen
    chosen = db.get_setting("chat_custom_list_id", "")
    name = ""
    if chosen and fanvue.is_connected():
        try:
            for lst in fanvue.list_custom_lists():
                if lst.get("uuid") == chosen:
                    name = lst.get("name", "")
                    break
        except Exception:  # noqa: BLE001
            pass
    db.set_setting("chat_custom_list_name", name)
    # Prio-2-Listennamen ebenfalls aufloesen
    chosen2 = db.get_setting("prio2_custom_list_id", "")
    name2 = ""
    if chosen2 and fanvue.is_connected():
        try:
            for lst in fanvue.list_custom_lists():
                if lst.get("uuid") == chosen2:
                    name2 = lst.get("name", "")
                    break
        except Exception:  # noqa: BLE001
            pass
    db.set_setting("prio2_custom_list_name", name2)
    db.log("info", "system", "Einstellungen gespeichert")
    return RedirectResponse("/settings", status_code=303)


# ---------------------------------------------------------------------- PPV
def _dollars(cents) -> str:
    try:
        return f"{int(cents)/100:.2f}"
    except (TypeError, ValueError):
        return "0.00"


templates.env.filters["dollars"] = _dollars


@app.get("/ppv", response_class=HTMLResponse)
def ppv_page(request: Request):
    ctx = _base_ctx(request)
    ctx["folders"] = []
    ctx["error"] = None
    ctx["thumb_cache_hours"] = db.get_setting("ppv_thumb_cache_hours", 6)
    if fanvue.is_connected():
        try:
            live = fanvue.list_vault_folders()
            cfg = db.list_ppv_folders()
            merged = []
            for f in live:
                row = cfg.get(f["name"])
                merged.append({
                    "name": f["name"],
                    "media_count": f.get("mediaCount", 0),
                    "enabled": bool(row["enabled"]) if row else False,
                    "price_cents": row["price_cents"] if row else 500,
                    "tags": row["tags"] if row else "",
                    "request_only": bool(row["request_only"]) if row else False,
                    # Startwert fuer neue Ordner: Name entscheidet, Rest sind Bilder
                    "media_kind": (row["media_kind"] or "image") if row else (
                        "video" if ("video" in f["name"].lower() or "clip" in f["name"].lower())
                        else "image"),
                    # Versions-Kennung fuer den Browser-Cache: aendert sich das
                    # Vorschaubild, wird der localStorage-Cache dieses Ordners ungueltig.
                    "preview": (row["preview_media_uuid"] or "") if row else "",
                })
            ctx["folders"] = merged
        except Exception as exc:  # noqa: BLE001
            ctx["error"] = str(exc)
    return templates.TemplateResponse("ppv.html", ctx)


@app.post("/ppv/folder/save")
async def ppv_folder_save(request: Request):
    form = await request.form()
    name = form.get("name", "")
    if not name:
        return RedirectResponse("/ppv", status_code=303)
    try:
        price = int(round(float(form.get("price_dollars", "5") or "5") * 100))
    except ValueError:
        price = 500
    price = max(price, 300)  # Fanvue-Minimum $3
    db.upsert_ppv_folder(
        name,
        enabled=1 if form.get("enabled") == "on" else 0,
        price_cents=price,
        tags=(form.get("tags", "") or "").strip(),
        request_only=1 if form.get("request_only") == "on" else 0,
        media_kind=(form.get("media_kind", "image") or "image").strip().lower(),
    )
    return RedirectResponse("/ppv", status_code=303)


def _parse_date(raw: str):
    """ISO oder TT.MM.JJJJ -> unix ts, sonst None."""
    raw = (raw or "").strip()
    if not raw:
        return None
    from datetime import datetime
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw[:19] if "T" in raw else raw, fmt).timestamp()
        except ValueError:
            continue
    return None


@app.get("/ppv/import", response_class=HTMLResponse)
def ppv_import_page(request: Request):
    ctx = _base_ctx(request)
    ctx["errors"] = None
    ctx["job"] = poller.import_status()
    return templates.TemplateResponse("ppv_import.html", ctx)


@app.get("/ppv/import/status")
def ppv_import_status():
    return poller.import_status()


@app.post("/ppv/import", response_class=HTMLResponse)
async def ppv_import(request: Request, file: UploadFile = File(...), check_api: str = Form("")):
    ctx = _base_ctx(request)
    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    parsed = csv_import.parse_purchase_csv(raw)
    ctx["errors"] = parsed["errors"] if not parsed["rows"] else None
    if parsed["rows"]:
        started = poller.start_purchase_import(parsed["rows"], check_api=(check_api == "on"))
        if not started:
            ctx["errors"] = ["Es läuft bereits ein Import. Bitte warten, bis dieser fertig ist."]
    ctx["job"] = poller.import_status()
    return templates.TemplateResponse("ppv_import.html", ctx)


_THUMB_TTL_SECONDS = 240.0  # kurzer Cache: frische signierte URLs laufen nicht ab


@app.get("/ppv/folder/{folder_name:path}/thumbs")
def ppv_folder_thumbs(folder_name: str, n: int = 3, refresh: int = 0):
    """Liefert bis zu n frische Vorschaubild-URLs eines Ordners (fuer die Uebersicht).
    Das gesetzte Vorschaubild kommt zuerst. Signierte Fanvue-URLs sind kurzlebig, daher
    nur ein kurzer Cache (~4 Min), damit nie abgelaufene URLs gerendert werden."""
    import json as _json
    cfg = db.get_ppv_folder(folder_name)
    # Kurzer Server-Cache
    if not refresh and cfg and cfg["thumbs_json"] and cfg["thumbs_cached_at"]:
        if (time.time() - cfg["thumbs_cached_at"]) < _THUMB_TTL_SECONDS:
            try:
                return {"thumbs": _json.loads(cfg["thumbs_json"]), "cached": True}
            except (ValueError, TypeError):
                pass
    if not fanvue.is_connected():
        return {"thumbs": []}
    try:
        preview_uuid = cfg["preview_media_uuid"] if cfg else None
        result = fanvue.list_folder_media(folder_name, size=max(n * 4, 12), media_type="")
        ready = []
        for m in result.get("data", []):
            if m.get("status") != "ready":
                continue
            url = fanvue.media_variant_url(m)
            if url:
                ready.append({"uuid": m.get("uuid"), "url": url})
        thumbs = []
        preview_item = next((r for r in ready if r["uuid"] == preview_uuid), None)
        if preview_item:
            thumbs.append({"url": preview_item["url"], "preview": True})
        for r in ready:
            if preview_item and r["uuid"] == preview_item["uuid"]:
                continue
            thumbs.append({"url": r["url"], "preview": False})
            if len(thumbs) >= n:
                break
        thumbs = thumbs[:n]
        db.upsert_ppv_folder(folder_name, thumbs_json=_json.dumps(thumbs),
                             thumbs_cached_at=time.time())
        return {"thumbs": thumbs, "cached": False}
    except Exception as exc:  # noqa: BLE001
        db.log("error", "ppv", f"thumbs '{folder_name}' fehlgeschlagen", str(exc))
        return {"thumbs": []}


@app.get("/ppv/folder/{folder_name:path}", response_class=HTMLResponse)
def ppv_folder_media(request: Request, folder_name: str, ok: str = "", err: str = ""):
    ctx = _base_ctx(request)
    ctx["folder_name"] = folder_name
    ctx["media"] = []
    ctx["error"] = None
    ctx["analyze_ok"] = ok
    ctx["analyze_err"] = err
    ctx["folder_cfg"] = db.get_ppv_folder(folder_name)
    if fanvue.is_connected():
        try:
            # media_type="" -> Bilder UND Videos anzeigen
            result = fanvue.list_folder_media(folder_name, size=50, media_type="")
            cfg = db.list_ppv_media(folder_name)
            items = []
            for m in result.get("data", []):
                if m.get("status") != "ready":
                    continue
                uuid = m.get("uuid")
                row = cfg.get(uuid)
                is_video = m.get("mediaType") == "video"
                # Fanvues KI-Tags aus mehreren Feldern buendeln
                fv_tags: list[str] = []
                t = m.get("tags")
                if isinstance(t, dict):
                    for field in ("tags", "bodyParts", "sexActs", "setting",
                                  "position", "importantTags", "otherTags"):
                        for tag in (t.get(field) or []):
                            tag = str(tag).strip().lower()
                            if tag and tag not in fv_tags:
                                fv_tags.append(tag)
                fv_joined = ", ".join(fv_tags)
                # Analyse: bei Videos das Standbild (thumbnail) nehmen, bei Bildern die main-Variante
                analyze_pref = (("thumbnail_gallery", "thumbnail") if is_video
                                else ("main", "thumbnail", "thumbnail_gallery"))
                items.append({
                    "uuid": uuid,
                    "thumb": fanvue.media_variant_url(m),
                    "analyze_url": fanvue.media_variant_url(m, prefer=analyze_pref),
                    "name": m.get("name") or uuid,
                    "is_video": is_video,
                    "recommended": m.get("recommendedPrice"),
                    "tags": row["tags"] if row else "",
                    "analyzed": bool(row["analyzed"]) if row else False,
                    "fanvue_tags": fv_joined,
                })
            ctx["media"] = items
        except Exception as exc:  # noqa: BLE001
            ctx["error"] = str(exc)
    return templates.TemplateResponse("ppv_folder.html", ctx)


@app.post("/ppv/media/{media_uuid}/tags")
async def ppv_media_tags(media_uuid: str, request: Request):
    form = await request.form()
    folder = form.get("folder_name", "")
    db.upsert_ppv_media(media_uuid, folder_name=folder, tags=(form.get("tags", "") or "").strip())
    return RedirectResponse(f"/ppv/folder/{folder}", status_code=303)


@app.post("/ppv/folder/preview/set")
async def ppv_set_preview(request: Request):
    form = await request.form()
    folder = form.get("folder_name", "")
    media_uuid = form.get("media_uuid", "")
    if folder:
        # Vorschaubild aendert die Reihenfolge/Markierung -> Thumbnail-Cache verwerfen
        db.upsert_ppv_folder(folder, preview_media_uuid=media_uuid or None,
                             thumbs_cached_at=None)
    return RedirectResponse(f"/ppv/folder/{folder}", status_code=303)


@app.post("/ppv/media/{media_uuid}/analyze")
async def ppv_media_analyze(media_uuid: str, request: Request):
    from urllib.parse import quote as _q
    form = await request.form()
    folder = form.get("folder_name", "")
    image_url = form.get("image_url", "")
    if not image_url:
        return RedirectResponse(f"/ppv/folder/{folder}?err={_q('Keine Bild-URL vorhanden')}",
                                status_code=303)
    try:
        tags = openrouter.analyze_image(image_url)
        if not tags:
            db.log("warn", "generate", f"Bildanalyse ohne Tags ({media_uuid[:8]})", "")
            return RedirectResponse(
                f"/ppv/folder/{folder}?err={_q('Modell lieferte keine Tags (evtl. Inhalt abgelehnt)')}",
                status_code=303)
        existing = db.get_ppv_media(media_uuid)
        merged = tags
        if existing and existing["tags"]:
            have = [t.strip() for t in existing["tags"].split(",") if t.strip()]
            merged = have + [t for t in tags if t not in have]
        db.upsert_ppv_media(media_uuid, folder_name=folder, tags=", ".join(merged), analyzed=1)
        db.log("info", "generate", f"Bild analysiert ({media_uuid[:8]})", ", ".join(tags))
        return RedirectResponse(f"/ppv/folder/{folder}?ok=1", status_code=303)
    except Exception as exc:  # noqa: BLE001
        db.log("error", "generate", "Bildanalyse fehlgeschlagen", str(exc))
        return RedirectResponse(f"/ppv/folder/{folder}?err={_q(str(exc)[:300])}", status_code=303)


# --------------------------------------------------------------------- Logs
@app.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request):
    ctx = _base_ctx(request)
    ctx["logs"] = db.list_logs(limit=300)
    return templates.TemplateResponse("logs.html", ctx)


# --------------------------------------------------------------------- OAuth
@app.get("/oauth/start")
def oauth_start(request: Request):
    client_id = db.get_setting("fanvue_client_id")
    redirect_uri = db.get_setting("fanvue_redirect_uri")
    if not client_id or not redirect_uri:
        db.log("error", "oauth", "Client-ID/Redirect-URI fehlen")
        return RedirectResponse("/settings/shared#fanvue", status_code=303)
    verifier, challenge = fanvue.make_pkce()
    state = fanvue.new_state()
    request.session["pkce_verifier"] = verifier
    request.session["oauth_state"] = state
    url = fanvue.build_authorize_url(client_id, redirect_uri, state, challenge)
    return RedirectResponse(url, status_code=303)


@app.get("/oauth/callback", response_class=HTMLResponse)
def oauth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    ctx = {"request": request}
    if error:
        ctx["message"] = f"Fanvue hat einen Fehler gemeldet: {error}"
        ctx["ok"] = False
        return templates.TemplateResponse("oauth_result.html", ctx)
    if not code or state != request.session.get("oauth_state"):
        ctx["message"] = "Ungueltiger State oder fehlender Code (CSRF-Schutz)."
        ctx["ok"] = False
        return templates.TemplateResponse("oauth_result.html", ctx)
    verifier = request.session.get("pkce_verifier", "")
    try:
        fanvue.exchange_code(code, verifier)
        db.log("info", "oauth", "Fanvue erfolgreich verbunden")
        ctx["message"] = "Fanvue erfolgreich verbunden!"
        ctx["ok"] = True
    except Exception as exc:  # noqa: BLE001
        db.log("error", "oauth", "Verbindung fehlgeschlagen", str(exc))
        ctx["message"] = f"Verbindung fehlgeschlagen: {exc}"
        ctx["ok"] = False
    request.session.pop("pkce_verifier", None)
    request.session.pop("oauth_state", None)
    return templates.TemplateResponse("oauth_result.html", ctx)


@app.post("/oauth/disconnect")
def oauth_disconnect():
    db.clear_tokens()
    db.log("info", "oauth", "Fanvue-Verbindung getrennt")
    return RedirectResponse("/settings/shared#fanvue", status_code=303)


@app.get("/health")
def health():
    return {"ok": True, "running": db.get_setting("bot_running", False) and not preview_enabled() and not deployment.pending(),
        "preview": preview_enabled(),
            "connected": fanvue.is_connected(), "ts": time.time()}


# AutoPost shares the process, port and release lifecycle of the AutoChat.
from .creatorpilot import install_content_studio
install_content_studio(app)

@app.exception_handler(PreviewNetworkBlocked)
async def preview_network_error(request, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse({'detail': MESSAGE}, status_code=403)
