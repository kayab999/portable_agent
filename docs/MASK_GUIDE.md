# Guía de Integración: Mask Machine (Output Profiles)

La Mask Machine aplica perfiles de salida y redacta PII (emails, teléfonos).
**No** firma criptográficamente la respuesta: `integrity_hash` es un fingerprint
SHA-256 de depuración, no un HMAC ni una firma verificable.

## Uso actual

El gateway ya aplica `MaskService().sanitize()` a la respuesta de `/api/chat`
cuando `mask_outputs` está activo (default). No hace falta montarlo a mano.

```python
from mask_machine import MaskService

clean_text, redacted = MaskService().sanitize(respuesta_llm)
```

## Fingerprint de integridad

```python
result = MaskService().apply_mask(respuesta_llm, flavor="technical")
print(result.clean_text)
print(result.integrity_hash)          # fingerprint, no firma
print(mm.get_signature_block(result)) # bloque informativo de debug
```

## Beneficios

- **Privacidad**: redacción automática de datos sensibles.
- **Rigor**: validación de secciones y densidad.
- **Auditoría local**: fingerprint reproducible para comparar respuestas.

## Limitaciones

- El fingerprint no prueba autenticidad ni autoría.
- No sustituye TLS, firmas HMAC, ni un control de integridad del binario.
- Si necesitas una firma real, añade HMAC con una clave del vault en una
  release posterior.
