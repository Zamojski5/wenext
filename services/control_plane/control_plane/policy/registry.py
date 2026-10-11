"""Machine-readable register of section 19.4 (L3929-3946), required by D-019.

WHY A REGISTER EXISTS AT ALL. Section 19.4 lists twelve requirements. Six of them —
redirects, popups and new tabs, iframes, WebSockets, service workers, downloads (L3937-3942) —
need an enforcement point INSIDE A BROWSER, and F1 has no browser: the browser path is E1.F2.
D-019 settled that they go into an explicit NOT_ENFORCED register with an anchor and a target
phase, "a nie do milczenia". Its own verification clause says why: "Milczenie o pozycji jest
tu tym samym, czym kontrola bez pomiaru."

WHY THERE ARE NO TESTS PRETENDING TO ENFORCE THOSE SIX. In F1 there is no navigation and no
proxy, so a test named `test_a_redirect_to_another_origin_is_blocked` could only prove that a
function returned False. That is a name broader than its content, and the precedent is already
in this repository: `contracts/tests/test_invariants.py` near line 259 has a test named
`test_approval_from_another_account_does_not_match_the_intent` which swaps the whole revision
and never touches an account. The register says "not enforced" out loud instead.

TWO ITEMS ARE MARKED ENFORCED WITH A CAVEAT, AND THE CAVEAT IS PART OF THE VERDICT.
`docs/KONWENCJE.md`: "Zastrzeżenie idzie przy werdykcie, nie w przypisie." L3943 names an
egress proxy and L3944 names top-level navigation; F1 has neither. The mechanisms are real and
total at the only surface F1 has — authorization — so the status is ENFORCED and the `caveat`
field carries the limitation. D-019 defines exactly two statuses, so inventing a third would
contradict the decision it implements.

`enforced_by` NAMES CODE, AND A TEST RESOLVES IT. Two packages here once exported 27 names
that had never existed, and D-005 cited one of those phantoms as proof a requirement was met.
A register pointing at a non-existent function would be the same lie one document further on,
so `test_every_enforced_item_names_a_callable_that_really_exists` imports every path.

`requirement` HOLDS THE DOCUMENT'S OWN SENTENCE, VERBATIM, IN POLISH — it is quoted data, not
an identifier (docs/KONWENCJE.md). A test compares it against the actual lines of
`ROADMAP_CODEX_CDP_COMMUNITY_OS.md`, so re-wrapping the document breaks the build instead of
leaving the register pointing at sentences that moved.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "ENFORCED",
    "NOT_ENFORCED",
    "SECTION_19_4",
    "THIS_PHASE",
    "EnforcementStatus",
    "PolicyItem",
]

#: The phase this package implements. A NOT_ENFORCED item may not claim it as its target.
THIS_PHASE = "E1.F1"


class EnforcementStatus(StrEnum):
    """The two statuses of D-019, and no third one.

    Member names are English (D-010); the values are the Polish words the decision log uses,
    because they are what an artifact and the founder's report will carry.
    """

    ENFORCED = "EGZEKWOWANE"
    NOT_ENFORCED = "NIE_EGZEKWOWANE"


@dataclass(frozen=True)
class PolicyItem:
    """One bullet of section 19.4 with its verdict.

    Fields:
      anchor       document line, e.g. "L3937" — this is what binds the entry to the document
      requirement  the bullet's own text, verbatim
      status       EGZEKWOWANE / NIE_EGZEKWOWANE
      phase        where it is enforced, or the phase it is waiting for
      enforced_by  dotted path under `control_plane.policy`, empty when nothing enforces it
      rationale    why the verdict is what it is
      caveat       what the verdict does NOT cover; empty only when it covers the sentence
    """

    anchor: str
    requirement: str
    status: EnforcementStatus
    phase: str
    enforced_by: str
    rationale: str
    caveat: str = ""

    def __post_init__(self) -> None:
        if not (self.anchor.startswith("L") and self.anchor[1:].isdigit()):
            raise ValueError(f"kotwica {self.anchor!r} nie ma postaci Lxxxx")
        if not self.requirement:
            raise ValueError(f"{self.anchor}: pusta treść wymogu")
        if not self.rationale:
            raise ValueError(f"{self.anchor}: brak uzasadnienia werdyktu")
        if not self.phase:
            raise ValueError(f"{self.anchor}: brak fazy")
        if self.status is EnforcementStatus.ENFORCED and not self.enforced_by:
            raise ValueError(
                f"{self.anchor}: EGZEKWOWANE bez punktu egzekwowania. Status bez kodu, który go "
                "realizuje, to kontrola bez pomiaru"
            )
        if self.status is EnforcementStatus.NOT_ENFORCED:
            if self.enforced_by:
                raise ValueError(
                    f"{self.anchor}: NIE_EGZEKWOWANE, a wskazuje na {self.enforced_by!r}. "
                    "Wskazanie kodu przy pozycji nieegzekwowanej czyta się jak zaliczenie"
                )
            if self.phase == THIS_PHASE:
                raise ValueError(
                    f"{self.anchor}: NIE_EGZEKWOWANE z fazą docelową {THIS_PHASE} — pozycja "
                    "nieegzekwowana w tej samej fazie, w której miałaby być egzekwowana"
                )

    def to_json(self) -> dict[str, str]:
        """Polish keys: this is artifact data, read by the founder (docs/KONWENCJE.md)."""
        return {
            "kotwica": self.anchor,
            "wymog": self.requirement,
            "status": str(self.status),
            "faza": self.phase,
            "egzekwowane_przez": self.enforced_by,
            "uzasadnienie": self.rationale,
            "zastrzezenie": self.caveat,
        }


#: All twelve bullets of section 19.4, in document order. `requirement` is the line verbatim.
SECTION_19_4: tuple[PolicyItem, ...] = (
    PolicyItem(
        anchor="L3933",
        requirement="canonicalizację URL,",
        status=EnforcementStatus.ENFORCED,
        phase=THIS_PHASE,
        enforced_by="urlpolicy.canonical_origin",
        rationale=(
            "Jedna kanonikalizacja w repozytorium — funkcja sidecara, zaimportowana, nie "
            "skopiowana. Schemat i host małymi literami, domyślny port zdjęty, IDN do postaci "
            "ASCII, żeby homograf nie przeszedł jako 'inna pisownia tego samego hosta'."
        ),
    ),
    PolicyItem(
        anchor="L3934",
        requirement="exact origin/explicit host set jako granicę uprawnień,",
        status=EnforcementStatus.ENFORCED,
        phase=THIS_PHASE,
        enforced_by="urlpolicy.UrlPolicy.require_url",
        rationale=(
            "Porównanie dokładnego originu (schemat + host + port) z jawną allowlistą. Pusta "
            "allowlista to błąd konstrukcji, a nie 'nic nie pasuje': obiekt polityki, który "
            "nie autoryzuje niczego, przy pierwszej poprawce czyta się jak brak ograniczeń."
        ),
        caveat=(
            "Host będący dosłownym adresem IP jest w F1 odrzucany — literał omija DNS, więc "
            "kontrola z L3943 nie miałaby czego sprawdzić, a kanonikalizacja gubi nawiasy "
            "literału IPv6 (`https://[::1]` → `https://::1`)."
        ),
    ),
    PolicyItem(
        anchor="L3935",
        requirement="eTLD+1 wyłącznie do klasyfikacji, nie autoryzacji,",
        status=EnforcementStatus.ENFORCED,
        phase=THIS_PHASE,
        enforced_by="urlpolicy.UrlPolicy.require_url",
        rationale=(
            "Autoryzuje wyłącznie równość originu. W kodzie nie ma dopasowania po sufiksie "
            "domeny w ogóle — nie ma więc czego wyłączać: `zly.example.com` i "
            "`api.example.com` mają wspólny eTLD+1 i różne originy."
        ),
    ),
    PolicyItem(
        anchor="L3936",
        requirement="tylko HTTPS poza jawnie testowym fixture,",
        status=EnforcementStatus.ENFORCED,
        phase=THIS_PHASE,
        enforced_by="urlpolicy.UrlPolicy.require_url",
        rationale=(
            "Plaintext wymaga wpisu na DRUGIEJ liście (`plaintext_fixture_origins`); wpis "
            "`http://` bez tego oznaczenia jest odrzucany już przy budowie polityki. W "
            "sidecarze `DEFAULT_PORTS` przyjmuje http dla dowolnego hosta, a "
            "`FIXTURE_SITE_ORIGIN` nie jest w logice w żaden sposób wyróżniony — to jest "
            "dokładnie ta luka, którą ten moduł zamyka."
        ),
    ),
    PolicyItem(
        anchor="L3937",
        requirement="redirecty,",
        status=EnforcementStatus.NOT_ENFORCED,
        phase="E1.F2",
        enforced_by="",
        rationale=(
            "Przekierowanie zdarza się w nawigacji, a w F1 nie ma nawigacji ani proxy — nie ma "
            "punktu, w którym dałoby się je przerwać. Polityka URL sprawdza pojedynczy URL i "
            "nie zobaczy kolejnego skoku."
        ),
    ),
    PolicyItem(
        anchor="L3938",
        requirement="popupy i nowe karty,",
        status=EnforcementStatus.NOT_ENFORCED,
        phase="E1.F2",
        enforced_by="",
        rationale=(
            "Nowa karta to zdarzenie targetu przeglądarki (CDP `Target.targetCreated`). W F1 "
            "nie ma osadzonej przeglądarki, więc nie ma czego nasłuchiwać."
        ),
    ),
    PolicyItem(
        anchor="L3939",
        requirement="iframe'y,",
        status=EnforcementStatus.NOT_ENFORCED,
        phase="E1.F2",
        enforced_by="",
        rationale=(
            "Ramka podrzędna powstaje w drzewie ramek strony. Bez punktu egzekwowania w "
            "przeglądarce origin ramki nie jest w F1 w ogóle widoczny."
        ),
    ),
    PolicyItem(
        anchor="L3940",
        requirement="WebSockety,",
        status=EnforcementStatus.NOT_ENFORCED,
        phase="E1.F2",
        enforced_by="",
        rationale=(
            "Schematy `ws:`/`wss:` nie przechodzą przez kanonikalizację, bo `DEFAULT_PORTS` "
            "zna tylko http i https — ale to jest odmowa dla URL-a podanego jawnie, nie "
            "kontrola uścisku dłoni WebSocketu inicjowanego przez stronę."
        ),
    ),
    PolicyItem(
        anchor="L3941",
        requirement="service workery,",
        status=EnforcementStatus.NOT_ENFORCED,
        phase="E1.F2",
        enforced_by="",
        rationale=(
            "Service worker rejestruje się po stronie przeglądarki i przeżywa zamknięcie "
            "karty. Wyłącza się go przy uruchamianiu przeglądarki i przez CDP — jedno i "
            "drugie należy do F2."
        ),
    ),
    PolicyItem(
        anchor="L3942",
        requirement="downloady,",
        status=EnforcementStatus.NOT_ENFORCED,
        phase="E1.F2",
        enforced_by="",
        rationale=(
            "Pobieranie to `Browser.setDownloadBehavior` i katalog docelowy w kapsule. F1 nie "
            "uruchamia przeglądarki, więc nie ma zachowania, które można by ustawić."
        ),
    ),
    PolicyItem(
        anchor="L3943",
        requirement=(
            "prywatne i metadata IP sprawdzane po DNS resolution przez egress proxy, również "
            "przy rebindingu,"
        ),
        status=EnforcementStatus.ENFORCED,
        phase=THIS_PHASE,
        enforced_by="egress.EgressPolicy.authorize",
        rationale=(
            "Adresy sprawdzane PO rozwiązaniu nazwy, przy każdym wywołaniu (bez pamiętania "
            "werdyktu na host), z resolverem wstrzykiwanym. Odrzucane, jeśli ZŁA jest "
            "choćby jedna odpowiedź — nie 'jeśli wszystkie są złe'. Pusta odpowiedź DNS to "
            "odmowa. Rebinding przy połączeniu: `verify_connected_address` wymaga adresu z "
            "przypiętej odpowiedzi, więc podmiana na inny adres publiczny też jest odmową."
        ),
        caveat=(
            "Punktem egzekwowania jest funkcja autoryzacji control plane'u, NIE proxy egress "
            "z tego zdania — proxy jako osobny element sieci powstaje w E1.F2. Kod, który "
            "otwiera połączenie, musi sam wywołać `verify_connected_address`; w F1 nie ma "
            "gniazda, które by to wymuszało."
        ),
    ),
    PolicyItem(
        anchor="L3944",
        requirement=(
            "blokadę top-level navigation do `file:`, `data:`, `javascript:`, `chrome:` "
            "i `devtools:`."
        ),
        status=EnforcementStatus.ENFORCED,
        phase=THIS_PHASE,
        enforced_by="urlpolicy.UrlPolicy.require_url",
        rationale=(
            "Wszystkie pięć schematów jest w `FORBIDDEN_SCHEMES` sidecara (razem z `blob`, "
            "`about`, `view-source` i `ftp`), a kanonikalizacja rzuca na nich, więc żadna "
            "konfiguracja nie potrafi ich dopuścić — allowlista też nie."
        ),
        caveat=(
            "To jest odmowa przy AUTORYZACJI, nie blokada nawigacji: w F1 nie ma nawigacji "
            "top-level, bo nie ma przeglądarki. Blokada w samej przeglądarce (żeby strona nie "
            "wykonała takiego skoku sama) należy do E1.F2."
        ),
    ),
)

ENFORCED: tuple[PolicyItem, ...] = tuple(
    item for item in SECTION_19_4 if item.status is EnforcementStatus.ENFORCED
)

#: The six positions D-019 asks for by name. `len(NOT_ENFORCED)` is part of this package's DoD.
NOT_ENFORCED: tuple[PolicyItem, ...] = tuple(
    item for item in SECTION_19_4 if item.status is EnforcementStatus.NOT_ENFORCED
)

# Import-time guards. The register is a literal, so these can only fire while somebody is
# editing this file — which is exactly when a lost or duplicated entry has to be loud. A
# register that quietly shrinks is the same disease as a gate announcing GO with no
# measurement (D-007), one document further on.
if len(SECTION_19_4) != 12:
    raise RuntimeError(
        f"rejestr 19.4 ma {len(SECTION_19_4)} pozycji, a sekcja 19.4 dokumentu ma 12 punktów"
    )
if len({item.anchor for item in SECTION_19_4}) != len(SECTION_19_4):
    raise RuntimeError("rejestr 19.4 ma zduplikowaną kotwicę — jedna pozycja przykrywa drugą")
if len(ENFORCED) + len(NOT_ENFORCED) != len(SECTION_19_4):
    raise RuntimeError("pozycja rejestru wypadła z obu list statusów")
