"""One explicit action authorization contract from graph readback to AI."""

from dataclasses import dataclass
from typing import Iterable, Mapping, Tuple


def _actions(values: object) -> Tuple[str, ...]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        return ()
    return tuple(dict.fromkeys(
        str(value or "").strip().upper() for value in values
        if str(value or "").strip()
    ))


@dataclass(frozen=True)
class GraphActionAuthorization:
    allowed: Tuple[str, ...] = ()
    blocked: Tuple[str, ...] = ()
    conflicts: Tuple[str, ...] = ()

    def allows(self, action: object) -> bool:
        code = str(action or "").strip().upper()
        return bool(code and code != "NO_ACTION" and code in self.allowed and code not in self.blocked)

    @classmethod
    def from_sources(cls, *sources: Mapping[str, object]):
        """First explicit authorization wins, including an empty deny-all list.

        The graph's AI envelope is narrower than its general policy envelope.
        Missing fields may use a compatibility source; explicit denial may not.
        """
        allowed = None
        blocked = []
        for source in sources:
            if not isinstance(source, Mapping):
                continue
            if allowed is None:
                for key in ("aiAllowedActions", "allowed_actions", "allowedActions"):
                    if key in source:
                        allowed = _actions(source[key])
                        break
            for key in ("blocked_actions", "blockedActions"):
                blocked.extend(_actions(source.get(key)))
        denied = _actions(blocked)
        return cls(
            tuple(action for action in allowed or () if action not in denied),
            denied,
            tuple(action for action in allowed or () if action in denied),
        )

    @classmethod
    def from_actions(cls, allowed: Iterable[str], blocked: Iterable[str] = ()):
        return cls.from_sources({"allowedActions": list(allowed or ()), "blockedActions": list(blocked or ())})
