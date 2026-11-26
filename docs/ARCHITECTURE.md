# Arquitectura

`instagram-edits-mcp` es un **relé MCP local**: expone por stdio una sesión MCP cuyo servidor real es el de automatización que Instagram Edits para Mac incluye de serie. No implementa herramientas de edición propias. Las herramientas son las de Edits y cambian según la pantalla: unas 30 en la pantalla de inicio y unas 270 con un proyecto abierto.

```
 Cliente MCP                instagram-edits-mcp (stdio)                              Edits.app
                     ┌───────────────────────────────────────────┐
 stdin  ───────────► │ relay.messages.read_stream → classify     │
                     │ relay.server.Relay ─ dispatch por método  │
                     │   ├─ security.policy  (métodos, tools)    │
                     │   ├─ relay.catalog    (huella del listado)│
                     │   └─ relay.status     (modo desconectado) │
                     │ edits.client.EditsConnection              │──► security.identity
                     │   http.client · loopback · sin proxy      │      (lsof + codesign)
                     │   sin redirecciones · límites de tamaño   │──────── HTTP ─────────► 127.0.0.1:19501
 stdout ◄─────────── │ edits.patches.apply ◄─────────────────────│◄────────────────────────
                     └───────────────────────────────────────────┘
 stderr ◄─────────── security.redaction.Logger (todo redactado)
```

## Paquetes y módulos

| Módulo | Responsabilidad |
|---|---|
| `config.py` | `Endpoint` (dirección de Edits validada) y `Config` (inmutable). Una tabla única asocia cada clave TOML con su campo, su validador y su variable de entorno. Precedencia: valores por defecto < TOML < entorno |
| `errors.py` | Jerarquía de excepciones. Cada error de Edits lleva su código JSON-RPC y un `explain(url)` apto para el usuario |
| `security/redaction.py` | `Redactor` (valores registrados y patrones de credencial) y `Logger` a stderr |
| `security/keychain.py` | `Token` (oculto en `repr`/`str`, registrado en el redactor), `Keychain` (`/usr/bin/security`, secreto por stdin) y `resolve_token` |
| `security/identity.py` | `ServerVerifier`: `lsof` → PIDs en el puerto → `codesign --verify -R=<requisito>` por PID, con caché |
| `security/policy.py` | Métodos reenviables, heurística de solo lectura y `ToolPolicy` (`read_only`, `allow`, `deny`) |
| `edits/client.py` | `EditsConnection`: `send` (POST que acepta JSON o SSE), `offers_stream` y `follow` (GET SSE con reconexión) |
| `edits/sse.py` | `EventDecoder` incremental con límite por evento |
| `edits/patches.py` | Parches puros que conservan la identidad del objeto cuando no hay cambios: `speed_curve_catalog` y `cache_hints` |
| `relay/messages.py` | Clasificación JSON-RPC (`request`/`notification`/`response`/`invalid`), constructores de respuestas y lector acotado de stdin |
| `relay/catalog.py` | `ToolCatalog`: huella SHA-256 de los nombres de herramientas **visibles tras la política** |
| `relay/status.py` | Herramienta local `edits_connection_status` y respuesta local al *handshake* |
| `relay/server.py` | `Relay`: tabla de manejadores por método, estados conectado y desconectado, reintentos, sondeo del listado y seguimiento del *stream* |
| `diagnostics.py` | `doctor`: cadena de pasos sobre un `Probe` compartido; cada paso puede detener la cadena |
| `cli.py` | Órdenes y manejador global de errores con redacción |
| `bin/instagram-edits-mcp` | Lanzador POSIX: busca Python ≥ 3.12 y lo ejecuta en modo aislado (`-I`) |

