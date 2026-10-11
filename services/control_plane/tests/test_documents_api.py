"""Dwie trasy odczytu dokumentów projektu przeciwko prawdziwemu PostgreSQL (P8 etap A).

CZEGO NIE DA SIĘ ZMIERZYĆ BEZ BAZY. `tests/test_documents_store.py` pilnuje zapisu, a testy
dashboardu pilnują tego, co ekran robi z gotową odpowiedzią. Zostaje warstwa pomiędzy i to ona
ma tu swoje przypadki: czy indeks pokazuje NAJNOWSZĄ wersję i liczbę zdań TEJ wersji, czy stara
wersja po przepisaniu nadal daje się przeczytać w niezmienionej postaci, czy pytanie o wersję,
której nie ma, dostaje liczbę zamiast pustki, i czy dokument cudzego projektu jest nie do
odróżnienia od nieistniejącego.

KAŻDY PRZYPADEK TO JEDEN SPOSÓB, W JAKI TEN ODCZYT MÓGŁBY SKŁAMAĆ:

  * pusty projekt pokazany jako 404, czyli „nie ma projektu" zamiast „jeszcze nic nie domknięto";
  * liczba zdań policzona ze wszystkich wersji naraz, więc rosnąca przy każdym przepisaniu;
  * kolejność ról wzięta z tego, jak wiersze leżą w tabeli, czyli zmienna i niczyja;
  * zdanie oddane bez podstawy, czyli twierdzenie, którego nie da się sprawdzić;
  * stara wersja zmieniona przez przepisanie, czyli brak czegokolwiek do porównania;
  * tożsamość dokumentu bez wersji pokazana jako pozycja indeksu, której nie da się otworzyć.

ZASIEW IDZIE PRZEZ `DocumentStore.rebuild_project`, nie przez surowe INSERT-y. Zasiew własnymi
wstawkami potrafi zbudować stan, którego produkt nigdy nie wyprodukuje, i wtedy trasa jest
mierzona na czymś, czego nikt nie zobaczy. Jedyne surowe wstawki tutaj są tam, gdzie generator z
założenia nie dociera: pusta sekcja i tożsamość dokumentu bez ani jednej wersji.
"""

from __future__ import annotations

import uuid

import pytest
from control_plane.documents import DocumentStore
from control_plane.knowledge.store import KnowledgeStore, NewItem, Scope

pytestmark = [pytest.mark.db, pytest.mark.p8]

WORKSPACES = "/api/workspaces"

#: Kolejność czytania projektu, ta sama, którą deklaruje `api/documents.py`. Wpisana tu jeszcze
#: raz z premedytacją: test, który importuje `ROLE_ORDER` z modułu, potwierdziłby tylko, że stała
#: jest równa samej sobie, i przepuściłby jej zmianę bez świadomej decyzji.
READING_ORDER = ("PROJECT_CARD", "WHAT_WORKS", "DECISIONS", "COMMUNITIES")


def _documents_path(workspace_id, project_id) -> str:
    return f"{WORKSPACES}/{workspace_id}/projects/{project_id}/documents"


async def _project(client, workspace_id, name: str | None = None) -> str:
    response = await client.post(
        f"{WORKSPACES}/{workspace_id}/projects",
        json={"name": name or f"projekt-{uuid.uuid4().hex[:8]}", "goal": "obserwuj i odpowiadaj"},
    )
    assert response.status_code == 201, response.content[:300]
    return response.json()["project"]["project_id"]


def _scope(tenant, project_id) -> Scope:
    return Scope(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=uuid.UUID(str(project_id)),
    )


async def _write(pool, tenant, project_id, *, kind, statement, stated_by="AGENT", payload=None):
    return await KnowledgeStore(pool).write(
        NewItem(
            kind=kind,
            statement=statement,
            stated_by=stated_by,
            project_id=uuid.UUID(str(project_id)),
            payload=payload or {},
        ),
        _scope(tenant, project_id),
    )


