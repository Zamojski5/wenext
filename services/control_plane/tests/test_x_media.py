"""One image under an own X post (27.09): the consented field, the ports and their refusals.

* `join_media` and `split_media` are inverses, and a malformed image line stays text a human sees;
* `image_type_of` reads PNG and JPEG from the bytes themselves and nothing else;
* the own post port types the text WITHOUT the image line and sends the image beside it, read by
  its loader; with no loader it refuses before calling the capsule;
* the reply port refuses any image at the last door, whatever let it through before.
"""

from __future__ import annotations

import base64
import json
from uuid import UUID, uuid4

import httpx
import pytest
from community_os_contracts.enums import SemanticCapability
from community_os_contracts.execution import ApprovalHashInput
from control_plane.execution.adapter import (
    ACT_X_POST_PATH,
    PublicationRefused,
    XCapsuleOwnPostPort,
    XCapsuleReplyPort,
)
from control_plane.pilot.publication import MediaRef, image_type_of, join_media, split_media

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
MEDIA = MediaRef(UUID("0f8fad5b-d9cb-469f-a165-70867728950e"), "sha256:" + "ab" * 32)
TEXT = "Nowy model agentów wyszedł dziś rano. Najciekawsze jest to, czego nie pokazali."


def test_the_image_line_goes_on_and_comes_off_exactly() -> None:
    field = join_media(TEXT, MEDIA)
    assert field.endswith(f"\n\n[obraz {MEDIA.artifact_id} {MEDIA.checksum}]")
    assert split_media(field) == (TEXT, MEDIA)
    assert split_media(TEXT) == (TEXT, None), "pole bez linii obrazu to sam tekst"


def test_a_malformed_image_line_is_text_and_a_checksum_must_be_sha256() -> None:
    broken = f"{TEXT}\n\n[obraz {MEDIA.artifact_id} sha256:za-krotka]"
    assert split_media(broken) == (broken, None), "zła linia zostaje w tekście, widoczna"
    with pytest.raises(ValueError):
        join_media(TEXT, MediaRef(uuid4(), "md5:abc"))


def test_the_image_type_is_read_from_the_bytes() -> None:
    assert image_type_of(PNG) == "image/png"
    assert image_type_of(JPEG) == "image/jpeg"
    assert image_type_of(b"GIF89a....") is None
    assert image_type_of(b"") is None


def _content(text_and_media: str, *, reply: bool = False) -> ApprovalHashInput:
    return ApprovalHashInput(
        organization=uuid4(),
        workspace=uuid4(),
        assignment_version=uuid4(),
        account=uuid4(),
        platform="X",
        action_type=SemanticCapability.SUBMIT_COMMENT if reply else SemanticCapability.SUBMIT_POST,
        target_external_id="x:2103587050346250341" if reply else "x:@demo_founder",
        target_url=(
            "https://x.com/jack/status/2103587050346250341" if reply else "https://x.com/demo_founder"
        ),
        source_fingerprint=f"session:{uuid4()}",
        text_and_media=text_and_media,
        intelligence_profile_version=uuid4(),
        plan_version=uuid4(),
        memory_snapshot=uuid4(),
        adapter_version="wenext-session-draft/1",
    )


class Capsule:
    """The actuator over `httpx.MockTransport`: records each body, answers like the real one."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.url.path, json.loads(request.content)))
        return httpx.Response(
            200,
            json={
                "post_id": "2104999999999999999",
                "permalink": "https://x.com/demo_founder/status/2104999999999999999",
                "account": "demo_founder",
                "evidence": "toast",
            },
        )

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


async def test_an_own_post_with_an_image_types_the_text_and_sends_the_image_beside_it() -> None:
    capsule = Capsule()
    asked: list[MediaRef] = []

    async def load(media: MediaRef) -> tuple[bytes, str]:
        asked.append(media)
        return PNG, "image/png"

    port = XCapsuleOwnPostPort(
        "http://capsule-x:8765",
        token="w",
        expected_account="demo_founder",
        client=capsule.client(),
        media_loader=load,
    )
    evidence = await port.submit(_content(join_media(TEXT, MEDIA)))

    assert asked == [MEDIA], "obraz czyta loader, po artefakcie i sumie ze zgody"
    [(path, body)] = capsule.calls
    assert path == ACT_X_POST_PATH
    assert body["text"] == TEXT, "linia obrazu nigdy nie trafia do okna posta"
    assert base64.b64decode(body["image_b64"]) == PNG
    assert body["image_type"] == "image/png"
    assert body["account"] == "demo_founder"
    assert evidence.external_id == "x:2104999999999999999"


async def test_an_own_post_without_an_image_sends_no_image_fields() -> None:
    capsule = Capsule()
    port = XCapsuleOwnPostPort(
        "http://capsule-x:8765", token="w", expected_account="demo_founder", client=capsule.client()
    )
    await port.submit(_content(TEXT))
    [(_, body)] = capsule.calls
    assert body == {"text": TEXT, "account": "demo_founder"}


async def test_an_image_the_port_cannot_read_is_refused_before_the_capsule() -> None:
    capsule = Capsule()
    port = XCapsuleOwnPostPort(
        "http://capsule-x:8765", token="w", expected_account="demo_founder", client=capsule.client()
    )
    with pytest.raises(PublicationRefused, match="nic nie wysłano"):
        await port.submit(_content(join_media(TEXT, MEDIA)))
    assert capsule.calls == [], "bez loadera nic nie idzie do kapsuły"


async def test_a_loader_refusal_stops_the_post_before_the_capsule() -> None:
    capsule = Capsule()

    async def refuse(media: MediaRef) -> tuple[bytes, str]:
        raise PublicationRefused("obraz różni się od zatwierdzonego; nic nie wysłano")

    port = XCapsuleOwnPostPort(
        "http://capsule-x:8765",
        token="w",
        expected_account="demo_founder",
        client=capsule.client(),
        media_loader=refuse,
    )
    with pytest.raises(PublicationRefused, match="różni się"):
        await port.submit(_content(join_media(TEXT, MEDIA)))
    assert capsule.calls == []


async def test_a_reply_never_carries_an_image() -> None:
    capsule = Capsule()
    port = XCapsuleReplyPort(
        "http://capsule-x:8765", token="w", expected_account="demo_founder", client=capsule.client()
    )
    with pytest.raises(PublicationRefused, match="tylko pod własnym postem"):
        await port.submit(_content(join_media(TEXT, MEDIA), reply=True))
    assert capsule.calls == []
