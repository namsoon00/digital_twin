"""Capabilities used only by static-seed identity."""

from typing import Dict, List, Protocol


class IdentityStore(Protocol):
    def base_schema_contract_metadata(self) -> Dict[str, str]: ...

    def seed_static_box_names(self) -> List[str]: ...

    def seed_static_manifest_entity_id(self) -> str: ...
