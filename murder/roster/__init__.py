"""Roster feature: application service, persistence, and projection provider."""

from .service import RosterService, register_projection_provider

__all__ = ["RosterService", "register_projection_provider"]
