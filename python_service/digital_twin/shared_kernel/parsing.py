from typing import Dict, Optional


def parse_assignments(raw: str, defaults: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    values = dict(defaults or {})
    for line in str(raw or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        separator = "=" if "=" in stripped else ":" if ":" in stripped else "," if "," in stripped else ""
        if not separator:
            continue
        key, raw_value = stripped.split(separator, 1)
        key = key.strip()
        if not key.replace("_", "").isalnum() or key[0].isdigit():
            continue
        try:
            values[key] = float(raw_value.strip())
        except ValueError:
            values[key] = 0.0
    return values

