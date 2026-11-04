import re
import hashlib
from dataclasses import dataclass
from typing import List, Tuple

@dataclass
class MaskResult:
    clean_text: str
    is_valid: bool
    violations: List[str]
    redacted_types: List[str]
    integrity_hash: str

class MaskService:
    """
    Servicio de Sanitización y Redacción de PII (Personal Identifiable Information).
    """
    PII_PATTERNS = {
        "email": r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+",
        "phone": r"\+?\d{1,3}[-.\\s]?\(?\d{1,3}\)?[-.\\s]?\d{3,4}[-.\\s]?\d{4}",
        "credit_card": r"\b(?:\d[ -]*?){13,16}\b",
    }

    def sanitize(self, text: str) -> Tuple[str, List[str]]:
        # 1. Eliminar tags de razonamiento interno
        clean_text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        
        # 2. Redactar PII
        redacted_types = []
        for pii_type, pattern in self.PII_PATTERNS.items():
            if re.search(pattern, clean_text):
                redacted_types.append(pii_type)
                clean_text = re.sub(pattern, f"[REDACTED_{pii_type.upper()}]", clean_text)
        
        return clean_text, redacted_types

class SpecValidator:
    """
    Validador de estructura y densidad de la respuesta.
    """
    def __init__(self, required_sections: List[str] = None):
        self.required_sections = required_sections or ["Context", "Task", "Format"]

    def validate(self, text: str) -> Tuple[bool, List[str]]:
        violations = []
        text_lower = text.lower()
        
        # 1. Verificar secciones obligatorias
        for section in self.required_sections:
            if section.lower() not in text_lower:
                violations.append(f"Falta sección obligatoria: <{section}>")
            else:
                # 2. Verificar densidad (mínimo 5 palabras por sección)
                pattern = rf"<{section}>(.*?)</{section}>"
                match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
                if match:
                    content = match.group(1).strip()
                    if len(content.split()) < 5:
                        violations.append(f"Sección <{section}> demasiado pobre o vacía")
        
        return len(violations) == 0, violations

class MaskMachine:
    """
    Output governor: PII sanitization + optional structure checks.

    integrity_hash is a non-cryptographic content fingerprint for debugging only.
    It is NOT an authenticity proof (no secret MAC, no trust boundary).
    """
    def __init__(self, system_key: str | None = None):
        import os

        self.mask_service = MaskService()
        self.spec_validator = SpecValidator()
        # Prefer env; never use a hardcoded production secret
        self.system_key = system_key or os.environ.get(
            "BONSAI_MASK_KEY", "bonsai-dev-fingerprint-only"
        )

    def apply_mask(self, raw_output: str, flavor: str = "technical") -> MaskResult:
        clean_text, redacted = self.mask_service.sanitize(raw_output)
        is_valid, violations = self.spec_validator.validate(clean_text)
        # Content fingerprint only (not a security control)
        integrity_payload = f"{clean_text}|{flavor}"
        integrity_hash = hashlib.sha256(integrity_payload.encode()).hexdigest()

        return MaskResult(
            clean_text=clean_text,
            is_valid=is_valid,
            violations=violations,
            redacted_types=redacted,
            integrity_hash=integrity_hash,
        )

    def get_signature_block(self, result: MaskResult) -> str:
        """Debug fingerprint block — not a cryptographic signature."""
        sig = "\n\n--- BONSAI OUTPUT FINGERPRINT ---\n"
        sig += f"Structure: {'ok' if result.is_valid else 'incomplete'}\n"
        if result.redacted_types:
            sig += f"PII redacted: {', '.join(result.redacted_types)}\n"
        sig += f"Fingerprint: {result.integrity_hash[:16]}...\n"
        sig += "--- END ---\n"
        return sig