async def _rebuild(pool, tenant, project_id):
    return await DocumentStore(pool).rebuild_project(
        organization_id=tenant["organization"],
        workspace_id=tenant["workspace"],
        project_id=uuid.UUID(str(project_id)),
    )


async def _index(client, tenant, project_id) -> list[dict]:
    """Indeks dokumentów projektu, już rozpakowany. Powtarzany w niemal każdym przypadku."""
    response = await client.get(_documents_path(tenant["workspace"], project_id))
    assert response.status_code == 200, response.content[:300]
    return response.json()["dokumenty"]


async def _seeded_project(logged_in_client, pool, tenant) -> str:
    """Projekt z dwoma ustaleniami i kompletem dokumentów w wersji 1."""
    project_id = await _project(logged_in_client, tenant["workspace"])
    await _write(
        pool,
        tenant,
        project_id,
        kind="PREFERENCE",
        statement="piszemy krótko i bez żargonu",
        stated_by="USER",
        payload={"hypothesis": True},
    )
    await _write(
        pool,
        tenant,
        project_id,
        kind="INTERPRETATION",
        statement="wieczorem odzew jest mniejszy",
    )
    saved = await _rebuild(pool, tenant, project_id)
    assert saved, "zasiew nie zbudował ani jednego dokumentu"
    assert all(created for _, created in saved), "pierwszy przebieg tworzy wszystkie wersje"
    return project_id


async def test_an_empty_project_answers_with_an_empty_list_and_not_a_404(
    logged_in_client, tenant
) -> None:
    # Pierwszy dzień projektu. 404 kazałoby ekranowi zgadywać, czy projekt nie istnieje, czy
    # tylko nic jeszcze nie domknięto, a to są dwa różne zdania dla człowieka.
    project_id = await _project(logged_in_client, tenant["workspace"])

    response = await logged_in_client.get(_documents_path(tenant["workspace"], project_id))

    assert response.status_code == 200, response.content[:300]
    assert response.json() == {"dokumenty": []}


async def test_the_index_reads_in_the_product_order_no_matter_how_the_rows_lie(
    logged_in_client, pool, tenant
) -> None:
    """Kolejność ról jest decyzją produktu, nie własnością tabeli.

    SETUP JEST TU CAŁĄ POINTĄ. `_INDEX_SQL` nie ma `ORDER BY`, więc bez sortowania po stronie
    serwera odpowiedź miałaby kolejność, w jakiej wiersze leżą w stercie. Zwykły `UPDATE` w
    PostgreSQL zapisuje nową wersję krotki NA KOŃCU sterty, więc dotknięcie karty projektu
    przesuwa ją na koniec. Jeśli po tym trasa nadal oddaje kartę jako pierwszą, to znaczy, że
    sortuje naprawdę.
    """
    project_id = await _seeded_project(logged_in_client, pool, tenant)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE project_documents SET created_at = created_at "
            "WHERE project_id = $1 AND role = 'PROJECT_CARD'",
            uuid.UUID(project_id),
        )
        lying = [
            row["role"]
            for row in await conn.fetch(
                "SELECT role FROM project_documents WHERE project_id = $1",
                uuid.UUID(project_id),
            )
        ]
    # Warunek SETUPU, nie produktu: jeśli tu czerwone, to baza przestała oddawać wiersze w
    # kolejności sterty i przypadek trzeba ustawić inaczej, a nie „naprawiać" trasę.
    assert lying[0] != "PROJECT_CARD", f"setup nie przestawił wierszy, baza dała {lying}"

    response = await logged_in_client.get(_documents_path(tenant["workspace"], project_id))

    assert response.status_code == 200, response.content[:300]
    roles = [entry["rola"] for entry in response.json()["dokumenty"]]
    assert roles == list(READING_ORDER)


