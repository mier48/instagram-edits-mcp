# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.0] - 2026-11-26

First release of `instagram-edits-mcp`. See [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) for the threats it addresses.

### Security
- The Edits address must be plain HTTP to a literal loopback IP; the socket peer is re-checked after connecting.
- Transport on `http.client`: no proxies and no redirects, so the token cannot be forwarded elsewhere.
- Before anything is sent, every process on the Edits port must pass `codesign` against Edits' bundle
  identifier and Instagram's Apple Team ID.
- Access token kept in the macOS keychain and stored through stdin, never in argv or on disk.
- Central redaction of every diagnostic; Edits error bodies are never relayed.
- Size limits on replies, stream events and stdin lines.
- Closed list of forwarded JSON-RPC methods.
- Configuration files writable by other users are refused.
- No publishing workflow; CI without secrets and with least privilege.

### Added
- Read-only mode and `allow`/`deny` tool policies (TOML file, environment, plugin option).
- Dynamic tool discovery: the client is notified when the visible tool set changes.
- Detached start with the `edits_connection_status` tool until Edits becomes available.
- `serve`, `doctor`, `set-token`, `delete-token` and `config` commands.
- `bin/instagram-edits-mcp` launcher (Python ≥ 3.12, isolated mode) used by the Claude Code plugin.
- Unit, integration and security test suites; tests fail on any non-loopback connection.
- Architecture, threat model, installation and configuration documents.
