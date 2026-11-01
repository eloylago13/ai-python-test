---
description: Escribe o amplía tests desde el contrato y reporta gaps.
agent: test-engineer
---

Escribe o amplía los tests para: $ARGUMENTS

Si no hay argumento, inspecciona el cambio actual y los criterios de aceptación
disponibles.

Proceso:

1. Carga `requirements-engineering` si necesitas reconstruir criterios.
2. Identifica el contrato observable: entradas, salidas, estados, errores e
   idempotencia.
3. Cubre camino feliz, límites, fallos esperados y concurrencia relevante.
4. No adaptes los tests a la implementación; modela el contrato externo.
5. Ejecuta la suite y reporta resultado, gaps y riesgos.

No modifiques código de producción. No hagas commit, push ni crees ramas.
