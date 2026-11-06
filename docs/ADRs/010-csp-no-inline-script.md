# ADR 010 — CSP forbids inline script

Status: accepted

## Decision
Extract UI JavaScript and CSS into `/app.js` and `/styles.css`, replace inline
event handlers with delegated `data-action` listeners, and serve
`script-src 'self'`.

## Rationale
Inline scripts are the main XSS amplifier in a single-page UI. External assets
plus delegation remove that class of injection even if a rendering bug slips
through.

## Consequences
- `style` attributes still require `style-src 'unsafe-inline'`; script
  injection is blocked.
- New UI handlers must be registered in the delegation table, not written as
  `onclick`.
