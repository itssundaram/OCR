"""
DOCINT — Response Builder
Constructs the fixed response envelope from extracted field data.
Used by both the direct-match path and the LLM-assisted path.
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


class ResponseBuilder:
    """
    Builds the standardised extraction response envelope.

    Response envelope format:
    {
        "success": true,
        "department": "FINANCE",
        "doc_type": "INVOICE",
        "template_version": 3,
        "extraction_method": "direct" | "llm",
        "confidence": 0.87,
        "pages_processed": 3,
        "fields": {
            "invoice_number": {"value": "INV-001", "confidence": 0.95, "found": true},
            ...
        },
        "tables": [
            {
                "table_name": "line_items",
                "rows": [ {...}, ... ]
            }
        ]
    }
    """

    @staticmethod
    def build(
        match_results: dict[str, Any],
        schema: dict[str, Any],
        method: str,
        department: str,
        doc_type: str,
        template_version: int,
        pages_processed: int,
        overall_confidence: float,
        ocr_method: str = "unknown",
        llm_tables: list[dict[str, Any]] | None = None,
        table_results: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Build the standard response envelope.

        Args:
            match_results:     Per-field {value, confidence, found} from FieldMatcher or LLM.
            schema:            Template JSON (fields list).
            method:            "direct" or "llm".
            department:        Department slug.
            doc_type:          Document type code.
            template_version:  Version of the template used.
            pages_processed:   Number of pages that were processed.
            overall_confidence: Overall confidence score (0.0-1.0).
            llm_tables:        Table data extracted by the LLM (if any).
        """
        fields_out: dict[str, Any] = {}
        tables_out: list[dict[str, Any]] = []

        fields = schema.get("fields", [])
        for field in fields:
            name = field.get("name", "")
            field_type = field.get("type", "text")

            if field_type == "table":
                # Tables come from LLM extraction or TableMatcher
                table_data = None
                if table_results and name in table_results:
                    table_data = table_results[name]
                elif llm_tables:
                    for t in llm_tables:
                        if t.get("table_name") == name:
                            table_data = t
                            break
                            
                if table_data and table_data.get("rows"):
                    tables_out.append(table_data)
                else:
                    tables_out.append({
                        "table_name": name,
                        "rows": None,
                    })
            else:
                field_result = match_results.get(name, {})
                fields_out[name] = {
                    "value": field_result.get("value"),
                    "confidence": round(float(field_result.get("confidence", 0.0)), 4),
                    "found": bool(field_result.get("found", False)),
                    "match_strategy": field_result.get("match_strategy"),
                    "source_page": field_result.get("source_page"),
                }

        return {
            "success": True,
            "department": department.upper(),
            "doc_type": doc_type.upper(),
            "template_version": template_version,
            "extraction_method": method,
            "ocr_method": ocr_method,
            "confidence": round(overall_confidence, 4),
            "pages_processed": pages_processed,
            "fields": fields_out,
            "tables": tables_out,
        }

    @staticmethod
    def from_llm_json(
        llm_data: dict[str, Any],
        schema: dict[str, Any],
        department: str,
        doc_type: str,
        template_version: int,
        pages_processed: int,
        overall_confidence: float,
        ocr_method: str = "unknown",
        field_confidences: list[Any] | None = None,
    ) -> dict[str, Any]:
        """
        Build response envelope from raw LLM-extracted JSON.
        LLM is expected to return {field_name: value, ...} and optionally
        {table_name: [{col: val}, ...]} for table fields.
        """
        from app.ai.field_normalizer import FieldNormalizer
        fields = schema.get("fields", [])
        
        # Map field confidences for quick lookup
        conf_map = {}
        if field_confidences:
            for fc in field_confidences:
                conf_map[fc.field_name] = fc.confidence_score
                
        match_results: dict[str, Any] = {}
        llm_tables: list[dict[str, Any]] = []

        for field in fields:
            name = field.get("name", "")
            field_type = field.get("type", "text")

            if field_type == "table":
                raw_table = llm_data.get(name)
                llm_tables.append({
                    "table_name": name,
                    "rows": raw_table if isinstance(raw_table, list) and len(raw_table) > 0 else None,
                })
            else:
                raw_value = llm_data.get(name)
                # Apply normalization/guardrails on LLM output
                known_values = field.get("known_values", [])
                normalized_value = FieldNormalizer.normalize(raw_value, field_type, known_values)
                
                field_conf = conf_map.get(name, overall_confidence) if normalized_value is not None else 0.0
                
                match_results[name] = {
                    "value": normalized_value,
                    "confidence": field_conf,
                    "found": normalized_value is not None,
                    "match_strategy": "llm",
                    "source_page": None,
                }

        return ResponseBuilder.build(
            match_results=match_results,
            schema=schema,
            method="llm",
            department=department,
            doc_type=doc_type,
            template_version=template_version,
            pages_processed=pages_processed,
            overall_confidence=overall_confidence,
            ocr_method=ocr_method,
            llm_tables=llm_tables,
        )
