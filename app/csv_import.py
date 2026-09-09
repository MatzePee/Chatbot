"""CSV-Import fuer PPV-Kaeufe.

Erwartete Spalten (Kopfzeile Pflicht, Reihenfolge egal, Gross/Kleinschreibung egal):

  Pflicht (mind. EINE Fan-Spalte + Set-Spalte):
    - handle      : Fanvue-Benutzername des Subscribers  (Synonyme: username, user, fan, subscriber)
      ODER
    - user_uuid   : interne Fanvue-UUID des Fans          (Synonyme: uuid, fan_uuid, userid)
    - folder      : Name des PPV-Sets/Ordners             (Synonyme: set, ppv, content, name, title)

  Optional:
    - price       : Preis in Dollar (z.B. 9.99 oder 9,99) (Synonyme: amount, betrag, preis)
      ODER price_cents : Preis in Cent
    - purchased_at: Kaufdatum (ISO oder TT.MM.JJJJ)       (Synonyme: date, datum, gekauft_am)
    - status      : "gekauft"/"1"/"yes" = Kauf (Default), "nicht"/"0"/"no" = kein Kauf

Trennzeichen (Komma oder Semikolon) und BOM werden automatisch erkannt.
"""
from __future__ import annotations

import csv
import io
from typing import Any

# Spalten-Synonyme -> kanonischer Name
_SYNONYMS = {
    "handle": "handle", "username": "handle", "user": "handle", "fan": "handle",
    "subscriber": "handle", "benutzer": "handle", "nutzer": "handle",
    "user_uuid": "user_uuid", "uuid": "user_uuid", "fan_uuid": "user_uuid",
    "userid": "user_uuid", "user_id": "user_uuid", "id": "user_uuid",
    "folder": "folder", "set": "folder", "ppv": "folder", "content": "folder",
    "name": "folder", "title": "folder", "ordner": "folder", "titel": "folder",
    "price": "price", "amount": "price", "betrag": "price", "preis": "price",
    "price_cents": "price_cents", "cents": "price_cents",
    "purchased_at": "purchased_at", "date": "purchased_at", "datum": "purchased_at",
    "gekauft_am": "purchased_at", "kaufdatum": "purchased_at",
    "status": "status", "gekauft": "status", "purchased": "status",
}


def _norm_header(name: str) -> str:
    key = (name or "").strip().lower().lstrip("﻿")
    return _SYNONYMS.get(key, key)


def _detect_delimiter(text: str) -> str:
    first = text.splitlines()[0] if text.splitlines() else ""
    return ";" if first.count(";") > first.count(",") else ","


def price_to_cents(raw: str) -> int:
    """'9,99' / '$9.99' / '9' -> Cent. Bei Fehler 0."""
    if raw is None:
        return 0
    s = str(raw).strip().replace("$", "").replace("€", "").replace(" ", "")
    if not s:
        return 0
    # deutsches Format: Komma als Dezimaltrenner
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    else:
        s = s.replace(",", "")  # Tausendertrenner
    try:
        return int(round(float(s) * 100))
    except ValueError:
        return 0


def _is_purchased(status: str) -> bool:
    """Nur bei eindeutig 'gekauft'-Status True. 'offered'/'unknown'/leer -> False."""
    return str(status).strip().lower() in ("purchased", "gekauft", "bought", "1",
                                           "yes", "ja", "true", "paid")


def parse_purchase_csv(text: str) -> dict[str, Any]:
    """Parst den CSV-Text. Rueckgabe:
    {rows: [{handle,user_uuid,folder,price_cents,purchased,purchased_at}], errors: [...], header: [...]}
    """
    text = text.lstrip("﻿")
    if not text.strip():
        return {"rows": [], "errors": ["Datei ist leer"], "header": []}
    delim = _detect_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    try:
        raw_header = next(reader)
    except StopIteration:
        return {"rows": [], "errors": ["Keine Kopfzeile gefunden"], "header": []}
    header = [_norm_header(h) for h in raw_header]

    errors: list[str] = []
    if "folder" not in header:
        errors.append("Pflichtspalte fehlt: 'folder' (bzw. set/ppv/content/name)")
    if "handle" not in header and "user_uuid" not in header:
        errors.append("Es muss 'handle' ODER 'user_uuid' als Spalte geben")
    if errors:
        return {"rows": [], "errors": errors, "header": header}

    idx = {name: i for i, name in enumerate(header)}
    rows: list[dict[str, Any]] = []
    for lineno, cols in enumerate(reader, start=2):
        if not any((c or "").strip() for c in cols):
            continue  # Leerzeile
        def val(name: str) -> str:
            i = idx.get(name)
            return (cols[i].strip() if i is not None and i < len(cols) else "")
        folder = val("folder")
        handle = val("handle").lstrip("@")
        user_uuid = val("user_uuid")
        if not folder:
            errors.append(f"Zeile {lineno}: kein Set/Ordner angegeben – übersprungen")
            continue
        if not handle and not user_uuid:
            errors.append(f"Zeile {lineno}: weder handle noch user_uuid – übersprungen")
            continue
        if "price_cents" in idx and val("price_cents"):
            try:
                price_cents = int(float(val("price_cents")))
            except ValueError:
                price_cents = 0
        else:
            price_cents = price_to_cents(val("price"))
        rows.append({
            "handle": handle,
            "user_uuid": user_uuid,
            "folder": folder,
            "price_cents": price_cents,
            # Nur bei Status-Spalte explizit; ohne Status-Spalte gilt jede Zeile als Kauf
            "purchased": (_is_purchased(val("status")) if "status" in idx else True),
            "purchased_at": val("purchased_at"),
        })
    return {"rows": rows, "errors": errors, "header": header}
