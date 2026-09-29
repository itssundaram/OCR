"""
DOCINT — Field Normalizer
Provides guardrails and standardization for field values extracted by OCR or LLM.
"""
import re
from datetime import datetime
from typing import Any


class FieldNormalizer:
    @staticmethod
    def normalize(value: str, field_type: str, known_values: list[str] = None) -> Any:
        """
        Normalizes a raw string value to the expected type.
        Applies known_values guardrails if provided.
        """
        if value is None:
            return None
            
        value = str(value).strip()
        if not value:
            return None

        # Guardrail: strict known_values match (case-insensitive)
        if known_values:
            lower_value = value.lower()
            for kv in known_values:
                if lower_value == kv.lower():
                    return kv  # Return exactly the expected case
            
        if field_type == "number":
            return FieldNormalizer.normalize_number(value)
        elif field_type == "date":
            return FieldNormalizer.normalize_date(value)
        elif field_type == "boolean":
            return FieldNormalizer.normalize_boolean(value)
            
        # String normalization
        # Collapse multiple spaces and newlines
        value = re.sub(r'\s+', ' ', value)
        return value

    @staticmethod
    def normalize_number(value: str) -> float | int | None:
        """Extracts the first valid number from a string, ignoring currency symbols and commas."""
        # Remove currency symbols and commas
        cleaned = re.sub(r'[^\d.\-]', '', value)
        # Handle multiple dots (e.g. "1.234.56" -> "1234.56")
        parts = cleaned.split('.')
        if len(parts) > 2:
            cleaned = "".join(parts[:-1]) + "." + parts[-1]
            
        if not cleaned or cleaned == "-" or cleaned == ".":
            return None
            
        try:
            return float(cleaned) if "." in cleaned else int(cleaned)
        except ValueError:
            return None

    @staticmethod
    def normalize_date(value: str) -> str | None:
        """Attempts to parse common date formats into ISO 8601 (YYYY-MM-DD)."""
        candidate = value.strip().split(" ")[0]  # Take first part to avoid times
        
        # 1. Look for DD-MMM-YYYY or DD-Month-YYYY
        match = re.search(r'(\d{1,2})[\s/-]+([A-Za-z]{3,})[\s/-]+(\d{2,4})', candidate)
        if match:
            day, month_str, year = match.groups()
            if len(year) == 2:
                year = f"20{year}" if int(year) < 50 else f"19{year}"
            month_map = {
                "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
                "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12"
            }
            month = month_map.get(month_str[:3].lower(), "01")
            return f"{year}-{month.zfill(2)}-{day.zfill(2)}"

        # 2. Look for YYYY-MM-DD or DD/MM/YYYY
        fmts = [
            "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%Y.%m.%d", "%d.%m.%Y"
        ]
        for fmt in fmts:
            try:
                dt = datetime.strptime(candidate, fmt)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue

        # Return the original string if we can't parse it
        return value

    @staticmethod
    def normalize_boolean(value: str) -> bool:
        v = str(value).lower().strip()
        return v in ("yes", "true", "1", "y", "t", "on")
