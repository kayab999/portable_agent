import re
import tomllib
from pathlib import Path
from dataclasses import dataclass
from enum import Enum
from typing import List, Dict, Tuple, Optional, Callable

# --- ENUMS INTERNOS (Independencia Total) ---
class RuleSeverity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

class RuleResult(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"

@dataclass
class Diagnostic:
    code: str
    category: str
    message: str
    severity: str  # 'error', 'warning', 'info'
    fixable: bool
    details: Optional[str] = None

# --- AUTOFIXER (Ruta Rápida) ---
class AutoFixer:
    """
    Aplica arreglos deterministas (Regex) a los prompts sin usar LLM.
    """
    def __init__(self):
        self.fix_registry: Dict[str, Callable[[str], str]] = {
            "W002": self._fix_filler_words,
            "S002": self._fix_unclosed_tags,
            "R001": self._fix_think_tags,
            "R006": self._fix_model_claims
        }

    def fix(self, prompt: str, diagnostics: List[Diagnostic]) -> Tuple[str, List[str]]:
        current_prompt = prompt
        applied_fixes = []
        active_codes = {d.code for d in diagnostics if d.fixable}

        for code, fixer_func in self.fix_registry.items():
            if code in active_codes:
                new_prompt = fixer_func(current_prompt)
                if new_prompt != current_prompt:
                    current_prompt = new_prompt
                    applied_fixes.append(code)

        return current_prompt, applied_fixes

    def _fix_filler_words(self, prompt: str) -> str:
        fillers = [
            r'Please\s+', r'I want you to\s+', r'Could you\s+', r'If possible,\s+', 
            r'I need a\s+', r'Can you help me\s+', r'Por favor,\s+', r'Quisiera que\s+'
        ]
        cleaned = prompt
        for pattern in fillers:
            cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
        if cleaned:
            cleaned = cleaned[0].upper() + cleaned[1:]
        return cleaned.strip()

    def _fix_unclosed_tags(self, prompt: str) -> str:
        tags = re.findall(r'<([a-zA-Z0-9_-]+)>', prompt)
        closing_tags = re.findall(r'</([a-zA-Z0-9_-]+)>', prompt)
        fixed_prompt = prompt
        for tag in tags:
            if tag not in closing_tags:
                 if f"</{tag}>".format(tag) not in prompt:
                     fixed_prompt += f"\n</{tag}>".format(tag)
        return fixed_prompt

    def _fix_think_tags(self, prompt: str) -> str:
        return re.sub(r'<think>.*?</think>|<reasoning>.*?</reasoning>', '', prompt, flags=re.DOTALL).strip()

    def _fix_model_claims(self, prompt: str) -> str:
        pattern = r"(?i)(i have succeeded|here is the refined|successfully refined|he refinado con éxito)"
        return re.sub(pattern, '', prompt).strip()

# --- PROMPT LINTER (Motor de Reglas) ---
class PromptLinter:
    """
    Linter de Prompts para Kayab Agente Portable.
    """
    REQUIRED_SECTIONS = ['Context', 'Task', 'Format']
    
    def __init__(self, rules_path: str = "./rules"):
        self.rules_path = Path(rules_path)
        self.fixer = AutoFixer()
        self.active_rules = self._load_all_rules()

    def _load_all_rules(self) -> List[Dict]:
        rules = []
        base_file = self.rules_path / "base.toml"
        if base_file.exists():
            with open(base_file, "rb") as f:
                data = tomllib.load(f)
                rules.extend(data.get("rules", []))
        return rules

    def lint(self, prompt: str) -> List[Diagnostic]:
        diagnostics = []
        # 1. Reglas Estáticas de Código
        diagnostics.extend(self._check_structure(prompt))
        diagnostics.extend(self._check_filler(prompt))
        diagnostics.extend(self._check_tag_matching(prompt))
        
        # 2. Reglas Cargadas de TOML (Regex)
        for rule in self.active_rules:
            if rule.get("check_type") == "regex":
                if re.search(rule["pattern"], prompt):
                    diagnostics.append(Diagnostic(
                        code=rule["id"][:4],
                        category="RuleEngine",
                        message=rule["description"],
                        severity=rule["severity"].lower(),
                        fixable=rule["id"] in ["R001_NO_THINK_TAG", "R006_MODEL_DECLARED_SUCCESS"]
                    ))
        return diagnostics

    def lint_and_fix(self, prompt: str) -> Tuple[str, List[Diagnostic], List[str]]:
        """Limpia el prompt antes de enviarlo al modelo."""
        diagnostics = self.lint(prompt)
        fixed_prompt, applied_fixes = self.fixer.fix(prompt, diagnostics)
        return fixed_prompt, diagnostics, applied_fixes

    def fix_syntax(self, response: str) -> str:
        """Limpia la respuesta del modelo (etiquetas, fillers)."""
        # Reutilizamos el AutoFixer para asegurar que la respuesta sea pura
        clean_resp = self.fixer._fix_unclosed_tags(response)
        clean_resp = self.fixer._fix_think_tags(clean_resp)
        return clean_resp

    def _check_structure(self, prompt: str) -> List[Diagnostic]:
        diagnostics = []
        # Omitir validación de estructura para mensajes cortos (naturales)
        if len(prompt.split()) < 10:
            return diagnostics
        prompt_lower = prompt.lower()
        missing = [s for s in self.REQUIRED_SECTIONS if s.lower() not in prompt_lower]
        if missing:
            diagnostics.append(Diagnostic(
                code="S001", category="Structure",
                message=f'Faltan secciones obligatorias: {", ".join(missing)}.',
                severity="error", fixable=False
            ))
        return diagnostics

    def _check_filler(self, prompt: str) -> List[Diagnostic]:
        diagnostics = []
        fillers = ['please', 'could you', 'por favor', 'si puedes']
        prompt_lower = prompt.lower()
        found = [f for f in fillers if f in prompt_lower]
        if found:
            diagnostics.append(Diagnostic(
                code="W002", category="Weakness",
                message=f'Relleno detectado: "{found[0]}".',
                severity="info", fixable=True
            ))
        return diagnostics

    def _check_tag_matching(self, prompt: str) -> List[Diagnostic]:
        diagnostics = []
        tags = re.findall(r'<(/?[a-zA-Z0-9_-]+)>', prompt)
        stack = []
        for tag in tags:
            if not tag.startswith('/'):
                stack.append(tag)
            elif stack and stack[-1] == tag[1:]:
                stack.pop()
            else:
                diagnostics.append(Diagnostic(
                    code="S003", category="Structure",
                    message=f'Tag mal anidado o huérfano: <{tag}>',
                    severity="error", fixable=False
                ))
        for leftover in stack:
            diagnostics.append(Diagnostic(
                code="S002", category="Structure",
                message=f'Tag sin cerrar: <{leftover}>',
                severity="error", fixable=True
            ))
        return diagnostics
