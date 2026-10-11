/**
 * The two screens a studio page shows instead of its content, kept apart on purpose.
 *
 * "The read did not happen" and "the read happened and the answer is: there is no such thing" are
 * different facts, and a product that renders them the same way teaches its user to distrust
 * both. It is the same distinction `lib/guard.ts` makes between a logged-out session and an
 * unreachable control plane, one layer up.
 *
 * Neither renders the shell: the shell needs the workspace, and in both of these cases there is
 * no workspace to draw it from.
 */

export function StudioUnreachable({ reason }: { readonly reason: string }) {
  return (
    <main className="st" data-st-route="unreachable">
      <section className="st__main">
        <div className="st__content">
          <h1 className="st__title">Brak odpowiedzi</h1>
          <p className="st__lede">
            Odczyt z control plane się nie udał, więc ten ekran nic nie twierdzi o stanie
            workspace&apos;u. Powód: {reason}
          </p>
          <p className="st__lede">
            <a className="st-btn" data-kind="quiet" href="/">
              Spróbuj ponownie
            </a>
          </p>
        </div>
      </section>
    </main>
  );
}

export function StudioMissing({
  what,
}: {
  readonly what: "workspace" | "projekt" | "profil" | "rozmowa";
}) {
  return (
    <main className="st" data-st-route="missing">
      <section className="st__main">
        <div className="st__content">
          <h1 className="st__title">Nie ma takiego zasobu</h1>
          <p className="st__lede">
            Control plane odpowiedział, że w tej organizacji nie ma tego zasobu ({what}). To jest
            odczytany fakt, nie awaria: adres może pochodzić z innego workspace&apos;u.
          </p>
          <p className="st__lede">
            <a className="st-btn" data-kind="quiet" href="/">
              Wróć do swoich workspace&apos;ów
            </a>
          </p>
        </div>
      </section>
    </main>
  );
}
