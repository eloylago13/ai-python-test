---
description: Ejecuta tests y valida la evidencia contra los criterios de aceptación.
agent: engineering-lead
---

Verifica: $ARGUMENTS

Si no hay argumento, verifica el estado actual del repositorio.

Proceso:

1. Ejecuta la suite de tests.
2. Si el proyecto tiene prueba de carga o integración, ejecútala cuando el
   entorno esté disponible.
3. Recupera los criterios de aceptación y construye una matriz
   requisito → evidencia → resultado.
4. Distingue entre verificado, no verificado y fallo conocido.
5. Reporta logs o métricas relevantes sin incluir secretos ni PII.

No corrijas código en este paso salvo que el usuario lo pida explícitamente.
No hagas commit, push ni crees ramas.
