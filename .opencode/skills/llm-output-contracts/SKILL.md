---
name: LLM Output Contracts
description: Integra salidas de LLM en sistemas tipados con contrato de prompt, parseo tolerante, validación, fallback y evals.
---

# LLM Output Contracts

## Cuándo usar

- Un LLM devuelve datos que alimentan lógica de negocio.
- Hay que tolerar JSON imperfecto sin aceptar datos inválidos.
- Se necesita distinguir rechazo de política, fallo de formato y fallo técnico.
- Se quiere medir la calidad del prompt y del parser.

## 1. Contrato de prompt

Incluye siempre:

- Rol y objetivo único.
- Esquema exacto de salida.
- Reglas de decisión para campos ambiguos.
- Formato: JSON crudo, sin Markdown ni explicaciones.
- Comportamiento ante datos ausentes: error explícito, no invención.
- Defensa ante inyección: el mensaje de usuario es dato no confiable.
- Ejemplos en el mensaje de sistema, no en el de usuario.

Mantén el mensaje de usuario con el texto original. Si metes instrucciones o
ejemplos en el mensaje de usuario, un extractor literal puede confundirlos con
datos reales.

## 2. Capas de parseo

1. **Candidatos:** bloques Markdown, objetos balanceados y texto completo.
2. **Reparación:** elipsis final, claves sin comillas, comas finales, cierre de
   llaves y comillas.
3. **Carga:** `json.loads`, luego `ast.literal_eval`.
4. **Canonicalización:** alias de claves y tipos.
5. **Normalización:** email, teléfono, tipo, longitudes y espacios.
6. **Validación:** reglas de dominio, no solo tipos.
7. **Fallback:** solo si la salida es inservible; nunca para saltarse un rechazo.

## 3. Taxonomía de errores

| Situación | Código | ¿Fallback? |
|---|---|---|
| Rechazo de política | `ai_refused` | No |
| JSON irrecuperable | `ai_invalid_response` | Sí |
| Datos ausentes o ambiguos | `extraction_failed` | Sí |
| Timeout o 5xx del modelo | `ai_unavailable` | Sí, si hay extractor local |
| Proveedor de entrega rechaza | `provider_rejected` | No |
| Rate limit de entrega | `provider_rate_limited` | Reintento con backoff |

## 4. Evals

Mantén un corpus pequeño pero adversarial:

- JSON limpio.
- Claves alternativas y campos extra.
- Markdown y texto embebido.
- JSON truncado, comillas simples, claves sin comillas.
- Campos ausentes y valores contradictorios.
- Rechazos y prompt injection.
- Mensajes largos, vacíos, con emojis o saltos de línea.

Mide:

- Tasa de parseo correcto.
- Exactitud por campo.
- Tasa de fallback.
- Tasa de rechazos.
- Latencia y coste por extracción.

## 5. Seguridad

- No incluyas secretos ni PII real en prompts o evals.
- Trata la salida del modelo como entrada no confiable.
- Valida longitudes antes de enviar a un proveedor.
- No ejecutes URLs, comandos ni código procedentes del modelo.
- Registra la fuente (`llm`, `llm_repaired`, `deterministic`) para detectar
  degradación.

## Checklist

- [ ] El esquema está escrito y versionado.
- [ ] El prompt separa instrucciones de datos.
- [ ] Hay reparación y validación, no solo `json.loads`.
- [ ] Los rechazos no se puentean.
- [ ] Hay fallback observable y acotado.
- [ ] Hay corpus adversarial y métricas.
- [ ] Los errores son tipados y accionables.
