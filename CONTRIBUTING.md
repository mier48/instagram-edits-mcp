# Contribuir

## Comunicar un problema

Ejecuta esto y pega la salida en la *issue* (no contiene secretos):

```bash
instagram-edits-mcp doctor --json
```

Incluye la versión de Edits (**Edits › About Edits**) y la del cliente MCP. **Nunca pegues el token.**

## Desarrollo

```bash
python3.12 -m venv .venv && . .venv/bin/activate && pip install pytest==9.1.1
```

```bash
python -m pytest
```

Reglas del proyecto:

- **Solo biblioteca estándar** en tiempo de ejecución. Cualquier dependencia nueva necesita una justificación de seguridad (ver "Por qué sin dependencias" en docs/ARCHITECTURE.md).
- **Sin red:** el código solo puede conectarse a la URL de loopback configurada. La *fixture* `loopback_only` hace fallar cualquier prueba que intente otra cosa.
- **Sin secretos reales en las pruebas:** usa `TEST_TOKEN` del doble u otros valores ficticios y simula `/usr/bin/security`.
- Todo texto de diagnóstico debe pasar por `redact`.
- Cuando Edits cambie su comportamiento, actualiza primero el doble `tests/support/edits_double.py` y después el relé.
- Cada corrección de seguridad va acompañada de una prueba en `tests/security/`.
