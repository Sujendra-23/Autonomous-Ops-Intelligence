"""Notion connector authority is always bounded by AOI workspace membership."""

from app.config import base_settings
from app.tenancy import (
    connector_service_context,
    principal_context,
    settings_context,
    workspace_context,
)


class NotionAccessError(Exception):
    """A safe permission error that never contains provider credentials."""


def require_notion_write(*resources) -> None:
    if base_settings().auth_mode != "oidc":
        return
    workspace = workspace_context.get()
    principal = principal_context.get()
    if workspace is None or settings_context.get() is None:
        raise NotionAccessError("Notion requires a workspace context")
    if principal is not None:
        if principal.workspace_id != workspace or principal.role not in {
            "owner",
            "admin",
            "member",
        }:
            raise NotionAccessError("Notion writes require a writable workspace membership")
    elif not connector_service_context.get():
        raise NotionAccessError("Notion requires a signed-in user or workspace worker")
    if any(resource is not None and resource.workspace_id != workspace for resource in resources):
        raise NotionAccessError("Notion resource belongs to another workspace")
