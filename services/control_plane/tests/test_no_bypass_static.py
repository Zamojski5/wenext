"""Does anything in this repository write outwards without the adapter? — DoD L5217, statically.

READ THIS FIRST, BECAUSE THE GREEN RESULT BELOW MEANS LESS THAN ITS NAME SUGGESTS.

**In phase E1.F1 the set of outbound writing paths is EMPTY.** There is not one line in this
repository that writes to an external platform: the Action Adapter is E1.F5 (L5323-5329),
capability probing and the headless capsule are E1.F2 (L5220-5240). So when
`test_no_writing_side_effect_bypasses_the_execution_intent_gate` passes, the sentence it has
proved is

    "there is nothing to bypass"

and NOT

    "nobody bypasses the gate".

Those are different sentences and only the second is the DoD. Reporting the first as the second
would be `all([])` raised to the level of a phase — the same shape as the soak that reported
"zero attempt-limit violations" without ever reaching the limit, and as the egress table whose
every row was caught by an earlier condition. This project has had eight false passes and this
file is a deliberate refusal to produce the ninth.

WHAT THE TEST DOES PROVE TODAY, which is worth having and is not nothing:

  1.  the instrument reads a non-empty set of production files, and the number is asserted, not
      assumed. A scan of zero files also reports zero violations;
  2.  its vocabulary matches real code — it finds the one dispatch inside `adapter.py`, so a
      vocabulary that had silently stopped matching anything would fail here;
  3.  the number of writing call sites outside the adapter is REPORTED on every run. Today it is
      zero because the numerator is empty.

**WHEN THIS TEST STARTS MEANING WHAT ITS NAME SAYS:** when that number stops being zero, i.e. in
E1.F2, when capability probing becomes the first real consumer of the gate. Until then, treat a
green run here as the absence of a question, not as an answer. The second test in this file is
what keeps the instrument honest in the meantime: it plants a bypass and watches the scan name it.
"""

from __future__ import annotations

import pathlib

from control_plane.execution.bypass_scan import (
    INTERNAL_CONTROL_HOSTS,
    INTERNAL_POST,
    PERMITTED_ACTUATOR_ADDRESSES,
    PERMITTED_AUTH_EXCHANGES,
    PERMITTED_INTERNAL_CONTROL,
    PERMITTED_LOCAL_EVALUATIONS,
    PERMITTED_PLATFORM_ACTUATORS,
    PERMITTED_WRITE_POINT,
    ScanResult,
    scan_source,
    scan_tree,
)

#: A file the per-file allowance names — the negative tests below plant foreign addresses INTO
#: this name, because the point of the address rule is that the file-level allowance does not
#: cover them.
AN_INTERNAL_CONTROL_FILE = "services/control_plane/control_plane/observe/capsule_client.py"

#: Four levels up from `services/control_plane/tests/test_no_bypass_static.py`.
REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]

#: The instrument's field of view has to be large enough to be worth calling a scan. Measured on
#: 2026-08-31: 79 production Python files under `services/`. The floor is deliberately far below
#: that — it exists to catch a scan that reads nothing (a moved root, a renamed subtree), not to
#: track the repository's size.
MINIMUM_FILES_A_REAL_SCAN_READS = 40