async def test_the_index_counts_the_sentences_of_the_newest_version_only(
    logged_in_client, pool, tenant
) -> None:
    # Liczba zdań policzona ze wszystkich wersji rosłaby przy każdym przepisaniu i po tygodniu
    # mówiłaby o dokumencie rzecz nieprawdziwą, nie przestając wyglądać wiarygodnie.
    project_id = await _seeded_project(logged_in_client, pool, tenant)
    await _write(
        pool,
        tenant,
        project_id,
        kind="INTERPRETATION",
        statement="wątki z liczbami z produkcji żyją dłużej",
    )
    again = await _rebuild(pool, tenant, project_id)
    grew = [version for version, created in again if created]
    assert grew, "drugi przebieg po nowym ustaleniu nie dał ani jednej nowej wersji"

    index = await _index(logged_in_client, tenant, project_id)
    by_id = {entry["document_id"]: entry for entry in index}

    before: dict[str, int] = {}
    after: dict[str, int] = {}
    for version in grew:
        entry = by_id[str(version.document_id)]
        assert entry["wersja"] == version.version == 2
        path = f"{_documents_path(tenant['workspace'], project_id)}/{version.document_id}"
        newest = await logged_in_client.get(path)
        first = await logged_in_client.get(f"{path}?wersja=1")
        assert newest.status_code == 200, newest.content[:300]
        assert first.status_code == 200, first.content[:300]
        after[str(version.document_id)] = len(newest.json()["zdania"])
        before[str(version.document_id)] = len(first.json()["zdania"])
        # Właściwy więz: liczba z indeksu opisuje TĘ wersję, którą indeks pokazuje.
        assert entry["liczba_zdan"] == after[str(version.document_id)]
    # A tu warunek, bez którego powyższe przechodziłoby także wtedy, gdyby obie wersje miały po
    # tyle samo zdań: wtedy „liczy najnowszą" i „liczy wszystkie" dają ten sam wynik i przypadek
    # niczego nie rozstrzyga.
    assert any(after[key] > before[key] for key in after), (
        f"żaden dokument nie urósł o zdanie: wersja 1 {before}, wersja 2 {after}"
    )


async def test_every_sentence_carries_the_findings_it_rests_on(
    logged_in_client, pool, tenant
) -> None:
    """Zdanie bez podstawy jest twierdzeniem, którego po miesiącu nikt nie odróżni od zmyślonego.

    Sprawdzane są TRZY rzeczy naraz, bo każda z nich osobno da się przejść pozorem: że podstawa
    jest niepusta, że deklarowana liczba zgadza się z listą, i że identyfikatory wskazują na
    ustalenia TEGO projektu, a nie na cokolwiek o kształcie uuid.
    """
    project_id = await _seeded_project(logged_in_client, pool, tenant)
    async with pool.acquire() as conn:
        known = {
            str(row["id"])
            for row in await conn.fetch(
                "SELECT id FROM knowledge_items WHERE project_id = $1", uuid.UUID(project_id)
            )
        }
    index = await _index(logged_in_client, tenant, project_id)
    assert index, "bez dokumentów ten przypadek nie sprawdziłby niczego"

    seen = 0
    for entry in index:
        detail = await logged_in_client.get(
            f"{_documents_path(tenant['workspace'], project_id)}/{entry['document_id']}"
        )
        assert detail.status_code == 200, detail.content[:300]
        for sentence in detail.json()["zdania"]:
            seen += 1
            assert sentence["podstawa"], f"zdanie bez podstawy: {sentence['tekst'][:60]!r}"
            assert sentence["liczba_podstaw"] == len(sentence["podstawa"])
            assert set(sentence["podstawa"]) <= known, sentence["podstawa"]
    assert seen > 0, "żaden dokument nie miał zdań, więc pętla wyżej nic nie sprawdziła"


