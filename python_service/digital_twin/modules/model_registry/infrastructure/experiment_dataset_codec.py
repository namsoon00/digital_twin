"""Lossless, bounded storage of immutable experimental input packets."""

import base64
import binascii
import hashlib
import json
import zlib

from digital_twin.modules.model_registry.domain.experiment_observations import validate_dataset


MAX_STORED_BYTES = 2_000_000
MAX_EXPANDED_BYTES = 32_000_000
ENCODING = "experiment-dataset-zlib-v1"


def encode_dataset(dataset):
    raw = json.dumps(dataset, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_EXPANDED_BYTES:
        raise ValueError("experiment-packet-expanded-size-limit")
    envelope = {"encoding": ENCODING, "sha256": hashlib.sha256(raw).hexdigest(),
                "data": base64.b64encode(zlib.compress(raw)).decode("ascii")}
    encoded = json.dumps(envelope, separators=(",", ":"))
    if len(encoded) > MAX_STORED_BYTES:
        raise ValueError("experiment-packet-size-limit")
    return encoded


def decode_dataset(value):
    try:
        envelope = json.loads(value) if isinstance(value, str) else value
        if not isinstance(envelope, dict):
            raise ValueError("experiment-packet-invalid")
        if "encoding" not in envelope:
            return validate_dataset(envelope)
        if envelope["encoding"] != ENCODING or len(envelope.get("data", "")) > MAX_STORED_BYTES:
            raise ValueError("experiment-packet-encoding-invalid")
        compressed = base64.b64decode(envelope["data"], validate=True)
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, MAX_EXPANDED_BYTES + 1)
        if len(raw) > MAX_EXPANDED_BYTES or not decoder.eof or decoder.unused_data:
            raise ValueError("experiment-packet-expanded-size-limit")
        if hashlib.sha256(raw).hexdigest() != envelope.get("sha256"):
            raise ValueError("experiment-packet-content-mismatch")
        return validate_dataset(json.loads(raw))
    except (TypeError, KeyError, binascii.Error, zlib.error, UnicodeError) as error:
        raise ValueError("experiment-packet-invalid") from error
