---
description: Convierte un brief o repositorio en requisitos, criterios de aceptación y riesgos.
agent: engineering-lead
---

Carga la skill `requirements-engineering`.

Analiza el siguiente alcance: $ARGUMENTS

Si no hay argumento, inspecciona el repositorio actual y deduce el alcance del
cambio o del módulo que se va a tocar.

Produce:

1. Problema y resultado esperado.
2. Requisitos funcionales con identificadores.
3. NFRs medibles.
4. Criterios de aceptación en formato Given/When/Then.
5. Invariantes y transiciones de estado.
6. Riesgos, supuestos y preguntas abiertas.
7. Matriz de trazabilidad requisito → criterio → test.

No escribas código de producción. Si el repositorio tiene una convención de
documentación, persiste el resultado en el documento correspondiente; si no,
devuélvelo en la respuesta.
