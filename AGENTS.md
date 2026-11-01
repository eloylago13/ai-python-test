# AGENTS.md

## Proyecto

Servicio interno de notificaciones: recibe peticiones en lenguaje natural,
extrae una notificación estructurada con IA, la valida y la entrega por email o
SMS. La API está en FastAPI y corre en el puerto 5000.

Documentación de referencia:

- `docs/architecture.md`: contexto, requisitos, diseño, ADRs, operación y
  limitaciones.
- `docs/ai-development.md`: agentes, comandos, skills y forma de trabajo.
- `readme.md`: contrato de integración y puesta en marcha del entorno.

## Estructura

```text
app/                  # servicio FastAPI y lógica de negocio
  main.py             # composición y lifespan
  notification/       # api/, application/, domain/, infrastructure/, observability/
provider/             # proveedor externo simulado; no modificar sin motivo
platform/             # k6, InfluxDB y Grafana
tests/                # tests unitarios y de integración
docs/                 # documentación del proyecto
.opencode/            # agentes, comandos, skills y permisos
```

## Comandos

```bash
# Dependencias
python -m venv .venv
.venv\Scripts\python -m pip install -r app/requirements.txt -r requirements-dev.txt

# Tests
.venv\Scripts\python -m pytest -q

# Servicio local
cd app
..\.venv\Scripts\python -m uvicorn main:app --host 127.0.0.1 --port 5000

# Métricas
curl http://localhost:5000/metrics

# Stack completo
docker compose up -d provider influxdb grafana
docker compose up -d --build app
docker compose run --rm load-test
```

## Convenciones

- Python 3.12, type hints y `from __future__ import annotations`.
- Async en toda la ruta de I/O.
- Dominio y casos de uso no dependen de FastAPI ni de `httpx`.
- Nuevas dependencias: justificar, fijar versión y actualizar requisitos.
- No loguear `user_input`, destinatarios, mensajes ni secretos.
- Los errores de negocio se modelan con `ErrorCode`; no se devuelven trazas.
- Los cambios de prompt se tratan como cambios de contrato: tests y revisión.
- Persistencia local con SQLite (`data/notifications.db`); producción debe usar
  un adaptador Postgres detrás del mismo puerto. No versionar `app/data/` ni
  archivos `*.db`.
- Métricas en `/metrics`; usa rutas normalizadas y evita etiquetas de alta
  cardinalidad.
- `INBOUND_API_KEY` es opcional; no hardcodear secretos ni activarla en local
  sin coordinación.
- El CI vive en `.github/workflows/ci.yml` y ejecuta la suite.
- El estado local es SQLite; las colas siguen en memoria. Al reiniciar se
  recuperan las peticiones en `processing`; no prometas durabilidad de cola.

## Flujo de trabajo asistido

1. Si el contrato no está claro: `/discover`.
2. Si hay decisiones de estructura o trade-offs: `/design`.
3. Implementación: `/implement`.
4. Tests desde el contrato: `/test`.
5. Revisión independiente: `/review`.
6. Evidencia final: `/verify`.

Las skills están en `.opencode/skills/` y son genéricas. El contexto del
proyecto vive aquí y en `docs/`, no dentro de las skills.

## Git

- No hacer commit, push ni crear ramas sin petición explícita.
- La configuración de `.opencode/opencode.json` bloquea esos comandos.
- El humano revisa el diff y decide la integración.

## Definition of done

- Contrato y estados correctos.
- Tests del camino feliz y de los fallos esperados.
- Sin secretos ni PII en código, tests o logs.
- Documentación actualizada si cambia el comportamiento o la configuración.
- Suite completa ejecutada y evidencia reportada.
