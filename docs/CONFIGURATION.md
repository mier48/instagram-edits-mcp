# Configuración

Precedencia: **valores por defecto < fichero TOML < variables de entorno**. Todo se valida al arrancar: un valor inválido detiene el servidor con un mensaje claro (código de salida 2) en lugar de ignorarse. Puedes ver la configuración efectiva, que no contiene secretos, con:

```bash
instagram-edits-mcp config
```

## Fichero TOML

Ruta por defecto: `~/.config/instagram-edits-mcp/config.toml` (o la que indique `INSTAGRAM_EDITS_MCP_CONFIG`). Debe pertenecer al usuario y no poder escribirlo nadie más (`chmod 600`); si no, se rechaza. Las claves desconocidas son un error. Ejemplo completo en [examples/config.toml](../examples/config.toml).

```toml
[server]
url = "http://127.0.0.1:19501/mcp"   # solo http:// con IP literal de loopback y puerto
verify = "enforce"                    # enforce | warn | off
expected_identifier = "com.burbn.basel.mac"
expected_team_id = "777W53UFB2"       # Instagram, Inc.
timeout_seconds = 600                 # 1-3600; las exportaciones pueden tardar
max_response_mb = 64                  # 1-1024; captureFrame devuelve imágenes

[policy]
read_only = false
allow = []                            # vacío = todas; si no, solo las que coinciden
deny = ["deleteProject"]              # patrones glob; deny gana siempre

[relay]
track_tools = true                    # re-comprobar las herramientas tras cada llamada
follow_stream = true                  # seguir el stream de notificaciones si Edits lo ofrece
retry_seconds = 5                     # 0,1-300, mientras Edits no está disponible

[credentials]
keychain = true
keychain_service = "instagram-edits-mcp"
keychain_account = "default"
```

## Variables de entorno

| Variable | Equivale a | Notas |
|---|---|---|
| `INSTAGRAM_EDITS_MCP_TOKEN` | — | Token. Tiene prioridad sobre el llavero. Es la que usa el plugin |
| `INSTAGRAM_EDITS_MCP_CONFIG` | — | Ruta del TOML. Si se indica y no existe, es un error |
| `INSTAGRAM_EDITS_MCP_URL` | `server.url` | |
| `INSTAGRAM_EDITS_MCP_VERIFY_SERVER` | `server.verify` | `warn` y `off` generan un aviso al arrancar |
| `INSTAGRAM_EDITS_MCP_READ_ONLY` | `policy.read_only` | `1`/`true`/`yes`/`on` o `0`/`false`/`no`/`off` |
| `INSTAGRAM_EDITS_MCP_ALLOW_TOOLS` | `policy.allow` | Lista separada por comas; **sustituye** a la del fichero |
| `INSTAGRAM_EDITS_MCP_DENY_TOOLS` | `policy.deny` | Lista separada por comas; **se suma** a la del fichero |
| `INSTAGRAM_EDITS_MCP_RETRY_SECONDS` | `relay.retry_seconds` | |
| `INSTAGRAM_EDITS_MCP_KEYCHAIN` | `credentials.keychain` | |
| `INSTAGRAM_EDITS_MCP_PYTHON` | — | Intérprete que debe usar el lanzador `bin/` |

Un valor del tipo `${user_config.…}` (el marcador sin resolver que deja Claude Code cuando una opción no está definida) se trata como si la variable no existiera.

## Políticas de herramientas

Una herramienta está permitida si se cumplen las tres condiciones:

1. no coincide con ningún patrón de `deny`;
2. `allow` está vacía o la herramienta coincide con algún patrón de `allow`;
3. el modo de solo lectura está desactivado, o la herramienta es de solo lectura.

Una herramienta es **de solo lectura** si Edits la anota con `readOnlyHint: true` o, cuando Edits no dice nada, si su nombre es de consulta: `get…`, `list…`, `search…`, `find…`, `describe…`, `inspect…`, `query…`, `read…`, `count…`, `capture…`, `…Status`, o una de las búsquedas conocidas (`appState`, `projects`, `elements`, `beatMarkers`, `audioOnsets`, `previewVideoFrames`). Nunca lo es si lleva `readOnlyHint: false` o `destructiveHint: true`.

Las herramientas no permitidas **desaparecen de `tools/list`** y, si el cliente las llama igualmente, **la llamada no llega a Edits**: se devuelve un resultado con `isError: true` que empieza por `Blocked by instagram-edits-mcp policy`.

Ejemplos:

```toml
# Solo análisis: ver proyectos, catálogos y fotogramas, sin cambiar nada.
[policy]
read_only = true

# Edición permitida, pero sin borrar ni exportar.
[policy]
deny = ["deleteProject", "export*"]

# Lista cerrada.
[policy]
allow = ["appState", "projects", "openProject", "addText", "captureFrame"]
```

## Verificación del servidor

Con `verify = "enforce"` (por defecto en macOS), antes de cada petición el relé:

1. pregunta a `/usr/sbin/lsof` qué procesos escuchan en el puerto;
2. comprueba la firma dinámica de cada uno con `/usr/bin/codesign --verify -R='anchor apple generic and identifier "com.burbn.basel.mac" and certificate leaf[subject.OU] = "777W53UFB2"' <pid>`. El resultado se cachea por PID.

Si algún proceso no supera la comprobación, **no se envía nada** (tampoco el token) y el relé responde con el error `-32003` o pasa a modo desconectado. `warn` solo registra un aviso, y `off` desactiva la comprobación, lo que solo tiene sentido en pruebas. En sistemas que no son macOS la comprobación se desactiva siempre, porque Edits solo existe para macOS.

Si una actualización de Edits cambia su firma, `doctor` lo mostrará. Comprueba la nueva firma con `codesign -dv --verbose=2 /Applications/Edits.app` antes de ajustar `expected_team_id` o `expected_identifier`.
