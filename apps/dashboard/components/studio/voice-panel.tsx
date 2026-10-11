import type { PersonVoiceView } from "../../lib/control-plane";
import { moment } from "../../lib/studio-labels";
import { Pop } from "./pop";

/**
 * Panel „Głos” jednego profilu: podgląd, nowa wersja, historia i propozycja z profilu pilota.
 *
 * CZTERY POLA, TE SAME, CO W PROFILU PILOTA. Nazwy po ludzku, bo to czyta człowiek, a nie model:
 * „co ta osoba zna głęboko” zamiast `knows_deeply`. Kolejność jest kolejnością pytań, jakie
 * zadaje się komuś, kto ma w czyimś imieniu napisać komentarz.
 *
 * FORMULARZ NOWEJ WERSJI JEST WYPEŁNIONY BIEŻĄCĄ TREŚCIĄ. Poprawka jednego zdania nie może
 * wymagać przepisywania czterech pól: człowiek, który musi przepisać wszystko, albo tego nie
 * zrobi, albo skróci resztę. Zapis i tak tworzy NOWĄ wersję, a poprzednia zostaje w historii,
 * więc wypełnione pola nie są edycją w miejscu, tylko punktem wyjścia.
 *
 * PROPOZYCJA JEST OSOBNYM FORMULARZEM I MA WŁASNY PRZYCISK. Gdyby wypełniała ten sam formularz,
 * kliknięcie „Zapisz” po otwarciu panelu zapisałoby cudzą treść jako głos tej osoby, bez
 * decyzji, że to ta sama osoba. Dlatego treść pilota widać, jest podpisana datą i wersją,
 * a przejęcie jest osobnym kliknięciem.
 */
export function VoicePanel({
  failure,
  profileId,
  profileName,
  returnTo,
  view,
  workspaceId,
}: {
  readonly failure: string | undefined;
  readonly profileId: string;
  readonly profileName: string;
  readonly returnTo: string;
  readonly view: PersonVoiceView | undefined;
  readonly workspaceId: string;
}) {
  const current = view?.voice ?? null;
  const history = view?.history ?? [];
  const proposal = view?.proposal ?? null;
  const hidden = (
    <>
      <input name="action" type="hidden" value="save_voice" />
      <input name="workspace_id" type="hidden" value={workspaceId} />
      <input name="person_profile_id" type="hidden" value={profileId} />
      <input name="return_to" type="hidden" value={returnTo} />
    </>
  );

  return (
    <Pop
      label={`Głos profilu ${profileName}`}
      name={`voice-${profileId}`}
      side="right"
      title={`Głos: ${profileName}`}
    >
      {failure !== undefined ? (
        <p className="st-tag" data-tone="absent">
          Nie udało się odczytać głosu tej osoby: {failure}
        </p>
      ) : null}

      {failure === undefined && current === null ? (
        <p className="st-note">
          Ta osoba nie ma jeszcze opisanego głosu. Dopóki go nie ma, sesja agenta dostaje odmowę
          zamiast cudzego stylu i pisze rzeczowo, bez udawania kogokolwiek.
        </p>
      ) : null}

      {current !== null ? (
        <p className="st-note" data-voice-version={current.version}>
          Obowiązuje wersja {current.version}, zapisana {moment(current.created_at)}
          {current.created_by === null ? " przez operatora" : ""}.
        </p>
      ) : null}

      <form action="/studio/people" className="st-form" method="post">
        {hidden}
        <label className="st-field">
          <span>Co ta osoba zna głęboko</span>
          <textarea
            className="st-textarea"
            defaultValue={current?.knows_deeply ?? ""}
            maxLength={4000}
            name="knows_deeply"
            placeholder="Na czym zna się z pierwszej ręki, z czego ma doświadczenie."
            required
          />
        </label>
        <label className="st-field">
          <span>Jakie ma stanowiska</span>
          <textarea
            className="st-textarea"
            defaultValue={current?.positions ?? ""}
            maxLength={4000}
            name="positions"
            placeholder="Co uważa i gotowa jest powiedzieć publicznie."
            required
          />
        </label>
        <label className="st-field">
          <span>Czego nigdy nie powie</span>
          <textarea
            className="st-textarea"
            defaultValue={current?.would_never_claim ?? ""}
            maxLength={4000}
            name="would_never_claim"
            placeholder="Granica twarda: zdania, których agent nie napisze w jej imieniu."
            required
          />
        </label>
        <label className="st-field">
          <span>Próbki jej pisania (można zostawić puste)</span>
          <textarea
            className="st-textarea"
            defaultValue={current?.voice_samples ?? ""}
            maxLength={4000}
            name="voice_samples"
            placeholder="Kilka zdań tak, jak sama je napisała."
          />
        </label>
        <button className="st-btn" data-kind="primary" type="submit">
          Zapisz jako nową wersję
        </button>
      </form>

      {proposal !== null ? (
        <form action="/studio/people" className="st-form" data-voice-proposal="pilot" method="post">
          {hidden}
          <p className="st-note">
            Ta przestrzeń ma profil, z którym pisał pilot: wersja {proposal.version} z{" "}
            {moment(proposal.created_at)}. Jeśli to ten sam człowiek, możesz przejąć jego treść
            jako pierwszą wersję głosu tego profilu. Nic się nie zapisze, dopóki nie klikniesz.
          </p>
          <input name="knows_deeply" type="hidden" value={proposal.knows_deeply} />
          <input name="positions" type="hidden" value={proposal.positions} />
          <input name="would_never_claim" type="hidden" value={proposal.would_never_claim} />
          <input name="voice_samples" type="hidden" value={proposal.voice_samples} />
          <details className="st-note">
            <summary>Zobacz, co zostanie przejęte</summary>
            <p>
              <strong>Zna głęboko:</strong> {proposal.knows_deeply}
            </p>
            <p>
              <strong>Stanowiska:</strong> {proposal.positions}
            </p>
            <p>
              <strong>Nigdy nie powie:</strong> {proposal.would_never_claim}
            </p>
            <p>
              <strong>Próbki:</strong> {proposal.voice_samples === "" ? "brak" : proposal.voice_samples}
            </p>
          </details>
          <button className="st-btn" type="submit">
            Przejmij treść profilu pilota
          </button>
        </form>
      ) : null}

      {history.length > 1 ? (
        <details className="st-note">
          <summary>Historia głosu ({history.length})</summary>
          <ul>
            {history.map((version) => (
              <li key={version.voice_version_id}>
                wersja {version.version}, {moment(version.created_at)}: {version.knows_deeply}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </Pop>
  );
}
