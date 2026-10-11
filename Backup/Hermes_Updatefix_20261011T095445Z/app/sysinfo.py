"""Server-Auslastung fuer das Dashboard - ohne zusaetzliche Abhaengigkeit.

Die App laeuft in einem Proxmox-LXC-Container. Deshalb werden die Werte
bevorzugt aus cgroup v2 gelesen (`/sys/fs/cgroup/...`): das sind die Grenzen
und Verbraeuche DES CONTAINERS. `/proc/cpuinfo` wuerde dagegen die CPUs des
ganzen Hosts zeigen - also z.B. 16 Kerne, obwohl dem Container nur 2
zugeteilt sind.

Fallback-Kette pro Wert: cgroup v2 -> cgroup v1 -> /proc -> None.
Ist ein Wert nicht ermittelbar, steht None drin und die Oberflaeche blendet
ihn aus. Nichts hier darf jemals eine Exception nach aussen lassen - eine
Anzeige ist die Sache nicht wert.
"""
from __future__ import annotations

import os
import shutil
import time
from typing import Any, Optional

CGROUP = "/sys/fs/cgroup"


# ------------------------------------------------------------------ Lesehilfen
def _read(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return None


def _read_int(path: str) -> Optional[int]:
    raw = _read(path)
    if raw is None:
        return None
    raw = raw.split()[0] if raw.split() else raw
    if raw in ("max", "-1"):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _read_keyed(path: str) -> dict[str, int]:
    """Dateien im Format 'schluessel wert' pro Zeile (cpu.stat, memory.stat)."""
    out: dict[str, int] = {}
    raw = _read(path)
    if not raw:
        return out
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            try:
                out[parts[0]] = int(parts[1])
            except ValueError:
                continue
    return out


# ------------------------------------------------------------------------ CPU
def cpu_quota() -> Optional[float]:
    """Dem Container zugeteilte Kerne (kann gebrochen sein, z.B. 1.5)."""
    raw = _read(f"{CGROUP}/cpu.max")                     # cgroup v2: "<quota> <period>"
    if raw:
        parts = raw.split()
        if len(parts) == 2 and parts[0] != "max":
            try:
                return int(parts[0]) / int(parts[1])
            except (ValueError, ZeroDivisionError):
                pass
    quota = _read_int(f"{CGROUP}/cpu/cpu.cfs_quota_us")   # cgroup v1
    period = _read_int(f"{CGROUP}/cpu/cpu.cfs_period_us")
    if quota and period:
        return quota / period
    return None


def cpu_count() -> Optional[int]:
    """Nutzbare Kerne. os.sched_getaffinity zaehlt die tatsaechlich erlaubten."""
    try:
        return len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return os.cpu_count()


def _cpu_usage_usec() -> Optional[int]:
    """Kumulierte CPU-Zeit des Containers in Mikrosekunden."""
    stat = _read_keyed(f"{CGROUP}/cpu.stat")             # cgroup v2
    if "usage_usec" in stat:
        return stat["usage_usec"]
    nanos = _read_int(f"{CGROUP}/cpuacct/cpuacct.usage")  # cgroup v1
    if nanos is not None:
        return nanos // 1000
    # Letzter Ausweg: /proc/stat (zeigt im LXC dank lxcfs meist Container-Werte)
    raw = _read("/proc/stat")
    if raw:
        for line in raw.splitlines():
            if line.startswith("cpu "):
                vals = [int(v) for v in line.split()[1:] if v.isdigit()]
                if len(vals) >= 4:
                    ticks = os.sysconf("SC_CLK_TCK") or 100
                    busy = sum(vals) - vals[3] - (vals[4] if len(vals) > 4 else 0)
                    return int(busy / ticks * 1_000_000)
    return None


# Letzter Messpunkt fuer die Differenzbildung. CPU-Auslastung ist immer ein
# Verhaeltnis ueber ein Zeitfenster - ein Einzelwert existiert nicht.
_last_sample: Optional[tuple[float, int]] = None


def cpu_percent(min_window: float = 0.12) -> Optional[float]:
    """Auslastung in Prozent seit der letzten Messung.

    Beim allerersten Aufruf wird kurz gemessen (min_window Sekunden), danach
    ist es die Differenz zum vorherigen Dashboard-Aufruf - das kostet nichts
    und ist ueber laengere Fenster sogar aussagekraeftiger.
    """
    global _last_sample
    usage = _cpu_usage_usec()
    if usage is None:
        return None
    now = time.monotonic()

    prev = _last_sample
    # Kein oder zu junger/zu alter Vergleichspunkt -> frisch messen
    if prev is None or (now - prev[0]) < min_window or (now - prev[0]) > 900:
        time.sleep(min_window)
        now2 = time.monotonic()
        usage2 = _cpu_usage_usec()
        if usage2 is None:
            return None
        prev, usage, now = (now, usage), usage2, now2

    # Messpunkt IMMER merken, auch wenn diesmal kein Wert herauskommt - sonst
    # faengt der naechste Aufruf wieder bei null an und misst erneut mit sleep().
    _last_sample = (now, usage)

    elapsed = now - prev[0]
    if elapsed <= 0:
        return None
    cores = cpu_quota() or cpu_count() or 1
    pct = (usage - prev[1]) / (elapsed * 1_000_000) / cores * 100
    return max(0.0, min(100.0, round(pct, 1)))


def load_average() -> Optional[tuple[float, float, float]]:
    raw = _read("/proc/loadavg")
    if not raw:
        try:
            return tuple(round(v, 2) for v in os.getloadavg())  # type: ignore[return-value]
        except (AttributeError, OSError):
            return None
    parts = raw.split()
    try:
        return (float(parts[0]), float(parts[1]), float(parts[2]))
    except (IndexError, ValueError):
        return None


# ------------------------------------------------------------------------ RAM
def memory() -> dict[str, Optional[int]]:
    """Speicher des Containers in Bytes: {'total', 'used'}.

    Wichtig: memory.current enthaelt auch den Dateicache. Den zieht man ab
    (wie es Docker/systemd tun), sonst sieht jeder Server nach kurzer Laufzeit
    zu 95% ausgelastet aus, obwohl der Cache jederzeit freigegeben wird.
    """
    total = _read_int(f"{CGROUP}/memory.max")
    current = _read_int(f"{CGROUP}/memory.current")
    if current is not None:
        stat = _read_keyed(f"{CGROUP}/memory.stat")
        used = current - stat.get("inactive_file", 0)
        if total is None:
            total = _meminfo_total()
        if total:
            return {"total": total, "used": max(0, min(used, total))}

    # cgroup v1
    total_v1 = _read_int(f"{CGROUP}/memory/memory.limit_in_bytes")
    used_v1 = _read_int(f"{CGROUP}/memory/memory.usage_in_bytes")
    if used_v1 is not None and total_v1 and total_v1 < (1 << 62):
        stat = _read_keyed(f"{CGROUP}/memory/memory.stat")
        return {"total": total_v1,
                "used": max(0, used_v1 - stat.get("total_inactive_file", 0))}

    # /proc/meminfo (im LXC via lxcfs meist bereits containerspezifisch)
    info = _meminfo()
    if info.get("MemTotal") and info.get("MemAvailable") is not None:
        return {"total": info["MemTotal"], "used": info["MemTotal"] - info["MemAvailable"]}
    return {"total": None, "used": None}


def _meminfo() -> dict[str, int]:
    out: dict[str, int] = {}
    raw = _read("/proc/meminfo")
    if not raw:
        return out
    for line in raw.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            out[key.strip()] = int(parts[0]) * 1024  # kB -> Bytes
    return out


def _meminfo_total() -> Optional[int]:
    return _meminfo().get("MemTotal")


def process_rss() -> Optional[int]:
    """Speicherverbrauch dieses Prozesses (der Bot selbst) in Bytes."""
    raw = _read("/proc/self/status")
    if raw:
        for line in raw.splitlines():
            if line.startswith("VmRSS:"):
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    return int(parts[1]) * 1024
    return None


# ----------------------------------------------------------------- Disk / Zeit
def disk(path: str) -> dict[str, Optional[int]]:
    try:
        usage = shutil.disk_usage(path)
        return {"total": usage.total, "used": usage.total - usage.free}
    except OSError:
        return {"total": None, "used": None}


def uptime_seconds() -> Optional[float]:
    raw = _read("/proc/uptime")
    if raw:
        try:
            return float(raw.split()[0])
        except (IndexError, ValueError):
            return None
    return None


def environment() -> dict[str, str]:
    """Laeuft die App im Container oder direkt auf dem Server?

    Nur fuer die Beschriftung - die Messung funktioniert in beiden Faellen.
    Im LXC ist /sys/fs/cgroup der Container-Namespace, dort stehen echte
    Limits. Direkt auf einem Ubuntu-Server hat das Root-Cgroup weder cpu.max
    noch memory.max, und die Werte gelten fuer die ganze Maschine.
    """
    marker = _read("/run/systemd/container")          # von systemd gesetzt
    if marker:
        names = {"lxc": "LXC-Container", "lxc-libvirt": "LXC-Container",
                 "docker": "Docker-Container", "podman": "Podman-Container",
                 "systemd-nspawn": "nspawn-Container"}
        return {"kind": "container",
                "label": names.get(marker.strip(), f"Container ({marker.strip()})")}
    if os.path.exists("/.dockerenv"):
        return {"kind": "container", "label": "Docker-Container"}
    env1 = _read("/proc/1/environ") or ""
    if "container=" in env1:
        return {"kind": "container", "label": "Container"}
    # Kein Marker, aber echte Limits im Root-Cgroup -> trotzdem begrenzt
    if cpu_quota() is not None or _read_int(f"{CGROUP}/memory.max") is not None:
        return {"kind": "container", "label": "Container (mit Limits)"}
    return {"kind": "host", "label": "Server"}


def _pct(used: Optional[int], total: Optional[int]) -> Optional[float]:
    if not total or used is None:
        return None
    return round(used / total * 100, 1)


def snapshot(data_path: str = ".", db_path: str = "") -> dict[str, Any]:
    """Alle Werte auf einen Schlag. Wirft nie."""
    try:
        mem = memory()
        dsk = disk(data_path)
        load = load_average()
        cores = cpu_quota() or cpu_count()
        env = environment()
        db_bytes = None
        if db_path:
            db_bytes = 0
            for suffix in ("", "-wal", "-shm"):  # SQLite im WAL-Modus
                try:
                    db_bytes += os.path.getsize(db_path + suffix)
                except OSError:
                    pass
        return {
            "ok": True,
            "env_kind": env["kind"], "env_label": env["label"],
            "cpu_percent": cpu_percent(),
            "cpu_cores": round(cores, 2) if cores else None,
            "load": list(load) if load else None,
            # Load ins Verhaeltnis zu den Kernen: >1.0 heisst Ueberlastung
            "load_ratio": (round(load[0] / cores, 2) if load and cores else None),
            "mem_used": mem["used"], "mem_total": mem["total"],
            "mem_percent": _pct(mem["used"], mem["total"]),
            "disk_used": dsk["used"], "disk_total": dsk["total"],
            "disk_percent": _pct(dsk["used"], dsk["total"]),
            "proc_rss": process_rss(),
            "db_bytes": db_bytes,
            "uptime": uptime_seconds(),
            "ts": time.time(),
        }
    except Exception as exc:  # noqa: BLE001 - eine Anzeige darf nie stoeren
        return {"ok": False, "error": str(exc), "ts": time.time()}


# ------------------------------------------------------------------ Formatierung
def human_bytes(value: Optional[float]) -> str:
    if value is None:
        return "–"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit in ("B", "KB") else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def human_uptime(seconds: Optional[float]) -> str:
    if not seconds:
        return "–"
    seconds = int(seconds)
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days} T {hours} Std"
    if hours:
        return f"{hours} Std {minutes} Min"
    return f"{minutes} Min"
