# Modelo de amenazas

## Activos

| Activo | Por qué importa |
|---|---|
| **Token de acceso de Edits** | Da control total de Edits, que tiene la sesión de Instagram iniciada: leer proyectos y medios, editarlos, borrarlos y exportarlos |
| Proyectos y medios del usuario | Contenido personal y, a menudo, sin publicar |
| Integridad de lo que ve el modelo | Un listado de herramientas o unos resultados falsos pueden llevar al modelo a hacer cosas que el usuario no quiere |

## Límites de confianza

```
[ Cliente MCP + modelo ]  --stdio-->  [ instagram-edits-mcp ]  --HTTP loopback-->  [ Edits.app ]
        confianza parcial                    confiable                            confiable si su firma es válida
                                                  ▲
               [ Otros procesos del mismo usuario ]   [ Red / Internet ]   [ Ficheros de configuración ]
                     no confiables                       no confiable        confiables solo si son del usuario y no los pueden escribir otros
```

- **El modelo** es de confianza parcial: puede equivocarse o ser manipulado mediante inyección de *prompt* indirecta (textos de proyectos, títulos o metadatos). Por eso los controles técnicos (política, solo lectura) están en el relé y no dependen de lo que decida el modelo.
- **Edits** es de confianza **solo si** el proceso que escucha está firmado con el Team ID de Instagram (`777W53UFB2`) y tiene el identificador `com.burbn.basel.mac`.
- **Fuera de alcance:** un atacante con privilegios de root o con capacidad de inyectar código en el propio relé o en el cliente MCP (por ejemplo, con un `sitecustomize.py` malicioso en el intérprete). Esto se mitiga en parte con `python -I` en el lanzador.

## Amenazas y controles

| # | Amenaza (STRIDE) | Vector | Control | Test |
|---|---|---|---|---|
| T1 | Divulgación del token por la red (I) | `INSTAGRAM_EDITS_MCP_URL` apuntando a un host externo, por error o desde una configuración manipulada | Solo `http://` con IP literal de loopback, comprobación del *peer* del socket | `TestEndpoint::test_refuses_everything_else`, `test_server_process_refuses_to_start_with_a_remote_address`, `test_nothing_is_sent_if_the_socket_lands_off_loopback` |
| T2 | Divulgación del token mediante una redirección (I) | Una respuesta 30x hacia otro host | `http.client` sin manejador de redirecciones; cualquier 3xx es un error | `test_redirects_are_not_followed` (301/302/303/307/308) |
| T3 | Divulgación del token a un proxy (I) | `HTTP_PROXY`/`ALL_PROXY` o el proxy del sistema macOS | `http.client` no usa proxies | `test_proxy_variables_are_ignored`, `test_server_process_ignores_proxy_variables` |
| T4 | Suplantación de Edits (S) | Un proceso local ocupa `127.0.0.1:19501` mientras Edits está cerrado | `ServerVerifier`: `lsof` + `codesign` con requisito fijado; el token no se envía y el relé pasa a modo desconectado | `TestImpersonation` (incluye dos pruebas con los `lsof` y `codesign` reales) |
| T5 | Token en reposo (I) | Fichero en texto plano, copias de seguridad, sincronización de *dotfiles* | Llavero de macOS; el secreto nunca va en `argv`; nunca se escribe en disco | `test_storing_passes_the_secret_on_stdin_only`, `test_token_is_not_on_the_command_line` |
| T6 | Token en diagnósticos (I) | Logs, mensajes de error, `doctor`, trazas de excepciones, `repr` | `Redactor` central, `Token.__repr__`, cuerpos de error no reenviados, manejador global en `cli` | `TestSecretsStayHidden`, `test_healthy_chain`, `tests/unit/test_redaction.py` |
| T7 | Acción no deseada del modelo (T/E) | Inyección de *prompt*, error del modelo | Modo de solo lectura, `allow`/`deny`, bloqueo también en `tools/call` | `test_read_only_mode`, `test_deny_list` |
| T8 | Métodos no previstos (E) | El cliente envía métodos JSON-RPC no estándar a Edits | Lista blanca de métodos | `test_methods_outside_the_allow_list_never_reach_edits` |
| T9 | Denegación de servicio (D) | Respuestas o líneas de stdin enormes | Límites de tamaño | `TestResourceLimits` |
| T10 | Debilitamiento de la configuración (T) | Otro usuario modifica `config.toml` | Se rechaza un fichero que no es del usuario o que otros pueden escribir; avisos cuando la verificación no está en `enforce` | `test_group_writable_file_is_rejected`, `test_weaker_verification_produces_a_warning` |
| T11 | Secuestro de módulos Python (E) | `PYTHONPATH`, `PYTHONSTARTUP` o un `json.py` en el directorio de trabajo | El lanzador usa `python -I` | Revisión del lanzador |
| T12 | Telemetría o exfiltración (I) | Código que llama a terceros | No hay ninguna conexión salvo a la URL de loopback; la batería de tests falla ante cualquier `connect()` que no sea a loopback | *Fixture* `loopback_only` en `tests/conftest.py` |
| T13 | Cadena de suministro (T) | Dependencias o acciones de CI comprometidas | Cero dependencias en tiempo de ejecución; workflow de publicación eliminado; CI sin secretos y con permisos mínimos | Revisión del workflow |

## Riesgos residuales

1. **TOCTOU en T4.** Entre la comprobación con `lsof`/`codesign` y `connect()` hay una ventana de milisegundos. Para aprovecharla, un proceso local tendría que ocupar el puerto justo cuando Edits lo libera. Mitigación completa: que Edits autentique el servidor (por ejemplo, con un socket Unix con *peer credentials*).
2. **El token en el entorno** (plugin de Claude Code o `INSTAGRAM_EDITS_MCP_TOKEN`) es legible por procesos del mismo usuario con `ps eww`. Un atacante con esa capacidad ya puede leer el llavero desbloqueado mediante `security`, con una solicitud de autorización de por medio. Con la instalación manual se recomienda el llavero.
3. **Inyección de *prompt* indirecta.** Los contenidos de Edits llegan al modelo sin filtrar. Ninguna herramienta de Edits publica en Instagram, pero sí puede borrar proyectos: usa `deny = ["deleteProject"]` o el modo de solo lectura cuando trabajes con contenido de terceros.
4. **Heurística de solo lectura.** Se basa en las anotaciones de Edits y en los nombres de las herramientas. Una herramienta mal anotada o con un nombre engañoso, del tipo `getAndDelete…`, podría clasificarse mal. Si necesitas una garantía estricta, combina `read_only` con una lista `allow` explícita.
5. **Cambios en Edits.** Si Instagram cambia el Team ID o el identificador, la verificación falla de forma segura: no se envía nada y `doctor` lo explica.
