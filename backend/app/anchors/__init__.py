"""Shared anchor logic: resolver (read a value from a verified box) and store (merge-safe saves)."""
from app.anchors.resolver import (  # noqa: F401
    AnchorReading, find_label_near, resolve_anchor, value_shape,
    words_from_detections, words_from_lines,
)
from app.anchors.store import (  # noqa: F401
    AnchorConflict, merge_anchor_fields, remove_anchor_field, rollback,
)
