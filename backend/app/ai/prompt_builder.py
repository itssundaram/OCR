"""
DOCINT — Prompt Builder
Constructs the optimized prompts for the LLM to extract JSON.
"""
from __future__ import annotations

import json
from typing import Any

from app.ocr.base import OCRResult


class PromptBuilder:
    @staticmethod
    def build_system_prompt(schema: dict[str, Any], extraction_instructions: str | None = None) -> str:
        """
        Builds a generic system prompt for any document type.
        Document-type-specific guidance is injected via `extraction_instructions`,
        which comes from the Template.extraction_instructions DB column — not hardcoded here.
        """
        expected_format = {}
        for field in schema.get("fields", []):
            field_name = field.get("name", "unknown")
            field_type = field.get("type", "text")
            desc = field.get("description", "")
            aliases = field.get("aliases", [])
            known_values = field.get("known_values", [])
            hint = field.get("extraction_hint", "")

            field_def = f"<{field_type}>"
            if desc: field_def += f" - {desc}"
            if aliases: field_def += f" (Also known as: {', '.join(aliases)})"
            if known_values: field_def += f" (MUST BE ONE OF: {', '.join(known_values)})"
            if hint: field_def += f" (Hint pattern: {hint})"

            expected_format[field_name] = field_def

        schema_str = json.dumps(expected_format, indent=2)

        # Separate text fields from table fields to build a concrete output example
        text_fields = [f for f in schema.get("fields", []) if f.get("type", "text") != "table"]
        table_fields = [f for f in schema.get("fields", []) if f.get("type", "text") == "table"]

        example_output: dict = {}
        for field in text_fields:
            example_output[field["name"]] = "<extracted string or null>"
        for field in table_fields:
            cols = field.get("columns", []) or []
            example_row = {c: "<value>" for c in cols} if cols else {"column_a": "<value>", "column_b": "<value>"}
            example_output[field["name"]] = [example_row, {"...": "ALL remaining rows"}]

        example_str = json.dumps(example_output, indent=2)

        # Build the template-specific instructions block (injected, not hardcoded)
        custom_instructions_block = ""
        if extraction_instructions and extraction_instructions.strip():
            custom_instructions_block = (
                "=== DOCUMENT-TYPE INSTRUCTIONS ===\n"
                f"{extraction_instructions.strip()}\n\n"
            )

        return (
            "You are an expert Document Intelligence AI. Extract structured data from the "
            "provided document and return it EXCLUSIVELY as a single valid JSON object.\n\n"
            "=== SCHEMA (fields you must extract) ===\n"
            f"{schema_str}\n\n"
            "=== EXACT OUTPUT FORMAT ===\n"
            f"{example_str}\n\n"
            f"{custom_instructions_block}"
            "=== RULES (follow strictly) ===\n"
            "RULE 1 — JSON only: Output ONLY the JSON object. No markdown fences, no explanation.\n"
            "RULE 2 — Unknown fields: If a field is not found, output null for text fields "
            "and [] for table fields.\n"
            "RULE 3 — Text fields (non-table): Extract a value ONLY if the field appears as a "
            "standalone labeled field in the document (e.g. 'Field Label: value'). "
            "If the value exists ONLY inside a table column and NOT as a separate labeled item, "
            "set it to null.\n"
            "RULE 4 — Table fields: Extract EVERY SINGLE ROW from the table — not just one. "
            "Return a JSON array where each element is one row as a flat object. "
            "Include all rows visible in the document. Missing rows = incorrect output.\n"
            "RULE 5 — OCR correction: Fix obvious OCR character errors based on context.\n"
            "RULE 6 — All pages: Scan every page before marking a field as null.\n"
        )

    @staticmethod
    def build_user_prompt(ocr_result: OCRResult, rag_context: str | None = None, include_text: bool = True) -> str:
        """
        Builds the user prompt containing the raw OCR text.
        If rag_context is provided (pre-filtered by RAG), use that instead of the full document.
        If include_text is False, omits text (used when relying solely on Vision/images).
        """
        if not include_text:
            return (
                "Extract the required information from the provided document images.\n\n"
                "Remember: Output ONLY valid JSON matching the schema. "
                "For table fields, include EVERY row — do not stop after the first row."
            )

        if rag_context:
            # RAG mode: use pre-retrieved relevant chunks only
            text_content = rag_context
            footer = (
                "\nNOTE: The text above is the most relevant sections retrieved from the document. "
                "Extract all matching fields from this context."
            )
        else:
            # Full-document mode: include all pages with labels
            parts = []
            for page in ocr_result.pages:
                parts.append(f"=== PAGE {page.page_number} ===\n{page.full_text}")
            text_content = "\n\n".join(parts)
            footer = ""

        # Count table rows across all pages so we can warn the LLM
        total_table_rows = sum(
            len(t.rows)
            for page in ocr_result.pages
            for t in (page.tables or [])
        )
        # Also count approximate data rows from OCR lines that look like table rows
        # (lines containing '|' are likely table rows from the raw OCR)
        ocr_line_table_rows = sum(
            1
            for page in ocr_result.pages
            for line in (page.lines or [])
            if "|" in line.text and any(c.isdigit() for c in line.text)
        )
        likely_rows = max(total_table_rows, ocr_line_table_rows // 2)

        row_hint = ""
        if likely_rows > 1:
            row_hint = (
                f"\n\nIMPORTANT: The document table appears to contain approximately {likely_rows} data rows. "
                f"Your output for table fields MUST contain ALL {likely_rows} rows as separate objects in the array. "
                "Do not stop after the first row. Do not omit any row."
            )

        return (
            "Extract the required information from the following document text:\n\n"
            f"<document_text>\n{text_content}\n</document_text>"
            f"{footer}{row_hint}\n\n"
            "Remember: Output ONLY valid JSON. For table fields, return ALL rows as a complete array."
        )

