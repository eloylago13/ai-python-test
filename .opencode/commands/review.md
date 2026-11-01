---
description: Revisión independiente de arquitectura y fiabilidad.
agent: engineering-lead
subagent: false
---

Revisa el cambio o el diseño actual: $ARGUMENTS

Lanza en paralelo los subagentes `software-architect` y
`reliability-reviewer`, con el mismo alcance y el diff o los archivos
relevantes. Espera ambos resultados.

Consolida la salida:

1. Hallazgos duplicados, agrupados.
2. Severidad (`bloqueante`, `alto`, `medio`, `bajo`).
3. Evidencia: archivo/línea, escenario y comportamiento.
4. Recomendación concreta.
5. Riesgos aceptados y preguntas abiertas.

No edites código en este paso. No hagas commit, push ni crees ramas.
