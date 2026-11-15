"""Self-contained UTF-8 context packs and authenticated prefix deltas."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import takewhile
from typing import Any

_PACK_SCHEMA = "powercontext.context-pack.v1"
_DELTA_SCHEMA = "powercontext.context-pack-delta.v1"


class ContextPackError(ValueError):
    """A malformed context wire value, chunk or reconstruction."""


def _json(value: Mapping[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContextPackError("Duplicate wire field")  # noqa: TRY003
        result[key] = value
    return result


def _payload(raw: bytes, schema: str, fields: set[str]) -> dict[str, Any]:
    try:
        if not isinstance(raw, bytes):
            raise ContextPackError("Wire input must be bytes")  # noqa: TRY003
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object)
    except (ValueError, UnicodeError) as error:
        raise ContextPackError("Invalid UTF-8 JSON") from error  # noqa: TRY003
    if not isinstance(value, dict) or set(value) != fields or value.get("schema") != schema:
        raise ContextPackError("Unexpected wire schema or fields")  # noqa: TRY003
    return value


def _validate_items(items: tuple[bytes, ...]) -> None:
    if not isinstance(items, tuple) or any(not isinstance(item, bytes) for item in items):
        raise ContextPackError("Chunks must be a tuple of bytes")  # noqa: TRY003
    try:
        for item in items:
            item.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise ContextPackError("Each chunk must be valid UTF-8") from error  # noqa: TRY003


def _encode(items: tuple[bytes, ...]) -> list[str]:
    return [base64.b64encode(item).decode("ascii") for item in items]


def _decode(value: object) -> tuple[bytes, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ContextPackError("Wire chunks must be a Base64 string array")  # noqa: TRY003
    try:
        result = tuple(base64.b64decode(item, validate=True) for item in value)
    except (ValueError, binascii.Error) as error:
        raise ContextPackError("Invalid Base64 chunk") from error  # noqa: TRY003
    if _encode(result) != value:
        raise ContextPackError("Base64 must use canonical standard encoding")  # noqa: TRY003
    _validate_items(result)
    return result


def _digest(value: object) -> None:
    if not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise ContextPackError("Invalid SHA-256 digest")  # noqa: TRY003


@dataclass(frozen=True, slots=True)
class ContextPack:
    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    def __post_init__(self) -> None:
        _validate_items(self.items)
        expected = "sha256:" + hashlib.sha256(_json({"schema": _PACK_SCHEMA, "items": _encode(self.items)})).hexdigest()
        if self.digest != expected:
            raise ContextPackError("Pack digest does not match its chunks")  # noqa: TRY003
        if type(self.byte_count) is not int or self.byte_count != len(self.to_bytes()):
            raise ContextPackError("Pack byte_count does not match its serialized size")  # noqa: TRY003

    @classmethod
    def build(cls, items: tuple[bytes, ...]) -> ContextPack:
        _validate_items(items)
        payload = {"schema": _PACK_SCHEMA, "items": _encode(items)}
        digest = "sha256:" + hashlib.sha256(_json(payload)).hexdigest()
        byte_count = 0
        while True:
            size = len(_json({**payload, "digest": digest, "byte_count": byte_count}))
            if size == byte_count:
                break
            byte_count = size
        return cls(items, digest, byte_count)

    @classmethod
    def from_content(cls, content: str | bytes | None, *, chunk_bytes: int = 512) -> ContextPack:
        if type(chunk_bytes) is not int or chunk_bytes <= 0:
            raise ContextPackError("chunk_bytes must be a positive integer")  # noqa: TRY003
        try:
            raw = content.encode("utf-8") if isinstance(content, str) else b"" if content is None else content
            if not isinstance(raw, bytes):
                raise ContextPackError("Content must be UTF-8 bytes or text")  # noqa: TRY003
            raw.decode("utf-8", errors="strict")
        except UnicodeError as error:
            raise ContextPackError("Content must be valid UTF-8") from error  # noqa: TRY003
        items = []
        start = 0
        while start < len(raw):
            end = min(start + chunk_bytes, len(raw))
            while end < len(raw) and end > start and raw[end] & 0xC0 == 0x80:
                end -= 1
            if end == start:
                raise ContextPackError("UTF-8 character exceeds chunk_bytes")  # noqa: TRY003
            # Prefer line boundaries so an appended context entry does not
            # change the preceding entry's last, partially filled chunk.
            newline = raw.rfind(b"\n", start, end)
            if newline >= start:
                end = newline + 1
            items.append(raw[start:end])
            start = end
        return cls.build(tuple(items))

    def to_bytes(self) -> bytes:
        return _json({
            "schema": _PACK_SCHEMA,
            "items": _encode(self.items),
            "digest": self.digest,
            "byte_count": self.byte_count,
        })

    @classmethod
    def from_bytes(cls, raw: bytes) -> ContextPack:
        payload = _payload(raw, _PACK_SCHEMA, {"schema", "items", "digest", "byte_count"})
        pack = cls(_decode(payload["items"]), payload["digest"], payload["byte_count"])
        if len(raw) != pack.byte_count:
            raise ContextPackError("Received pack length does not match byte_count")  # noqa: TRY003
        return pack


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    def __post_init__(self) -> None:
        _digest(self.base_digest)
        _digest(self.target_digest)
        if type(self.prefix_items) is not int or self.prefix_items < 0:
            raise ContextPackError("prefix_items must be a non-negative integer")  # noqa: TRY003
        _validate_items(self.appended)

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        base.__post_init__()
        target.__post_init__()
        prefix = sum(1 for _ in takewhile(lambda pair: pair[0] == pair[1], zip(base.items, target.items, strict=False)))
        return cls(base.digest, target.digest, prefix, target.items[prefix:])

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        return _json({
            "schema": _DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": _encode(self.appended),
        })

    @classmethod
    def from_bytes(cls, raw: bytes) -> ContextPackDelta:
        payload = _payload(raw, _DELTA_SCHEMA, {"schema", "base_digest", "target_digest", "prefix_items", "appended"})
        return cls(
            payload["base_digest"], payload["target_digest"], payload["prefix_items"], _decode(payload["appended"])
        )


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    base.__post_init__()
    delta.__post_init__()
    if base.digest != delta.base_digest or delta.prefix_items > len(base.items):
        raise ContextPackError("Delta base digest or prefix does not match")  # noqa: TRY003
    target = ContextPack.build(base.items[: delta.prefix_items] + delta.appended)
    if target.digest != delta.target_digest:
        raise ContextPackError("Reconstructed target digest does not match")  # noqa: TRY003
    return target


__all__ = ["ContextPack", "ContextPackDelta", "ContextPackError", "apply_context_pack_delta"]
