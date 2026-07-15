from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Action(StrEnum):
    READ = "approval:read"
    CREATE = "approval:create"
    DECIDE = "approval:decide"
    CANCEL = "approval:cancel"


class Principal(BaseModel):
    """The authenticated caller: which workspace, which user, which actions they hold."""

    model_config = ConfigDict(frozen=True)

    workspace_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    actions: frozenset[Action] = frozenset()

    def has_action(self, action: Action) -> bool:
        return action in self.actions
