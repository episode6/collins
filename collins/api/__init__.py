# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The API between the Collins session service and its clients.

`protocol` is the GTK-free message table, validation and framing (the twin
of `mcptools` for the session MCP tools). The transports that carry it
(`server` for the service, `client` for the window) are in this package; see
~/specs/collins/split-service-and-client.md.
"""
