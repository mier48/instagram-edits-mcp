# Seguridad

## Cómo se trata el token

- El token de Edits da control total de la app, que tiene iniciada la sesión de Instagram. Trátalo como una contraseña.
- **Plugin de Claude Code:** se introduce en un campo oculto y Claude Code lo guarda en el llavero del sistema. Se pasa al servidor por la variable `INSTAGRAM_EDITS_MCP_TOKEN`.
- **Instalación manual:** `instagram-edits-mcp set-token` lo lee sin eco y lo guarda en el llavero de macOS. El secreto se envía a `/usr/bin/security` por stdin, nunca en la línea de órdenes. No se escribe en ningún fichero.
- Nunca se escribe en logs, mensajes de error ni en la salida de `doctor`. Un redactor central enmascara además cualquier valor con forma de credencial.
- El token solo se envía a `http://<IP de loopback>`, sin proxies ni redirecciones, y solo después de comprobar que el proceso que escucha está firmado por Instagram, Inc.

Si el token acaba en algún sitio público (un chat, una captura o un commit), regenéralo en **Edits › Settings › Automation**.

## Garantías y límites

Consulta [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md). En resumen: el relé protege el token frente a la red, los proxies, las redirecciones, la suplantación del puerto y las fugas en diagnósticos, y limita lo que el modelo puede hacer mediante las políticas. No protege frente a un atacante que ya tenga control de tu cuenta de usuario de macOS.

## Comunicar una vulnerabilidad

Comunícala de forma privada mediante **Report a vulnerability**, en la pestaña Security del repositorio, y no en una *issue* pública. No incluyas tokens reales en el informe.
