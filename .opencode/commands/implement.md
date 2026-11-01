---
description: Implementa el siguiente slice vertical con criterios de aceptación y tests.
agent: engineering-lead
---

Implementa: $ARGUMENTS

Antes de editar:

- Lee `AGENTS.md`, la documentación relevante y los tests existentes.
- Localiza el contrato, los estados, los errores y los invariantes.
- Si el alcance es ambiguo, formula preguntas antes de escribir código.

Durante la implementación:

- Carga `reliability-hardening` si el cambio toca concurrencia, colas,
  reintentos o límites de tasa.
- Carga `llm-output-contracts` si el cambio toca prompts, parseo o validación
  de salidas de IA.
- Divide en slices verticales pequeños.
- No cambies contratos públicos sin aprobación.
- Ejecuta los tests relevantes después de cada cambio.
- Delega a `test-engineer` los tests de contrato cuando aporte independencia.

No hagas commit, push ni crees ramas.

Al terminar, reporta archivos tocados, decisiones, tests ejecutados, riesgos y
trabajo pendiente.
