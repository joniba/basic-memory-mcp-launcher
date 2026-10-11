"""Launch the installed Basic Memory server using handshake-only MCP."""

import argparse
from importlib import import_module, metadata
import re
import sys


class CompatibilityError(RuntimeError):
    """The installed runtime cannot provide the required handshake driver."""


def check_runtime():
    if sys.version_info < (3, 12):
        raise CompatibilityError("Python 3.12 or newer is required.")
    metadata.version("basic-memory")
    for package, major in (("mcp", 2), ("fastmcp", 4)):
        version = metadata.version(package)
        match = re.match(r"^(\d+)\.", version)
        if match is None or int(match.group(1)) != major:
            raise CompatibilityError(
                f"Requires {package} {major}.x; installed version is {version}. "
                "Use a compatible Basic Memory environment; no packages were changed."
            )


async def serve():
    from basic_memory.config import init_mcp_logging
    from fastmcp.server.context import reset_transport, set_transport
    from mcp.server.lowlevel.server import NotificationOptions
    from mcp.server.runner import serve_loop
    from mcp.server.stdio import stdio_server

    init_mcp_logging()
    for component in ("tools", "prompts", "resources"):
        import_module(f"basic_memory.mcp.{component}")
    app = import_module("basic_memory.mcp.server").mcp
    # FastMCP has no public option to select the SDK's handshake-only runner.
    server = getattr(app, "_mcp_server", None)
    if server is None or not callable(getattr(server, "lifespan", None)):
        raise CompatibilityError("FastMCP's server attachment API has changed.")

    token = set_transport("stdio")
    try:
        async with server.lifespan(server) as state:
            async with stdio_server() as (read_stream, write_stream):
                await serve_loop(
                    server,
                    read_stream,
                    write_stream,
                    lifespan_state=state,
                    init_options=server.create_initialization_options(
                        notification_options=NotificationOptions(tools_changed=True)
                    ),
                )
    finally:
        reset_transport(token)


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        check_runtime()
        import anyio

        anyio.run(serve)
    except metadata.PackageNotFoundError as error:
        print(
            f"basic-memory-mcp-launcher: missing {error.name}. "
            "Run this script with Basic Memory's installed Python environment.",
            file=sys.stderr,
        )
        return 1
    except (CompatibilityError, ImportError) as error:
        print(f"basic-memory-mcp-launcher: incompatible runtime: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
