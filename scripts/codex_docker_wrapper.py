#!/usr/bin/env python3
"""Run Codex in a container that can only see its home and task workspace."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


DEFAULT_IMAGE = "verify-engineering-work-codex:0.144.0-alpha.4"


def option_value(arguments: list[str], names: tuple[str, ...]) -> str | None:
    for index, argument in enumerate(arguments[:-1]):
        if argument in names:
            return arguments[index + 1]
    return None


def docker_command(arguments: list[str], environment: dict[str, str]) -> list[str]:
    docker = environment.get("CODEX_DOCKER_EXECUTABLE", "docker")
    image = environment.get("CODEX_DOCKER_IMAGE", DEFAULT_IMAGE)
    local_provider = environment.get("CODEX_LOCAL_PROVIDER")
    command = [
        docker,
        "run",
        "--rm",
        "--init",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit=512",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--env",
        "HOME=/tmp/codex-user",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=1g",
    ]
    if arguments == ["--version"]:
        return [*command, image, *arguments]

    raw_home = environment.get("CODEX_HOME")
    raw_workspace = option_value(arguments, ("-C", "--cd"))
    raw_auth = environment.get("CODEX_AUTH_FILE")
    if not raw_home:
        raise ValueError("CODEX_HOME is required")
    if not raw_workspace:
        raise ValueError("Codex command must include -C/--cd")
    if not local_provider and not raw_auth:
        raise ValueError("CODEX_AUTH_FILE is required")

    home = Path(raw_home).expanduser().resolve()
    workspace = Path(raw_workspace).expanduser().resolve()
    if not home.is_dir():
        raise ValueError(f"CODEX_HOME is not a directory: {home}")
    if not workspace.is_dir():
        raise ValueError(f"workspace is not a directory: {workspace}")
    if local_provider and "exec" in arguments:
        exec_index = arguments.index("exec")
        arguments = [
            *arguments[: exec_index + 1],
            "--oss",
            "--local-provider",
            local_provider,
            *arguments[exec_index + 1 :],
        ]
    command.extend(
        [
            "--mount",
            f"type=bind,source={home},target={home}",
            "--mount",
            f"type=bind,source={workspace},target={workspace}",
        ]
    )
    if local_provider:
        ollama_target = environment.get(
            "CODEX_OLLAMA_TARGET", "host.docker.internal:11434"
        )
        command.extend(
            [
                "--add-host",
                "host.docker.internal:host-gateway",
                "--env",
                f"OLLAMA_PROXY_TARGET={ollama_target}",
            ]
        )
    else:
        auth = Path(raw_auth).expanduser().resolve()
        if not auth.is_file():
            raise ValueError(f"authentication file is missing: {auth}")
        command.extend(
            [
                "--mount",
                f"type=bind,source={auth},target={home / 'auth.json'},readonly",
            ]
        )
    return [
        *command,
        "--env",
        f"CODEX_HOME={home}",
        "--workdir",
        str(workspace),
        image,
        *arguments,
    ]


def main() -> int:
    try:
        command = docker_command(sys.argv[1:], os.environ.copy())
        return subprocess.run(command, check=False).returncode
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
