"""CLI entrypoint for running the LibHippo web server."""

import argparse
from pathlib import Path

from libhippo.runner.config import HarnessConfig
from libhippo.runner.harness import GeneralAgentHarness
from libhippo.web.server import run_server


def main() -> None:
    parser = argparse.ArgumentParser(description="LibHippo Agent Harness Web Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="Port to bind (default: 8080)")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="Workspace root directory")
    args = parser.parse_args()

    config = HarnessConfig(workspace_root=args.workspace)
    harness = GeneralAgentHarness(config=config)

    print(f"Starting LibHippo Web Server at http://{args.host}:{args.port}")
    run_server(harness=harness, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
