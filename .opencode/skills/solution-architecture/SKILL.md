---
name: Solution Architecture
description: "Diseña o revisa arquitectura: contexto, límites, opciones, NFRs, ADRs, failure modes y ruta de evolución."
---

# Solution Architecture

## Cuándo usar

- Hay que elegir estructura, límites o tecnología.
- Un cambio afecta concurrencia, persistencia, integraciones o despliegue.
- Falta justificar un trade-off.
- Hay que revisar deuda o preparar una evolución.

## Workflow

1. **Contexto:** actores, sistemas externos, datos y disparadores.
2. **Restricciones:** presupuesto, latencia, equipo, infraestructura, seguridad.
3. **Opciones:** al menos dos, con ventajas, costes y riesgos.
4. **Decisión:** elige y escribe un ADR.
5. **Vistas:** componentes, flujo principal, estados y despliegue.
6. **Failure modes:** qué falla, cómo se detecta y cómo se recupera.
7. **NFRs:** latencia, throughput, disponibilidad, observabilidad, seguridad.
8. **Evolución:** qué se acepta hoy y cómo se sustituye mañana.

## Plantilla de ADR

```markdown
### ADR-XXX: título

**Contexto.** Fuerzas y restricciones.

**Decisión.** Qué se decide.

**Consecuencias.**
- Beneficios.
- Costes y límites.
- Qué queda fuera.

**Alternativas descartadas.** Opción y motivo.
```

## Checklist

- [ ] Los límites del sistema y del cambio están claros.
- [ ] Hay al menos dos opciones reales.
- [ ] La decisión tiene consecuencias explícitas.
- [ ] Los failure modes incluyen detección y recuperación.
- [ ] Los NFRs son medibles.
- [ ] La observabilidad y la operación están cubiertas.
- [ ] La ruta de evolución evita prometer lo que no se entrega.
- [ ] La documentación vive en el repositorio, no solo en el chat.

## Principios

1. **Simplicidad primero.** La complejidad debe pagar por un requisito.
2. **Límites explícitos.** Dominio, aplicación e infraestructura no se mezclan.
3. **Reversibilidad.** Prefiere decisiones fáciles de cambiar si el coste es
   similar.
4. **Fallos esperados.** Todo servicio tiene timeouts, reintentos y saturación.
5. **Datos sensibles.** La arquitectura debe minimizar su exposición y retención.

## Errores comunes

- Elegir una tecnología antes de entender el problema.
- Omitir el coste operativo.
- Confundir un diagrama bonito con una decisión.
- No documentar las alternativas descartadas.
- Ignorar el límite del proveedor externo.
