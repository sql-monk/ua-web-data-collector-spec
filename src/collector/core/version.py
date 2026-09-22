"""Інформація про версію застосунку для `collector version` та OCI labels."""

from __future__ import annotations

import os
from collections.abc import Mapping
from importlib import metadata

from pydantic import BaseModel, ConfigDict

from collector.contracts._base import CONTRACTS_VERSION

GIT_SHA_ENV = "COLLECTOR_GIT_SHA"
UNKNOWN_GIT_SHA = "unknown"


class VersionInfo(BaseModel):
    """Версії, які друкує CLI і які потрапляють у логи/health (§9.10)."""

    model_config = ConfigDict(frozen=True)

    package_version: str
    git_sha: str
    schema_version: str

    def render(self) -> str:
        """Рядковий вигляд для stdout: один `key=value` на рядок."""
        return "\n".join(
            (
                f"package_version={self.package_version}",
                f"git_sha={self.git_sha}",
                f"schema_version={self.schema_version}",
            )
        )


def package_version(distribution: str = "collector") -> str:
    """Версія встановленого дистрибутива або `0.0.0+unknown`, якщо пакет не встановлено."""
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return "0.0.0+unknown"


def git_sha(environ: Mapping[str, str] | None = None) -> str:
    """Git SHA з env `COLLECTOR_GIT_SHA` (задає CI/Docker build) або `unknown`."""
    env: Mapping[str, str] = os.environ if environ is None else environ
    value = env.get(GIT_SHA_ENV, "").strip()
    return value or UNKNOWN_GIT_SHA


def version_info() -> VersionInfo:
    """Зібрати повну інформацію про версію."""
    return VersionInfo(
        package_version=package_version(),
        git_sha=git_sha(),
        schema_version=CONTRACTS_VERSION,
    )
