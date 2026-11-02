"""Read the short-lived compressed Manifest format without writing it again.

A running worker wrote this representation during a storage experiment. Keep
its immutable historical markers readable after rolling back the writer.
"""

import base64
import hashlib
import json
import zlib


_ARCHIVE_KEY = "_manifestIndexArchive"
_VERSION = "manifest-index-zlib-v1"
_INDEX_FIELDS = {"scopePlan", "nativeRulePlannerTopology", "nativeRuleEvidenceReadIndex"}
_MAX_DECODED_BYTES = 64 * 1024 * 1024


def restore_legacy_manifest_indices(properties: dict) -> dict:
    if _ARCHIVE_KEY not in properties:
        return dict(properties)
    archive = properties[_ARCHIVE_KEY]
    if (properties.get("tboxClass") != "WorldviewManifest"
            or not isinstance(archive, dict) or archive.get("version") != _VERSION):
        raise ValueError("Unsupported archived Manifest index format")
    size = archive.get("decodedBytes")
    data = archive.get("data")
    if (type(size) is not int or not 0 < size <= _MAX_DECODED_BYTES
            or not isinstance(data, str) or len(data) > (_MAX_DECODED_BYTES + 2) // 3 * 4 + 128):
        raise ValueError("Archived Manifest index exceeds its bounded size")
    try:
        compressed = base64.b64decode(data, validate=True)
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, size + 1)
        if (len(raw) != size or not decoder.eof
                or decoder.unconsumed_tail or decoder.unused_data
                or hashlib.sha256(raw).hexdigest() != archive.get("sha256")):
            raise ValueError("Archived Manifest index integrity mismatch")
        indices = json.loads(raw)
    except (ValueError, TypeError, zlib.error) as error:
        raise ValueError("Invalid archived Manifest indices") from error
    if (not isinstance(indices, dict) or set(indices) != _INDEX_FIELDS
            or not isinstance(indices["scopePlan"], list)
            or not isinstance(indices["nativeRulePlannerTopology"], dict)
            or not isinstance(indices["nativeRuleEvidenceReadIndex"], dict)):
        raise ValueError("Archived Manifest index fields do not match their contract")
    for field, value in indices.items():
        if field in properties and properties[field] != value:
            raise ValueError("Archived Manifest index conflicts with its expanded value")
    return {**{key: value for key, value in properties.items() if key != _ARCHIVE_KEY}, **indices}
