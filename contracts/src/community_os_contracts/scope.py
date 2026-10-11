"""The scope boundary from section 4 (L237-253) recorded as data, not as a sentence in a doc.

D14 (ScopePolicy) enforces this list in CI. Here is its only source of truth.

The document, verbatim (L239):

    "The roadmap covers browser control, keeping logged-in sessions alive,
     marketing planning, research, content preparation, measurement and controlled actions."

And (L253):

    "A challenge, a block, MFA, an unknown screen or a mismatch of the logged-in identity
     end with a controlled stop."

The boundary holds in every stage (STAN-PROJEKTU.md, settled point 7) and in E1 without
exceptions (L4305).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ForbiddenPractice(StrEnum):
    """Nine items from the "Nie obejmuje" list (L243-251). Order as in the document."""

    CAPTCHA_OR_MFA_BYPASS = "CAPTCHA_OR_MFA_BYPASS"
    FINGERPRINT_SPOOFING = "FINGERPRINT_SPOOFING"
    RATE_LIMIT_OR_BLOCK_CIRCUMVENTION = "RATE_LIMIT_OR_BLOCK_CIRCUMVENTION"
    COOKIE_COPYING_BETWEEN_PROFILES = "COOKIE_COPYING_BETWEEN_PROFILES"
    PROXY_ROTATION_FOR_CIRCUMVENTION = "PROXY_ROTATION_FOR_CIRCUMVENTION"
    AUTOMATED_ACCOUNT_OR_IDENTITY_CREATION = "AUTOMATED_ACCOUNT_OR_IDENTITY_CREATION"
    COORDINATED_SELF_AMPLIFICATION = "COORDINATED_SELF_AMPLIFICATION"
    AUTOMATIC_RETRY_AFTER_AMBIGUOUS_SUBMIT = "AUTOMATIC_RETRY_AFTER_AMBIGUOUS_SUBMIT"
    UNBOUNDED_ACTION_LOOPS = "UNBOUNDED_ACTION_LOOPS"


class ForbiddenPracticeSpec(BaseModel):
    """One scope-boundary item together with its verbatim wording and place of enforcement."""

    model_config = ConfigDict(frozen=True)

    practice: ForbiddenPractice
    quote_pl: str
    source_line: int
    #: Where this item is enforced TODAY. Every path must exist — this is guarded by
    #: `test_every_enforced_by_path_exists`. An empty tuple means "nowhere yet",
    #: and then `planned_in` must point at the phase in which it will come into being.
    enforced_by: tuple[str, ...] = ()
    #: The phase in which the item will get enforcement. Filled in only when `enforced_by` is empty.
    planned_in: str | None = None


#: The verbatim list from L241-251. The order and the wording must agree with the document —
#: the contract test compares these quotes against the roadmap file.
FORBIDDEN_PRACTICES: tuple[ForbiddenPracticeSpec, ...] = (
    ForbiddenPracticeSpec(
        practice=ForbiddenPractice.CAPTCHA_OR_MFA_BYPASS,
        quote_pl="omijania CAPTCHA albo MFA",
        source_line=243,
        enforced_by=(
            "services/browser_supervisor/browser_supervisor/supervisor.py",
            "contracts/src/community_os_contracts/enums.py",
        ),
    ),
    ForbiddenPracticeSpec(
        practice=ForbiddenPractice.FINGERPRINT_SPOOFING,
        quote_pl="spoofingu fingerprintu i mechanizmów ukrywania automatyzacji",
        source_line=244,
        enforced_by=(
            "services/browser_supervisor/browser_supervisor/args.py",
            "infra/capsule/Dockerfile",
        ),
    ),
    ForbiddenPracticeSpec(
        practice=ForbiddenPractice.RATE_LIMIT_OR_BLOCK_CIRCUMVENTION,
        quote_pl="obchodzenia blokad lub technicznych rate limitów",
        source_line=245,
        planned_in="E1.F2 — wymaga adaptera i budżetu odczytu",
    ),
    ForbiddenPracticeSpec(
        practice=ForbiddenPractice.COOKIE_COPYING_BETWEEN_PROFILES,
        quote_pl="kopiowania cookies między profilami",
        source_line=246,
        enforced_by=(
            "services/browser_supervisor/browser_supervisor/profile.py",
            "services/profile_lease/profile_lease/store.py",
        ),
    ),
    ForbiddenPracticeSpec(
        practice=ForbiddenPractice.PROXY_ROTATION_FOR_CIRCUMVENTION,
        quote_pl="automatycznej rotacji proxy w celu obejścia zabezpieczeń",
        source_line=247,
        enforced_by=("services/browser_supervisor/browser_supervisor/args.py",),
    ),
    ForbiddenPracticeSpec(
        practice=ForbiddenPractice.AUTOMATED_ACCOUNT_OR_IDENTITY_CREATION,
        quote_pl="automatycznego tworzenia kont lub fałszywych tożsamości",
        source_line=248,
        planned_in="E1.F2 — konta zakłada człowiek, brak kodu do egzekwowania",
    ),
    ForbiddenPracticeSpec(
        practice=ForbiddenPractice.COORDINATED_SELF_AMPLIFICATION,
        quote_pl="koordynowanego podbijania tej samej treści przez kilka własnych kont",
        source_line=249,
        planned_in="E1.F4 — wymaga Conflict Engine i kolejki approvali",
    ),
    ForbiddenPracticeSpec(
        practice=ForbiddenPractice.AUTOMATIC_RETRY_AFTER_AMBIGUOUS_SUBMIT,
        quote_pl="automatycznego retry po niejednoznacznym submit",
        source_line=250,
        enforced_by=("contracts/src/community_os_contracts/execution.py",),
    ),
    ForbiddenPracticeSpec(
        practice=ForbiddenPractice.UNBOUNDED_ACTION_LOOPS,
        quote_pl=(
            "nieograniczonych pętli wykonujących akcje aż pojawi się wynik zależny od innych ludzi"
        ),
        source_line=251,
        planned_in="E1.F4 — wymaga budżetów dziennych i stop conditions",
    ),
)


#: Events that must end with a controlled stop (L253).
#: This is not a list of errors to handle — it is a list of situations in which the automation
#: is to stop working.
CONTROLLED_STOP_TRIGGERS: tuple[str, ...] = (
    "CHALLENGE",
    "BLOCK",
    "MFA",
    "UNKNOWN_SCREEN",
    "IDENTITY_MISMATCH",
)


class ScopeViolation(Exception):
    """Raised when the code tries to do something outside the scope boundary.

    This is not an ordinary validation error — it is a stop caused by crossing the product
    boundary.
    """

    def __init__(self, practice: ForbiddenPractice, context: str) -> None:
        spec = FORBIDDEN_PRACTICES_BY_KIND[practice]
        super().__init__(
            f"Granica zakresu (sekcja 4, L{spec.source_line}): {spec.quote_pl}. Kontekst: {context}"
        )
        self.practice = practice
        self.context = context


FORBIDDEN_PRACTICES_BY_KIND: dict[ForbiddenPractice, ForbiddenPracticeSpec] = {
    spec.practice: spec for spec in FORBIDDEN_PRACTICES
}
