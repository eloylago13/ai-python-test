---
name: Reliability Hardening
description: Endurece servicios asíncronos con colas acotadas, backpressure, reintentos, idempotencia, timeouts, rate limits y apagado ordenado.
---

# Reliability Hardening

## Cuándo usar

- Hay workers, colas, reintentos o integraciones externas.
- El servicio debe sobrevivir a picos y a fallos del proveedor.
- Hay riesgo de duplicados, bloqueos o saturación.
- Se prepara una prueba de carga o un release.

## Checklist de pipeline

### Aceptación

- [ ] El endpoint de aceptación no espera al proveedor.
- [ ] La operación es idempotente o tiene clave de idempotencia.
- [ ] La cola está acotada.
- [ ] Si la cola se llena, hay rechazo explícito con `Retry-After`.
- [ ] La entrada tiene límites de tamaño y validación.

### Procesamiento

- [ ] Los workers tienen un número máximo.
- [ ] Las etapas lentas están separadas de las limitadas por tasa.
- [ ] El estado se transiciona de forma atómica.
- [ ] Los estados terminales no se sobrescriben.
- [ ] Un fallo inesperado deja el registro en estado terminal, no colgado.

### Reintentos

- [ ] Solo se reintenta lo reintentable (timeout, 429, 5xx).
- [ ] Hay backoff exponencial con jitter.
- [ ] Hay un límite de intentos y un error terminal.
- [ ] Los errores de autenticación no se reintentan.
- [ ] Los reintentos no duplican efectos secundarios.

### Rate limiting

- [ ] El límite del proveedor está documentado.
- [ ] El límite del cliente deja margen para reintentos.
- [ ] Varias instancias no comparten un límite en memoria sin coordinación.
- [ ] La cola refleja el throughput real, no solo la capacidad de aceptación.

### Timeouts y saturación

- [ ] Hay timeout de conexión, lectura y pool.
- [ ] Un timeout no bloquea indefinidamente un worker.
- [ ] La saturación se propaga como backpressure, no como memoria infinita.
- [ ] Hay métricas o logs de profundidad de cola.

### Apagado

- [ ] Se deja de aceptar trabajo al recibir la señal.
- [ ] Se drena con un timeout acotado.
- [ ] Se cancelan workers y se cierran clientes.
- [ ] Se documenta qué trabajo se pierde si el drenaje no termina.

### Observabilidad

- [ ] Cada petición tiene un identificador correlacionable.
- [ ] Se registra el resultado de cada etapa sin PII.
- [ ] Hay health checks separados de liveness y readiness.
- [ ] Los errores incluyen código, no solo mensaje.

## Cálculo de capacidad

1. Mide la latencia p50/p95 de cada dependencia.
2. Calcula throughput máximo por worker: `1 / latencia`.
3. Multiplica por el número de workers.
4. Compara con el límite del proveedor.
5. El mínimo de los dos es el throughput real.
6. Si el productor supera ese mínimo, el backlog crece; decide si es aceptable,
   si hay que priorizar o si hay que rechazar.

## Anti-patrones

- Cola sin límite.
- Reintentar un `400` o un `401`.
- Un solo pool para etapas con cuellos de botella distintos.
- Rate limit solo en el cliente, sin coordinación entre réplicas.
- `await` de una operación lenta dentro del request de aceptación.
- Logs con el contenido completo del usuario.
- Health check que depende de un proveedor externo para liveness.

## Salida esperada

Un informe con: riesgos encontrados, escenario de fallo, evidencia, impacto y
recomendación. Si no hay evidencia, marca la hipótesis como no verificada.