def test_no_writing_side_effect_bypasses_the_execution_intent_gate() -> None:
    """DoD L5217 — and see this module's docstring for what that means in a phase with no writers.

    The three assertions are in this order on purpose. The two that come first are about the
    INSTRUMENT: a scan that read no files, or whose vocabulary matches nothing any more, would
    report an empty violation list just as convincingly as a clean repository.
    """
    result: ScanResult = scan_tree(REPOSITORY_ROOT)

    assert len(result.files_read) >= MINIMUM_FILES_A_REAL_SCAN_READS, (
        f"skan przeczytał {len(result.files_read)} plików — to nie jest pomiar, tylko pusty "
        "przebieg. Zero naruszeń na zerze przeczytanych plików nie znaczy nic"
    )
    assert len(result.permitted) >= 1, (
        "skan nie znalazł ANI JEDNEGO miejsca zapisu nawet w adapterze, który jest jedynym "
        f"dozwolonym punktem ({PERMITTED_WRITE_POINT}). Słownik przestał pasować do kodu, więc "
        "pusta lista naruszeń poniżej jest własnością słownika, nie repozytorium"
    )

    # The named allowance is measured, not trusted: exactly ONE site, exactly the token exchange.
    # Outcome A (2026-09-03) added the first outbound `post` outside the adapter — the Reddit
    # OAuth token request — and this is where a second one in that file would be caught.
    exchanges = [str(site) for site in result.auth_exchanges]
    assert len(PERMITTED_AUTH_EXCHANGES) == 1, sorted(PERMITTED_AUTH_EXCHANGES)
    assert len(exchanges) == 1 and exchanges[0].endswith("wywołanie .post()"), (
        f"w plikach z PERMITTED_AUTH_EXCHANGES ma być DOKŁADNIE jedno wywołanie .post() — wymiana "
        f"tokenu — a jest: {exchanges}"
    )
    assert "community_observer/reddit.py" in exchanges[0]

    # D-026 (plan B, level 2): `.post()` calls to OUR OWN containers — the capsule's `/mode` and the
    # gateway's `/browser-sessions`. Named per file with an exact count; every one of them must be
    # a `.post()` (the vocabulary the scanner sees), never `request("POST", …)`; one more call in
    # a named file lands in `violations` below as excess.
    internal = result.internal_control
    per_file = {where: 0 for where in PERMITTED_INTERNAL_CONTROL}
    for site in internal:
        assert site.what == INTERNAL_POST, site
        per_file[site.where] += 1
    assert per_file == PERMITTED_INTERNAL_CONTROL, (
        f"miejsca POST do własnych kontenerów: jest {per_file}, dozwolone dokładnie "
        f"{PERMITTED_INTERNAL_CONTROL}"
    )
    assert result.internal_control_excess == ()
    # D-027: every one of those addresses is rooted in `self._base_url` and names no foreign host.
    # The list of hosts is the compose service list and nothing more.
    assert frozenset({"capsule", "gateway", "capsule-x", "gateway-x"}) == INTERNAL_CONTROL_HOSTS
    assert result.foreign_targets == (), [str(site) for site in result.foreign_targets]

    # D-028 (R4 minimal): the platform actuator in the capsule, counted to the site. Exactly one
    # file, exactly EIGHT input sites: three for a comment on the old interface (reply link,
    # textarea, save button), three for an own post (title, body, submit button, 0023) and two
    # for a comment on the NEW interface, plus loading and opening its editor (2026-09-23).
    # An eleventh lands in `violations`.
    # The number is repeated here on purpose, so that widening the allowance in the scanner is not
    # enough: a new write site has to be admitted in two places, by someone who meant to.
    # PODNIOSŁEM JE ŚWIADOMIE i mówię dlaczego: stary interfejs oddaje tej przeglądarce od 21.09
    # 403 i stronę blokady, także na stronie wątku, więc pilot nie ma dziś drogi do publikacji.
    # Nowa droga nie omija bramki: `comment_www` wchodzi tym samym `POST /act/comment` i tą samą
    # intencją wykonania, co stara. Zmienia się markup strony, nie zgoda ani jej zużycie.
    # And its ADDRESS (the endpoint paths, the token header) may be spelled only in
    # the adapter and in the two capsule files that serve it: a third file that knew the address
    # would be a second caller of the actuator, which is the bypass this test exists to catch.
    # 2026-09-26: the X actuator is a SECOND actuator file with FIVE sites (fill, the keyboard
    # fallback's two presses and one press_sequentially, one click), shared by its reply and its
    # own post. It sits behind `/act/x/reply` and `/act/x/post`, the same writer token and the
    # same `_act_allowed` gate; the paths are spelled only in the adapter and in `reader.py`.
    # 2026-09-27: a SIXTH X site, `set_input_files` of the one image an own post may carry, behind
    # the same `/act/x/post` and covered by the consent (the digest names the image's checksum).
    assert PERMITTED_PLATFORM_ACTUATORS == {
        "services/browser_supervisor/browser_supervisor/writer.py": 10,
        "services/browser_supervisor/browser_supervisor/x_writer.py": 6,
    }
    per_actuator = {where: 0 for where in PERMITTED_PLATFORM_ACTUATORS}
    for site in result.actuators:
        per_actuator[site.where] += 1
    assert per_actuator == PERMITTED_PLATFORM_ACTUATORS, per_actuator
    assert result.actuator_excess == ()
    addresses = {site.where for site in result.actuator_addresses}
    assert addresses == PERMITTED_ACTUATOR_ADDRESSES, sorted(addresses)
    # Script inside our own Chrome for a local purpose: one file, one call (the tracing flush).
    assert PERMITTED_LOCAL_EVALUATIONS == {
        "services/browser_supervisor/browser_supervisor/contract.py": 1
    }
    per_local = {where: 0 for where in PERMITTED_LOCAL_EVALUATIONS}
    for site in result.local_evaluations:
        per_local[site.where] += 1
    assert per_local == PERMITTED_LOCAL_EVALUATIONS, per_local
    assert result.local_evaluation_excess == ()

    # The number, reported rather than merely asserted away. Zero because every outbound write in
    # the repository is either the adapter's, the one named exchange above, or a counted internal
    # control call.
    outside = [str(site) for site in result.violations]
    assert outside == [], (
        f"{len(outside)} miejsc zapisu poza {PERMITTED_WRITE_POINT}: {outside}. Żaden zapisujący "
        "side effect nie omija ExecutionIntent (L5217) — każde z tych wywołań musi przejść przez "
        "SideEffectAdapter.perform z dowodem autoryzacji w sygnaturze"
    )