Las dependencias van en un solo sentido: `config`/`errors` ← `security` ← `edits` ← `relay` ← `cli`/`diagnostics`. Solo se usa la biblioteca estándar (ver [Por qué sin dependencias](#por-qué-sin-dependencias)).

## Por qué sin dependencias

Se ha preferido una implementación propia con la biblioteca estándar al SDK oficial de MCP para Python:

| Criterio | SDK `mcp` | Implementación propia |
|---|---|---|
| Papel requerido | Relé fiel de un servidor con herramientas dinámicas y respuestas no estándar | Encaja de forma natural |
| Fidelidad | Los modelos Pydantic validan y reserializan: pueden rechazar o perder campos no estándar | Pasa el JSON y aplica solo parches puntuales |
| Red | `httpx` respeta `HTTP(S)_PROXY`/`ALL_PROXY` por defecto y admite redirecciones | Sin proxy ni redirecciones por construcción |
| Cadena de suministro | Más de 15 paquetes transitivos (`httpx`, `anyio`, `pydantic`, `pydantic-core`, `starlette`, `uvicorn`…) | 0 dependencias |
| Plugin desde GitHub | Hay que instalar dependencias desde PyPI en el primer arranque | Funciona recién clonado, sin red |

Si el proyecto llega a ofrecer muchas herramientas propias, conviene revisar esta decisión.

## Ciclo de un mensaje

1. `read_stream` lee una línea (máximo 16 MiB) y la decodifica. Un lote se procesa mensaje a mensaje.
2. `classify` decide el tipo. Las respuestas del cliente a peticiones del servidor se reenvían. Las notificaciones solo se reenvían si están en la lista y el relé está conectado. Una petición con un método fuera de la lista recibe `-32601` y no sale del proceso.
3. `Relay` elige el manejador: `initialize`, `ping`, `tools/list`, `tools/call` o reenvío genérico.
4. En `tools/call`, `ToolPolicy.decide` puede bloquear la llamada. El cliente recibe un resultado con `isError: true` y Edits no ve nada.
5. `EditsConnection.send`:
   1. `ServerVerifier.check`: **todos** los procesos del puerto deben ser Edits con firma válida;
   2. conexión TCP y comprobación de que el *peer* es loopback;
   3. `POST` con `Authorization`, `Mcp-Session-Id` y `MCP-Protocol-Version`;
   4. un estado ≥ 300 es un error (las redirecciones nunca se siguen), y el cuerpo se lee hasta el límite.
6. Las respuestas a `tools/list` se filtran con `ToolCatalog.present`. Todas pasan por `patches.apply` antes de escribirse.
7. Tras una llamada a una herramienta, el relé pide `tools/list` con un id interno (`instagram-edits-mcp/probe/N`, que nunca llega al cliente). Si la huella visible cambia, emite `notifications/tools/list_changed`.

## Estados del relé

| Estado | Entrada | Comportamiento |
|---|---|---|
| **Conectado** | `initialize` respondido por Edits | Reenvío con política, parches y sondeo de herramientas. Si Edits ofrece *stream*, un hilo lo sigue |
| **Desconectado** | `initialize` falla porque Edits no responde o no supera la verificación | El relé completa el *handshake* él mismo y ofrece solo `edits_connection_status`. Las llamadas a otras herramientas reciben `-32001`. Un hilo reintenta cada `retry_seconds` |
| Desconectado → conectado | Un reintento o una llamada a `edits_connection_status` tiene éxito | Se reenvía a Edits el *handshake* original del cliente y se emite `tools/list_changed` |

## Errores

| Código | Excepción | Significado |
|---|---|---|
| `-32001` | `EditsUnavailable` | Edits cerrado, o con la automatización desactivada |
| `-32002` | `EditsRejected` | Estado HTTP de error (401/403: token; 3xx: redirección rechazada) |
| `-32003` | `EditsImpersonated` | El proceso del puerto no es Edits; no se ha enviado nada |
| `-32004` | `EditsBadReply` | Respuesta inválida o demasiado grande |
| `-32601` | — | Método no disponible a través del relé |
| `isError: true` | — | Herramienta bloqueada por la política |

Ningún mensaje incluye cuerpos de respuesta de Edits ni el token, y todos pasan por el redactor.
