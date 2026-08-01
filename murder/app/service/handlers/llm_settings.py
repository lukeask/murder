"""``llm.*`` application handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from murder.app.protocol.requests import CommandName
from murder.app.service.settings import effective_harness_view
from murder.app.service.settings import llm as llm_usecases
from murder.app.service.settings_service import SettingsService
from murder.runtime.workers.model_catalog_refresh_worker import (
    provider_has_usable_credentials,
    request_catalog_refresh,
)

if TYPE_CHECKING:
    from murder.app.service.host import ServiceHost


def register(host: ServiceHost) -> None:  # noqa: PLR0915 - command registration is intentionally flat
    def _repository() -> SettingsService:
        return SettingsService(repo_root=host.repo_root)

    def _save_reply(cfg: Any, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        _repository().save(cfg)
        project = host.config.project.name if host.config.project is not None else None
        return llm_usecases.llm_reply(
            cfg,
            effective=effective_harness_view(host.config),
            extra=extra,
            project=project,
        )

    def _llm_set_disabled(body: dict[str, Any]) -> dict[str, Any]:
        cfg = llm_usecases.load_mutable_config(_repository())
        return _save_reply(llm_usecases.set_disabled(cfg, body.get("disabled")))

    def _llm_provider_create(body: dict[str, Any]) -> dict[str, Any]:
        cfg = llm_usecases.load_mutable_config(_repository())
        cfg, provider_id = llm_usecases.create_provider(cfg, body.get("provider"))
        reply = _save_reply(cfg, extra={"provider_id": provider_id})
        if provider_has_usable_credentials(cfg.llm.providers[provider_id]):
            request_catalog_refresh()
        return reply

    def _llm_provider_update(body: dict[str, Any]) -> dict[str, Any]:
        cfg = llm_usecases.load_mutable_config(_repository())
        provider_id = body.get("provider_id")
        reply = _save_reply(llm_usecases.update_provider(cfg, provider_id, body.get("patch")))
        # The child reloads YAML, so it sees the completed atomic settings save.
        provider = cfg.llm.providers.get(provider_id) if isinstance(provider_id, str) else None
        if provider is not None and provider_has_usable_credentials(provider):
            request_catalog_refresh()
        return reply

    def _llm_provider_delete(body: dict[str, Any]) -> dict[str, Any]:
        cfg = llm_usecases.load_mutable_config(_repository())
        return _save_reply(
            llm_usecases.delete_provider(
                cfg, body.get("provider_id"), confirm=body.get("confirm")
            )
        )

    def _llm_provider_models_update(body: dict[str, Any]) -> dict[str, Any]:
        cfg = llm_usecases.load_mutable_config(_repository())
        return _save_reply(
            llm_usecases.update_provider_models(
                cfg, body.get("provider_id"), body.get("patch")
            )
        )

    async def _llm_provider_discover_models(body: dict[str, Any]) -> dict[str, Any]:
        cfg = llm_usecases.load_mutable_config(_repository())
        provider_id = body.get("provider_id")
        try:
            cfg, discovered = await llm_usecases.discover_provider_models(cfg, provider_id)
            message = None
        except Exception as exc:  # preserve prior cache and report a usable error state
            if isinstance(provider_id, str) and cfg.llm and provider_id in cfg.llm.providers:
                cfg.llm.providers[provider_id].models.discovery_error = str(exc)
                _repository().save(cfg)
            return {"ok": True, "models": [], "message": str(exc)}
        _repository().save(cfg)
        return {
            "ok": True,
            "models": [{"id": model_id, "label": model_id} for model_id in discovered],
            "message": message,
        }

    def _llm_policy_create(body: dict[str, Any]) -> dict[str, Any]:
        cfg = llm_usecases.load_mutable_config(_repository())
        cfg, policy_id = llm_usecases.create_policy(
            cfg, body.get("name"), body.get("policy")
        )
        return _save_reply(cfg, extra={"policy_id": policy_id})

    def _llm_policy_update(body: dict[str, Any]) -> dict[str, Any]:
        cfg = llm_usecases.load_mutable_config(_repository())
        return _save_reply(
            llm_usecases.update_policy(cfg, body.get("policy_id"), body.get("patch"))
        )

    def _llm_policy_delete(body: dict[str, Any]) -> dict[str, Any]:
        cfg = llm_usecases.load_mutable_config(_repository())
        return _save_reply(
            llm_usecases.delete_policy(
                cfg, body.get("policy_id"), confirm=body.get("confirm")
            )
        )

    def _llm_policy_activate(body: dict[str, Any]) -> dict[str, Any]:
        cfg = llm_usecases.load_mutable_config(_repository())
        return _save_reply(llm_usecases.activate_policy(cfg, body.get("policy_id")))

    def _llm_policy_clone(body: dict[str, Any]) -> dict[str, Any]:
        cfg = llm_usecases.load_mutable_config(_repository())
        cfg, policy_id = llm_usecases.clone_policy(
            cfg, body.get("policy_id"), body.get("name")
        )
        return _save_reply(cfg, extra={"policy_id": policy_id})

    def _llm_feature_policy_set(body: dict[str, Any]) -> dict[str, Any]:
        cfg = llm_usecases.load_mutable_config(_repository())
        return _save_reply(
            llm_usecases.set_feature_policy(
                cfg, body.get("feature_type"), body.get("policy_id")
            )
        )

    def _llm_preview_resolution(body: dict[str, Any]) -> dict[str, Any]:
        cfg = llm_usecases.load_mutable_config(_repository())
        return llm_usecases.preview_resolution(cfg, body)

    host.register_application_command(CommandName.LLM_SETTINGS_SET_DISABLED, _llm_set_disabled)
    host.register_application_command(CommandName.LLM_PROVIDER_CREATE, _llm_provider_create)
    host.register_application_command(CommandName.LLM_PROVIDER_UPDATE, _llm_provider_update)
    host.register_application_command(CommandName.LLM_PROVIDER_DELETE, _llm_provider_delete)
    host.register_application_command(
        CommandName.LLM_PROVIDER_MODELS_UPDATE, _llm_provider_models_update
    )
    host.register_application_command(
        CommandName.LLM_PROVIDER_DISCOVER_MODELS, _llm_provider_discover_models
    )
    host.register_application_command(CommandName.LLM_POLICY_CREATE, _llm_policy_create)
    host.register_application_command(CommandName.LLM_POLICY_UPDATE, _llm_policy_update)
    host.register_application_command(CommandName.LLM_POLICY_DELETE, _llm_policy_delete)
    host.register_application_command(CommandName.LLM_POLICY_ACTIVATE, _llm_policy_activate)
    host.register_application_command(CommandName.LLM_POLICY_CLONE, _llm_policy_clone)
    host.register_application_command(
        CommandName.LLM_FEATURE_POLICY_SET, _llm_feature_policy_set
    )
    host.register_application_command(CommandName.LLM_PREVIEW_RESOLUTION, _llm_preview_resolution)
