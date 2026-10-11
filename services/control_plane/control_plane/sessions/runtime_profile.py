"""Which runtime configuration a session runs on, by session kind (D-046).

WHY THIS IS A MODULE AND NOT TWO LINES IN THE HANDLER. The pilot and the product's conversations
share one hardening generator, and until now they shared one answer: everything off. D-046 gives
the conversation the engine's own web search, and nothing else: the five `[features]` flags stay
off in every profile, because they are what would give the model a shell.

The difference is DECLARED HERE, in one place, so that:

  * the pilot's configuration is what it always was, byte for byte (the test compares the rendered
    file with the hardened default, not with a description of it);
  * nobody adds a second exception by editing the handler, where it would be invisible next to
    twenty lines about turns;
  * the value written is separate from the value VERIFIED, and that separation already paid for
    itself: the first version of this module asked for `"enabled"`, which is not a value the
    engine has. The live read said so (`unknown variant `enabled``), and the profile now asks
    for `"live"`, which is.

A session kind this module does not know gets the hardened profile. An unknown kind is the case
where a silent default matters most, and the safe answer is the one the pilot uses.
"""

from __future__ import annotations

from dataclasses import dataclass

from control_plane.sessions.store import SESSION_CONVERSATION, SESSION_PRESENCE

#: Product conversations may search the web natively; the pilot may not. Anything not listed
#: falls back to the hardened value.
#:
#: `"live"`, not `"cached"` ani `"indexed"`: rozmowa o tym, gdzie warto się teraz odezwać, pyta
#: o stan sieci z dzisiaj. Pozostałe dwa tryby są prawidłowymi wartościami enuma i zostają
#: dostępne dla profilu, który kiedyś będzie chciał taniej i wolniej.
WEB_SEARCH_BY_KIND: dict[str, str] = {
    SESSION_CONVERSATION: "live",
    SESSION_PRESENCE: "disabled",
}

WEB_SEARCH_OFF = "disabled"


@dataclass(frozen=True)
class RuntimeProfile:
    """What this session asks the runtime for. Small on purpose: one knob, one reason."""

    #: Value written to `web_search` in the session's `config.toml`.
    web_search: str

    @property
    def searches_the_web(self) -> bool:
        return self.web_search != WEB_SEARCH_OFF


def profile_for(kind: str | None) -> RuntimeProfile:
    """The profile for a session of this kind."""
    return RuntimeProfile(web_search=WEB_SEARCH_BY_KIND.get(kind or "", WEB_SEARCH_OFF))


def hardening_is_broken(mismatches: list[str]) -> list[str]:
    """The part of a `config/read` comparison that means the HARDENING did not take.

    Two kinds of mismatch, two meanings, and merging them would hide the one that matters.
    A `web_search` that came back other than asked is a capability we did not get: the session
    works, with one tool fewer. A `[features]` flag that came back other than `false` means the
    runtime has a shell the model can reach, and that is not a footnote.
    """
    return [one for one in mismatches if one.startswith("features.")]
