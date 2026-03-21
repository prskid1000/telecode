from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class BackendInfo:
    key:               str
    name:              str
    description:       str
    base_cmd:          list[str]
    default_flags:     list[str]  = field(default_factory=list)
    required_env_vars: list[str]  = field(default_factory=list)


@dataclass
class BackendParams:
    """
    Runtime parameters for a backend — loaded from settings.json via config module.

    extra_flags   appended to base_cmd at launch
    env           merged into subprocess environment
    session_args  backend-specific state (e.g. claude resume_id)
    """
    extra_flags:  list[str]      = field(default_factory=list)
    env:          dict[str, str] = field(default_factory=dict)
    session_args: dict[str, str] = field(default_factory=dict)

    def build_cmd(self, base_cmd: list[str], default_flags: list[str]) -> list[str]:
        return base_cmd + default_flags + self.extra_flags


class CLIBackend(ABC):
    @property
    @abstractmethod
    def info(self) -> BackendInfo: ...

    def startup_message(self) -> str:
        return f"✅ <b>{self.info.name}</b> session started."

    def build_launch_cmd(self, params: BackendParams) -> list[str]:
        return params.build_cmd(self.info.base_cmd, self.info.default_flags)

    def resolve_env(self, params: BackendParams) -> dict[str, str]:
        return dict(params.env)
