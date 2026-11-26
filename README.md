# Instagram Edits MCP

Servidor MCP **local y reforzado** para [Instagram Edits para Mac](https://edits.instagram.com/desktop). Conecta Claude Code, Claude Desktop, Codex o cualquier cliente MCP por stdio con el servidor de automatización que Edits incluye de serie, y mantiene sincronizado el conjunto de herramientas, que cambia según la pantalla (unas 30 en la pantalla de inicio y unas 270 con un proyecto abierto).

## Qué aporta

- **Solo loopback.** La URL debe ser `http://` con una IP literal de loopback. Sin proxies, sin redirecciones y sin ninguna otra conexión de red. No hay telemetría.
- **Comprueba que al otro lado está Edits.** Antes de enviar el token, verifica con `codesign` que el proceso que escucha en el puerto está firmado por Instagram, Inc. (`777W53UFB2`) con el identificador `com.burbn.basel.mac`.
- **El token, en el llavero de macOS.** Nunca se escribe en disco, nunca aparece en la línea de órdenes y nunca sale en logs, errores ni en `doctor`.
- **Modo de solo lectura y políticas** `allow`/`deny` por herramienta, aplicadas tanto al listado como a las llamadas.
- **Descubrimiento dinámico.** Avisa al cliente cuando Edits cambia de herramientas y permite arrancar antes que Edits (herramienta `edits_connection_status`).
- **Sin dependencias.** Solo la biblioteca estándar de Python ≥ 3.12. Se instala como plugin directamente desde GitHub.

## Inicio rápido

1. En Edits: **Settings › Automation › Enable Automation Server**, y copia el *Access token*.
2. Instala Python 3.12 o posterior (`brew install python@3.12`).
3. Elige tu cliente:

**Claude Code (plugin):**

```text
/plugin marketplace add mier48/instagram-edits-mcp
/plugin install instagram-edits-mcp@instagram-edits-mcp
```

**Cualquier otro cliente:** clona el repositorio, guarda el token y registra el servidor:

```bash
REPO/bin/instagram-edits-mcp set-token
```

```bash
REPO/bin/instagram-edits-mcp doctor
```

Después añade `REPO/bin/instagram-edits-mcp serve` como servidor MCP por stdio. Hay instrucciones para Claude Desktop, Claude Code y Codex en [docs/INSTALL.md](docs/INSTALL.md).

## Órdenes

| Orden | Qué hace |
|---|---|
| `serve` | Servidor MCP por stdio (es la orden por defecto) |
| `doctor [--json]` | Comprueba la app, la identidad del servidor, el token, la política y las herramientas. La salida no contiene secretos |
| `set-token` / `delete-token` | Guarda o borra el token en el llavero |
| `config` | Muestra la configuración efectiva |

## Documentación

- [docs/INSTALL.md](docs/INSTALL.md): instalación y clientes
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md): fichero TOML, variables de entorno, políticas y verificación
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): módulos y flujo de una petición
- [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md): activos, amenazas, controles y riesgos residuales
- [SECURITY.md](SECURITY.md): cómo comunicar una vulnerabilidad

## Desarrollo

```bash
python3.12 -m venv .venv && . .venv/bin/activate && pip install pytest==9.1.1
```

```bash
python -m pytest
```

Las pruebas usan un doble de Edits en `127.0.0.1` y tokens ficticios. No necesitan Edits, ni red, ni el llavero. En macOS, dos pruebas de seguridad usan además los `lsof` y `codesign` reales.

## Licencia

MIT. Ver [LICENSE](LICENSE). Proyecto independiente, sin relación con Meta, Instagram ni Anthropic, y sin su respaldo. "Instagram" y "Edits" son marcas de Meta Platforms, Inc.
