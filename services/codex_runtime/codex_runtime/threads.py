"""Four isolated thread roles, `ThreadSecurityBinding` and fail-closed resume (13.4, L2241-2266).

The split from L2241 is strict here: `ThreadSecurityBinding` is the PERSISTENT security of
resume, `CodexRunThread` is the metadata of a single run. Both types come from `contracts`
and are not duplicated here — the same goes for `authorize_resume`, which is the only
implementation of the fail-closed rule in the repo.

This module adds three things that contracts do not have, because they are runtime concerns:

* a registry of threads per role — four roles, four separate `thread_id`s, no global thread
  for the workspace (L2265),
* the verifier context whitelist (L1952, L5313),
* the ban on resume BETWEEN roles, which is a special case of fail-closed and which we
  want to have named, rather than derived from a message about a difference of fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from community_os_contracts import (
    ResumeRefused,
    ThreadRole,
    ThreadSecurityBinding,
    authorize_resume,
)

from .protocol import AppServerClient

#: The fields the verifier is allowed to see. Everything outside this list is removed.
#: L1952: "The verifier does not get the worker's private argumentation, only the outcome,
#: the sources, the criteria and the evidence." L5313: "the verifier works in a fresh thread
#: and does not see the private argumentation of the draft's author."
VERIFIER_CONTEXT_WHITELIST: frozenset[str] = frozenset({"wynik", "zrodla", "kryteria", "evidence"})

#: The keys that in practice carry the author's private argumentation. We keep them named,
#: so that a test can show the removal of a concrete field, and not merely of "something".
PRIVATE_REASONING_FIELDS: frozenset[str] = frozenset(
    {
        "rozumowanie",
        "reasoning",
        "notatki_autora",
        "chain_of_thought",
        "watpliwosci",
        "prompt_wewnetrzny",
        "wariantY_odrzucone",
    }
)


class RoleMismatch(ResumeRefused):
    """A resume between roles. A separate name, because it is the worst possible mismatch."""


def verifier_context(author_context: dict[str, Any]) -> dict[str, Any]:
    """Trims the context down to the whitelist. An unknown field is removed, not let through.

    The direction matters: this is an allowlist, not a denylist. A new field in the author's
    context does not leak to the verifier by itself — it has to be added here deliberately.
    """
    return {k: v for k, v in author_context.items() if k in VERIFIER_CONTEXT_WHITELIST}


@dataclass
class RoleThread:
    """One runtime thread assigned to one role."""

    role: ThreadRole
    binding: ThreadSecurityBinding
    thread_id: str
    session_id: str


@dataclass
class ThreadRegistry:
    """Four roles → four separate threads. There is no shared thread here and none can be made.

    The registry keeps a `ThreadSecurityBinding` per role and is the only place through which
    `thread/start` and `thread/resume` may be called.
    """

    client: AppServerClient
    codex_connection_id: UUID
    workspace_id: UUID
    tool_manifest_version: str
    security_policy_version: str
    social_account_id: UUID | None = None
    principal_generation: int | None = None
    _threads: dict[ThreadRole, RoleThread] = field(default_factory=dict, init=False)

    def binding_for(self, role: ThreadRole) -> ThreadSecurityBinding:
        return ThreadSecurityBinding(
            id=uuid4(),
            codex_connection_id=self.codex_connection_id,
            workspace_id=self.workspace_id,
            social_account_id=self.social_account_id,
            role=role,
            principal_generation=self.principal_generation,
            tool_manifest_version=self.tool_manifest_version,
            security_policy_version=self.security_policy_version,
        )

    def start(self, role: ThreadRole, **params: Any) -> RoleThread:
        """`thread/start` for one role. A repeated call for the same role is an error."""
        if role in self._threads:
            raise ValueError(
                f"wątek roli {role} już istnieje ({self._threads[role].thread_id}). "
                "Ponowny start znaczyłby, że rola ma dwa wątki — a to znosi izolację z L2265."
            )
        response = self.client.request("thread/start", dict(params))
        thread = response.get("thread", response)
        thread_id = thread["id"]
        # L2265: we record the ACTUAL session_id returned by the runtime, we do not guess it.
        session_id = thread.get("sessionId") or thread_id
        binding = self.binding_for(role).model_copy(
            update={"thread_id": thread_id, "session_id": session_id}
        )
        entry = RoleThread(role=role, binding=binding, thread_id=thread_id, session_id=session_id)
        self._threads[role] = entry
        return entry

    def start_all_roles(self, **params: Any) -> dict[ThreadRole, RoleThread]:
        return {role: self.start(role, **params) for role in ThreadRole}

    def thread(self, role: ThreadRole) -> RoleThread:
        if role not in self._threads:
            raise KeyError(f"rola {role} nie ma jeszcze wątku")
        return self._threads[role]

    @property
    def threads(self) -> dict[ThreadRole, RoleThread]:
        return dict(self._threads)

    def resume(
        self,
        stored: ThreadSecurityBinding,
        requested: ThreadSecurityBinding,
        *,
        active_principal_generation: int | None,
        **params: Any,
    ) -> RoleThread:
        """Resume only on an exact match of the stable binding (L2266).

        Authorization goes through `authorize_resume` from contracts — there is no second,
        parallel implementation of that rule here.
        """
        if stored.role is not requested.role:
            raise RoleMismatch(
                f"resume z roli {stored.role} do {requested.role} — role mają osobne wątki "
                "i nigdy nie wznawiają się nawzajem (L2265)"
            )
        authorize_resume(stored, requested, active_principal_generation=active_principal_generation)
        if not stored.thread_id:
            raise ResumeRefused("zapisany binding nie ma `thread_id` — nie ma czego wznawiać")

        response = self.client.request("thread/resume", {"threadId": stored.thread_id, **params})
        thread = response.get("thread", response)
        thread_id = thread.get("id", stored.thread_id)
        session_id = thread.get("sessionId") or thread_id
        binding = requested.model_copy(update={"thread_id": thread_id, "session_id": session_id})
        entry = RoleThread(
            role=requested.role, binding=binding, thread_id=thread_id, session_id=session_id
        )
        self._threads[requested.role] = entry
        return entry


def threads_are_disjoint(threads: dict[ThreadRole, RoleThread]) -> bool:
    """Whether every role has its own, unique `thread_id` (L2265)."""
    identyfikatory = [outcome.thread_id for outcome in threads.values()]
    return len(identyfikatory) == len(set(identyfikatory)) == len(ThreadRole)