def test_the_scan_names_a_planted_bypass_instead_of_reporting_a_clean_repository() -> None:
    """The positive control OF THE INSTRUMENT, which is the only reason the test above is worth
    running while the repository has no writers.

    Two planted call sites, one from each half of the vocabulary — an HTTP verb and a CDP input
    method — because the two halves are matched by different code paths (`ast.Call` on an
    attribute, and a string constant). One planted case would leave the other half unmeasured.

    The count is asserted BEFORE the contents. `all([])` is `True` and an empty result would
    satisfy every `in` check written the other way round; that mistake has produced four false
    passes in this repository.
    """
    planted = """
import httpx


async def publish_without_asking_anybody(session, url, payload, page):
    await session.post(url, json=payload)
    await page.send("Input.dispatchKeyEvent", {"type": "char", "text": "x"})
"""

    found = scan_source(planted, where="services/przyszly_serwis/obejscie.py")

    assert len(found) == 2, (
        f"skaner znalazł {len(found)} miejsc w podłożonym obejściu zamiast dwóch: "
        f"{[str(site) for site in found]}. Instrument, który nie widzi podłożonego zapisu, nie "
        "jest dowodem niczego w teście powyżej"
    )
    descriptions = sorted(site.what for site in found)
    assert descriptions == ["metoda CDP 'Input.dispatchKeyEvent'", "wywołanie .post()"], (
        descriptions
    )
    assert [site.line for site in found] == [6, 7], [str(site) for site in found]


def test_a_platform_host_planted_in_an_internal_control_file_is_named() -> None:
    """D-027, the negative control of the ADDRESS rule. The per-file allowance says
    `capsule_client.py` may hold three `.post()` calls; without this rule a `.post()` to
    `oauth.reddit.com` placed there would be "the one permitted call" as far as a count can tell.

    Three plants, three shapes a bypass could take: a literal address in the call, a literal bound
    to a name in the same function, and an address built from something other than `_base_url`.
    The control case in the same file — an f-string rooted in `self._base_url` — must NOT be
    named, or the rule is noise and gets an exclusion list.
    """
    planted = """
class Klient:
    async def dozwolone(self, path):
        url = f"{self._base_url}{path}"
        await self._client.post(url, json={})

    async def literal(self):
        await self._client.post("https://oauth.reddit.com/api/comment", json={})

    async def przez_nazwe(self):
        cel = "https://www.reddit.com/api/submit"
        await self._client.post(cel)

    async def cudzy_adres(self, adres):
        await self._client.post(url=adres)
"""

    found = scan_source(planted, where=AN_INTERNAL_CONTROL_FILE)

    posts = [site for site in found if site.what == INTERNAL_POST]
    foreign = sorted((site.line, site.what) for site in found if site.what != INTERNAL_POST)
    assert len(posts) == 4, [str(site) for site in found]
    assert foreign == [
        (8, "adres .post() spoza usług compose: 'oauth.reddit.com'"),
        (12, "adres .post() spoza usług compose: 'www.reddit.com'"),
        (15, "adres .post() nie pochodzi z self._base_url"),
    ], foreign

    result = ScanResult(files_read=(AN_INTERNAL_CONTROL_FILE,), sites=found)
    assert len(result.foreign_targets) == 3
    assert all(site in result.violations for site in result.foreign_targets), (
        "cudzy adres w pliku z listy PERMITTED_INTERNAL_CONTROL jest naruszeniem, nie przydziałem"
    )
    # The same text in a file NOT on the list is just a `.post()` — the address rule is what the
    # per-file allowance costs, not a second vocabulary.
    elsewhere = scan_source(planted, where="services/przyszly_serwis/inny.py")
    assert {site.what for site in elsewhere} == {INTERNAL_POST}


def test_a_route_decorator_is_not_a_writing_call_site() -> None:
    """The one exclusion the scan makes, measured — otherwise it is an excuse, not a rule.

    `@router.post("/session")` is a call of a method named `post` and it registers an INBOUND
    route. Counting it would make every FastAPI application a bypass, and a scanner that cries
    wolf gets an exclusion list, and an exclusion list is where a real bypass goes to hide.

    The positive control sits in the same planted file: the SAME method name, called from inside
    the function body, must still be found. Without it this test would also pass on a scanner that
    had stopped looking at `post` altogether.
    """
    planted = """
router = object()


@router.post("/session")
async def open_session(client):
    await client.post("https://platforma.example/publikacja")
"""

    found = scan_source(planted, where="services/przyszly_serwis/trasa.py")

    assert len(found) == 1, [str(site) for site in found]
    assert found[0].line == 7, (
        f"skaner wskazał linię {found[0].line}. Linia 5 to dekorator trasy przychodzącej, "
        "linia 7 to prawdziwe wywołanie wychodzące"
    )