async def test_an_older_version_stays_readable_and_unchanged_after_a_rewrite(
    logged_in_client, pool, tenant
) -> None:
    # Wersja, która zmienia się przy przepisaniu, nie pozwala odpowiedzieć na pytanie „co się
    # zmieniło od zeszłego tygodnia", a to jedyny powód, dla którego wersje w ogóle są.
    project_id = await _seeded_project(logged_in_client, pool, tenant)
    index = await _index(logged_in_client, tenant, project_id)
    card = next(entry for entry in index if entry["rola"] == "PROJECT_CARD")
    path = f"{_documents_path(tenant['workspace'], project_id)}/{card['document_id']}"
    before = (await logged_in_client.get(path)).json()

    await _write(
        pool,
        tenant,
        project_id,
        kind="PREFERENCE",
        statement="nie odpowiadamy w wątkach starszych niż dwa dni",
        stated_by="USER",
        payload={"hypothesis": True},
    )
    await _rebuild(pool, tenant, project_id)

    newest = (await logged_in_client.get(path)).json()
    old = (await logged_in_client.get(f"{path}?wersja=1")).json()
    assert newest["wersja"] == 2, newest["wersja"]
    assert old == before, "wersja 1 zmieniła się po przepisaniu"
    assert newest["zdania"] != old["zdania"], "przepisanie nie zmieniło treści, przypadek pusty"


async def test_a_version_that_does_not_exist_answers_with_the_newest_number(
    logged_in_client, pool, tenant
) -> None:
    # Pusta odpowiedź na pytanie o wersję 7 tam, gdzie jest jedna, kazałaby ekranowi zgadywać,
    # czy dokument zniknął, czy tylko numer jest zły. Liczba w zdaniu rozstrzyga to od razu.
    project_id = await _seeded_project(logged_in_client, pool, tenant)
    index = await _index(logged_in_client, tenant, project_id)
    document_id = index[0]["document_id"]

    response = await logged_in_client.get(
        f"{_documents_path(tenant['workspace'], project_id)}/{document_id}?wersja=7"
    )

    assert response.status_code == 404, response.content[:300]
    assert "najnowsza to 1" in response.json()["detail"], response.json()["detail"]


async def test_version_zero_is_refused_before_it_reaches_the_database(
    logged_in_client, pool, tenant
) -> None:
    # `?wersja=0` nie jest pomyłką w numerze, tylko pytaniem bez sensu: numeracja zaczyna się od
    # jedynki (więz z 0028). Odrzuca je walidacja zapytania, więc do bazy nie idzie nic.
    project_id = await _seeded_project(logged_in_client, pool, tenant)
    index = await _index(logged_in_client, tenant, project_id)

    response = await logged_in_client.get(
        f"{_documents_path(tenant['workspace'], project_id)}/{index[0]['document_id']}?wersja=0"
    )

    assert response.status_code == 422, response.content[:300]


async def test_a_document_identity_without_a_version_is_named_and_not_listed(
    logged_in_client, pool, tenant
) -> None:
    """Tożsamość bez treści to stan rzadki i trzeba go nazwać, a nie pokazać jako pustą pozycję.

    Wstawka jest tu surowa celowo: generator zawsze domyka wersję w tej samej transakcji, więc
    tego stanu nie da się wyprodukować przez produkt. Powstałby po przerwanym zapisie i wtedy
    „dokument bez treści" musi się różnić od „nie ma takiego dokumentu".
    """
    project_id = await _project(logged_in_client, tenant["workspace"])
    async with pool.acquire() as conn:
        document_id = await conn.fetchval(
            "INSERT INTO project_documents (organization_id, workspace_id, project_id, role) "
            "VALUES ($1, $2, $3, 'DECISIONS') RETURNING id",
            tenant["organization"],
            tenant["workspace"],
            uuid.UUID(project_id),
        )

    listed = await logged_in_client.get(_documents_path(tenant["workspace"], project_id))
    opened = await logged_in_client.get(
        f"{_documents_path(tenant['workspace'], project_id)}/{document_id}"
    )

    assert listed.json() == {"dokumenty": []}, "pozycja bez wersji nie ma czego pokazać na liście"
    assert opened.status_code == 404, opened.content[:300]
    assert "nie ma jeszcze żadnej wersji" in opened.json()["detail"]


