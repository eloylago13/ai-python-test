---
name: Requirements Engineering
description: Convierte un brief, ticket o repositorio en requisitos funcionales, NFRs medibles, criterios de aceptación, invariantes y riesgos.
---

# Requirements Engineering

## Cuándo usar

- El brief es ambiguo o mezcla problema con solución.
- Hay que estimar alcance, riesgo o esfuerzo.
- Faltan criterios de aceptación antes de implementar.
- Un cambio toca estados, contratos o integraciones externas.

## Principios

1. **Problema antes que solución.** No aceptes una tecnología como requisito si
   no está justificada por una necesidad.
2. **Medible o no es NFR.** "Rápido" no sirve; "p95 < 200 ms" sí.
3. **Un requisito, un criterio.** Cada requisito debe poder verificarse.
4. **Incluye fallos.** Los caminos tristes son requisitos, no casos raros.
5. **Haz explícito lo que no entra.** El alcance negativo evita sorpresas.

## Workflow

1. Lee el brief, el README, los contratos y el código relevante.
2. Identifica actores, disparadores y resultado esperado.
3. Separa requisitos funcionales de restricciones y decisiones ya tomadas.
4. Define NFRs con métrica, objetivo y método de verificación.
5. Enumera estados, transiciones e invariantes.
6. Deriva criterios de aceptación Given/When/Then.
7. Construye una matriz requisito → criterio → test.
8. Registra riesgos, supuestos y preguntas abiertas.

## Plantilla de salida

```markdown
## Problema
## Resultado esperado
## Alcance
### Dentro
### Fuera
## Requisitos funcionales
| ID | Requisito | Prioridad |
## Requisitos no funcionales
| Área | Métrica | Objetivo | Verificación |
## Criterios de aceptación
### FR-1
- Given ...
- When ...
- Then ...
## Invariantes y estados
## Riesgos y supuestos
## Preguntas abiertas
## Trazabilidad
| Requisito | Criterio | Test |
```

## Checklist

- [ ] Cada requisito tiene un criterio verificable.
- [ ] Los NFRs tienen número y método de medida.
- [ ] Los estados y transiciones están completos.
- [ ] Los errores esperados están incluidos.
- [ ] El alcance negativo está escrito.
- [ ] Las preguntas abiertas tienen dueño o decisión pendiente.

## Errores comunes

- Convertir una solución propuesta en requisito.
- Omitir idempotencia, concurrencia o límites de tasa.
- Definir "disponibilidad" sin SLO.
- No distinguir requisito de restricción legal o técnica.
- Aceptar un criterio que no se puede observar desde fuera.
