"""Hosted Wenext: the product's MCP server reachable over HTTPS with a personal token.

`http_app` is the ASGI app (its own service, `mcp-edge`, never mounted behind the operator
boundary of `control_plane.app`), `tokens` is the table of hashed personal tokens, and `invite`
is the administrator's command line that creates a stranger's organization, workspace and project
and mints the token. The protocol itself is `control_plane.tools.mcp_server.MCPToolServer`, the
same one the plugin runs over stdio.
"""
