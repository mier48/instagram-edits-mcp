# Instalación

## Requisitos

- macOS con [Instagram Edits para Mac](https://edits.instagram.com/desktop).
- En Edits: **Settings › Automation › Enable Automation Server**. Copia el **Access token** que aparece ahí.
- Python **3.12 o posterior** (por ejemplo, `brew install python@3.12`). El `python3` que trae macOS (3.9) no sirve.

No hace falta instalar ningún paquete. El lanzador `bin/instagram-edits-mcp` ejecuta el servidor directamente desde el código del repositorio, con Python en modo aislado.

```bash
git clone https://github.com/mier48/instagram-edits-mcp.git ~/Developer/instagram-edits-mcp
```

En los ejemplos siguientes, `REPO` es la ruta absoluta de ese clon (por ejemplo, `/Users/tu-usuario/Developer/instagram-edits-mcp`).

## Guardar el token

Salvo con el plugin de Claude Code, que gestiona el token él mismo, guárdalo en el llavero de macOS:

```bash
REPO/bin/instagram-edits-mcp set-token
```

El token se pide sin eco y se guarda como contraseña genérica (servicio `instagram-edits-mcp`, cuenta `default`). Nunca se escribe en disco, nunca aparece en la línea de órdenes y nunca debe pegarse en un chat. Para borrarlo, usa `instagram-edits-mcp delete-token`.

Comprueba la conexión:

```bash
REPO/bin/instagram-edits-mcp doctor
```

## Claude Code: plugin desde GitHub (recomendado)

Dentro de Claude Code:

```text
/plugin marketplace add mier48/instagram-edits-mcp
/plugin install instagram-edits-mcp@instagram-edits-mcp
```

Claude Code pide el token en un campo oculto y lo guarda en el llavero. También ofrece la opción **Read-only mode**. Para cambiar cualquiera de los dos más adelante, usa `/plugin configure instagram-edits-mcp@instagram-edits-mcp`.

Para instalarlo desde un clon local en lugar de desde GitHub:

```text
/plugin marketplace add REPO
```

El plugin instala también la *skill* `edits`.

## Claude Code: servidor MCP manual

```bash
claude mcp add --scope user instagram-edits -- REPO/bin/instagram-edits-mcp serve
```

No pongas el token en esa línea: quedaría en texto plano en `~/.claude.json` y en el historial de la *shell*. El servidor lo lee del llavero.

## Claude Desktop

Edita `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "instagram-edits": {
      "command": "REPO/bin/instagram-edits-mcp",
      "args": ["serve"]
    }
  }
}
```

Reinicia Claude Desktop. Claude Desktop arranca los servidores con un `PATH` mínimo; el lanzador busca también en `/opt/homebrew/bin` y `/usr/local/bin`. Si tu Python está en otra ruta, añade `"env": {"INSTAGRAM_EDITS_MCP_PYTHON": "/ruta/a/python3.12"}`.

## Codex

En `~/.codex/config.toml`:

```toml
[mcp_servers.instagram-edits]
command = "REPO/bin/instagram-edits-mcp"
args = ["serve"]
```

## Otros clientes MCP

Cualquier cliente que lance servidores MCP por **stdio** sirve: la orden es `REPO/bin/instagram-edits-mcp serve`. Para pasar el token por el entorno en lugar del llavero (por ejemplo, en un cliente que lo guarde él mismo cifrado), usa `INSTAGRAM_EDITS_MCP_TOKEN`.

## Instalación como paquete (opcional)

```bash
pipx install REPO
```

Esto deja la orden `instagram-edits-mcp` en el `PATH`. Ten en cuenta que construir el paquete descarga de PyPI el *backend* de construcción (`hatchling`); el lanzador de `bin/` no necesita red.

## Actualizar y desinstalar

- Plugin: `/plugin marketplace update instagram-edits-mcp` y `/plugin update instagram-edits-mcp@instagram-edits-mcp`. Para desinstalar: `/plugin uninstall instagram-edits-mcp@instagram-edits-mcp`.
- Manual: `git pull` en `REPO`. Para desinstalar: `claude mcp remove instagram-edits` (o borra la entrada del cliente), luego `instagram-edits-mcp delete-token` y borra `REPO`.
