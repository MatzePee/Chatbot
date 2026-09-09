"""Short maintenance phase while the new server process is checked."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pending():
    return (ROOT / 'data/deployment-pending').exists()


def status():
    try:
        data = json.loads((ROOT / 'data/update-status.json').read_text())
        return {key: data.get(key) for key in ('phase', 'message', 'tag', 'updated_at', 'backup')}
    except (OSError, ValueError):
        return {'phase': 'idle', 'message': ''}
