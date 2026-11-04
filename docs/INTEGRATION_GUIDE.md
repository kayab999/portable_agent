# Guía de Integración del Motor Linter (Kayab Portable)

> **Estado rc.4:** el linter **no está montado** en `launcher.py`, `gateway.py`
> ni `agent_loop.py`. No hay imports de `linter.py` en el path de producción.
> Este documento describe cómo integrarlo si se decide hacerlo; no es una
> capacidad activa del producto.

## Estructura

- `linter.py`: motor zero-dependency.
- `rules/`: definiciones de reglas en TOML.

## Integración propuesta

### 1. Inicialización

```python
from linter import PromptLinter

prompt_linter = PromptLinter(rules_path="./rules")
```

### 2. Pre-inferencia

```python
clean_prompt, diagnostics, fixes = prompt_linter.lint_and_fix(input_usuario)
```

### 3. Post-inferencia

```python
respuesta_final = prompt_linter.fix_syntax(respuesta_llm)
```

## Antes de anunciarlo

1. Añadir el hook real en `gateway._handle_agent_chat` o en el launcher.
2. Añadir tests de regresión para las reglas que bloquean entrada.
3. Medir latencia: el linter es regex, pero se ejecuta por turno.
4. Actualizar `RC_DOD.md` y `../README.md` solo cuando el hook exista.

## Beneficios (si se integra)

- **Determinismo**: estructura de prompt consistente.
- **Latencia**: regex instantáneo.
- **Seguridad**: puede bloquear patrones peligrosos antes del modelo.

## Limitaciones

- No es un sandbox ni un filtro de seguridad suficiente por sí solo.
- Las reglas TOML no están auditadas como parte de rc.4.
