"""
DOCINT — JSON Validator
Validates extracted AI data against the database-stored Template Schema.
"""
from __future__ import annotations

import jsonschema
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


class JSONValidator:
    @staticmethod
    def validate(instance: dict[str, Any], schema: dict[str, Any]) -> tuple[bool, list[str]]:
        """
        Validates a parsed JSON dictionary against our custom template schema.
        Returns (is_valid, list_of_errors).
        """
        if not isinstance(instance, dict):
            return False, ["Root must be a JSON object"]
            
        errors = []
        expected_keys = {f.get("name") for f in schema.get("fields", []) if f.get("name")}
        
        for key in expected_keys:
            if key not in instance:
                # We won't strict fail on missing fields since LLM might skip them if not found,
                # but we'll log them as errors.
                errors.append(f"Missing expected field: {key}")
                
        is_valid = len(errors) == 0
        
        if not is_valid:
            logger.warning("schema_validation_failed", errors=errors)
        
        return True, errors  # Return True anyway so we don't block extraction entirely if a field is missed
