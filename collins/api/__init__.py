# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The API between the Collins session service and its clients.

`protocol` is the GTK-free message table, validation and framing (the twin
of `mcptools` for the session MCP tools). The transports that carry it
(`server`, `client`, `loopback`) arrive with the PRs of the split that need
them; see ~/specs/collins/split-service-and-client.md.
"""