async def test_a_document_of_another_project_is_not_readable_through_this_one(
    logged_in_client, pool, tenant
) -> None:
    # Granica jest po projekcie, nie po samym identyfikatorze dokumentu. Bez tego uuid z jednego
    # projektu otwierałby się pod adresem drugiego i indeks przestałby cokolwiek znaczyć.
    theirs = await _seeded_project(logged_in_client, pool, tenant)
    mine = await _project(logged_in_client, tenant["workspace"])
    document_id = (await _index(logged_in_client, tenant, theirs))[0]["document_id"]

    response = await logged_in_client.get(
        f"{_documents_path(tenant['workspace'], mine)}/{document_id}"
    )

    assert response.status_code == 404, response.content[:300]
    assert str(document_id) in response.json()["detail"]


async def test_an_empty_section_comes_back_as_nothing_and_not_as_an_empty_name(
    logged_in_client, pool, tenant
) -> None:
    """0028 pozwala na `section = ''`, bo nie każdy dokument dzieli się na części.

    Trasa ma oddać `null`, a nie pusty napis: ekran rysuje nagłówek wtedy i tylko wtedy, gdy jest
    co w nim napisać, a napis zerowej długości byłby pustą linią udającą podział.
    """
    project_id = await _seeded_project(logged_in_client, pool, tenant)
    index = await _index(logged_in_client, tenant, project_id)
    card = next(entry for entry in index if entry["rola"] == "PROJECT_CARD")
    async with pool.acquire() as conn:
        version_id = await conn.fetchval(
            "SELECT id FROM project_document_versions WHERE document_id = $1 AND version = 1",
            uuid.UUID(card["document_id"]),
        )
        basis = await conn.fetchval(
            "SELECT basis FROM project_document_sentences WHERE version_id = $1 LIMIT 1",
            version_id,
        )
        await conn.execute(
            "INSERT INTO project_document_sentences "
            "(organization_id, workspace_id, version_id, position, section, text, basis) "
            "VALUES ($1, $2, $3, 999, '', 'zdanie spoza sekcji', $4)",
            tenant["organization"],
            tenant["workspace"],
            version_id,
            basis,
        )

    detail = (
        await logged_in_client.get(
            f"{_documents_path(tenant['workspace'], project_id)}/{card['document_id']}"
        )
    ).json()

    loose = [one for one in detail["zdania"] if one["tekst"] == "zdanie spoza sekcji"]
    assert len(loose) == 1, detail["zdania"]
    assert loose[0]["sekcja"] is None
    # Pozycja 999 jest najwyższa w tej wersji, więc zdanie ma stać na końcu: to samo sprawdzenie
    # pokazuje, że trasa sortuje po `position`, a nie po kolejności odczytu z bazy.
    assert detail["zdania"][-1]["pozycja"] == 999


async def test_both_routes_refuse_without_a_session(logged_in_client, pool, tenant) -> None:
    """Granica operatora obowiązuje domyślnie, ale nowa trasa ma to potwierdzić własnym testem.

    Lista tras publicznych jest pisana ręcznie i jedna pomyłkowa pozycja otwiera dokumenty
    projektu na świat. Ten przypadek kosztuje kilka linii i zamyka tę drogę.

    Ciasteczko jest tu KASOWANE, a nie pomijane przez drugiego klienta: `logged_in_client` i
    `client` to ten sam obiekt, więc drugi klient byłby tym samym klientem z sesją i ten
    przypadek przechodziłby przez pomyłkę.
    """
    project_id = await _seeded_project(logged_in_client, pool, tenant)
    index = await _index(logged_in_client, tenant, project_id)
    logged_in_client.cookies.clear()

    listed = await logged_in_client.get(_documents_path(tenant["workspace"], project_id))
    opened = await logged_in_client.get(
        f"{_documents_path(tenant['workspace'], project_id)}/{index[0]['document_id']}"
    )

    assert listed.status_code == 401, listed.content[:300]
    assert opened.status_code == 401, opened.content[:300]
