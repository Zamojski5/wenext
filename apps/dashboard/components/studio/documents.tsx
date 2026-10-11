import {
  documentRoleLabel,
  documentSourceLabel,
  type DocumentDetail,
  type DocumentEntry,
} from "../../lib/documents-schema";
import { moment } from "../../lib/studio-labels";

/**
 * „Dokumenty projektu": pamięć projektu w postaci, którą człowiek czyta, a nie przegląda (P8 A).
 *
 * PO CO TO ISTNIEJE. Rejestr ustaleń rośnie i po dwóch tygodniach nikt go nie czyta: sto zdań
 * posortowanych po dacie to nie jest odpowiedź na pytanie „co my właściwie o tym projekcie
 * wiemy". Dokument jest widokiem tego samego rejestru, ułożonym w role: karta projektu, co
 * działa, decyzje, społeczności.
 *
 * DOKUMENT NIE JEST DRUGĄ PAMIĘCIĄ, i widać to na ekranie: przy każdym zdaniu stoi liczba
 * ustaleń, z których ono wynika. Zdanie bez podstawy nie powstaje (pilnuje tego baza), a liczba
 * jest tu po to, żeby czytający wiedział, czy patrzy na wniosek z jednego pomiaru, czy z
 * dwudziestu. Sam odnośnik do ustaleń dochodzi w etapie C razem z porównywaniem wersji.
 *
 * TA SEKCJA NIE JEST MIEJSCEM PIERWSZEGO WPISU I NIE BĘDZIE (D-044). Teza projektu, hipotezy
 * i ustalenia powstają W ROZMOWIE: agent pyta, proponuje i zapisuje, a człowiek potwierdza
 * słowami. Ekran jest widokiem tego, co z tej rozmowy zostało, więc pusta sekcja mówi, skąd
 * dokumenty się wezmą, a nie prosi o wypełnienie formularza. W etapie C dochodzi przycisk
 * „Przepisz dokumenty", ale on ZLECA JOB, a nie przyjmuje treści, i to jest cała różnica.
 *
 * ETAP A NIE MA ŻADNEGO PRZYCISKU. Nie ma też porównywania wersji; jedno i drugie wchodzi
 * później. Sekcja ma na razie pokazywać, nie działać, i jest na to test.
 */
export function DocumentsBlock({
  documents,
  open,
  reason,
}: {
  readonly documents?: readonly DocumentEntry[];
  /** Rozwinięty dokument, gdy ekran go doczytał. Bez niego lista jest listą tytułów. */
  readonly open?: DocumentDetail;
  readonly reason?: string;
}) {
  if (reason !== undefined) {
    return (
      <section className="st__section" data-block="dokumenty" id="dokumenty">
        <div className="st__section-head">Dokumenty projektu</div>
        <p className="st-tag" data-tone="absent">
          Nie udało się odczytać dokumentów: {reason}. To nie znaczy, że ich nie ma.
        </p>
      </section>
    );
  }
  if (documents === undefined) return null;

  return (
    <section className="st__section" data-block="dokumenty" id="dokumenty">
      <div className="st__section-head">Dokumenty projektu</div>

      {documents.length === 0 ? (
        <p className="st-empty" data-documents="0">
          Nie ma jeszcze żadnego dokumentu. Powstaną z rozmowy i z pracy agenta: to on zapisuje
          ustalenia i układa je w dokumenty, gdy domknie pracę. Nie ma tu nic do wpisania.
        </p>
      ) : (
        <div className="st-surface">
          {documents.map((document) => {
            const opened = open?.document_id === document.document_id ? open : undefined;
            return (
              <details
                className="st-doc"
                data-document={document.document_id}
                data-role={document.rola}
                key={document.document_id}
                open={opened !== undefined}
              >
                <summary>
                  <strong>{documentRoleLabel(document.rola)}</strong>
                  <small>
                    {`wersja ${document.wersja}, ${moment(document.wygenerowano)}, ` +
                      `${document.liczba_zdan} ${zdania(document.liczba_zdan)}, ` +
                      documentSourceLabel(document.zrodlo)}
                  </small>
                </summary>
                {opened === undefined ? (
                  // Treść doczytuje strona projektu jednym zapytaniem na raz. Dopóki jej nie ma,
                  // mówimy o tym wprost, zamiast pokazywać pusty rozwinięty blok.
                  <p className="st-note">
                    <a href={`?dokument=${document.document_id}`}>Pokaż treść</a>
                  </p>
                ) : (
                  <Sentences detail={opened} />
                )}
              </details>
            );
          })}
        </div>
      )}
    </section>
  );
}

/** Odmiana przez liczbę. „2 zdań" zdradza, że zdanie skleiła maszyna. */
function zdania(count: number): string {
  if (count === 1) return "zdanie";
  const last = count % 10;
  const teen = count % 100 >= 10 && count % 100 < 20;
  return !teen && last >= 2 && last <= 4 ? "zdania" : "zdań";
}

function Sentences({ detail }: { readonly detail: DocumentDetail }) {
  let section: string | null = null;
  return (
    <div className="st-doc__body">
      <p className="st-note">{detail.tytul}</p>
      {detail.zdania.map((sentence) => {
        // Pusty napis to brak sekcji, nie sekcja bez nazwy: 0028 pozwala na `section = ''`,
        // a nagłówek bez tekstu byłby pustą linią udającą podział dokumentu.
        const named = sentence.sekcja !== null && sentence.sekcja.trim() !== "";
        const heading = named && sentence.sekcja !== section;
        section = named ? sentence.sekcja : section;
        return (
          <div key={`${sentence.pozycja}`}>
            {heading ? <h3 className="st-doc__section">{sentence.sekcja}</h3> : null}
            <p className="st-doc__sentence" data-basis={sentence.liczba_podstaw}>
              {sentence.tekst}{" "}
              {/* Liczba ustaleń pod zdaniem: czytający ma wiedzieć, czy to wniosek z jednego
                  pomiaru, czy z dwudziestu. Odnośnik do samych ustaleń dochodzi w etapie C. */}
              <span className="st-doc__basis">
                {`(${sentence.liczba_podstaw} ${ustalenia(sentence.liczba_podstaw)})`}
              </span>
            </p>
          </div>
        );
      })}
    </div>
  );
}

function ustalenia(count: number): string {
  if (count === 1) return "ustalenie";
  const last = count % 10;
  const teen = count % 100 >= 10 && count % 100 < 20;
  return !teen && last >= 2 && last <= 4 ? "ustalenia" : "ustaleń";
}
