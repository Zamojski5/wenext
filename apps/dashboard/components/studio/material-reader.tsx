import type { SourceSlice } from "../../lib/knowledge-schema";
import { AgentText } from "./conversation";

export function MaterialReader({ title, slice, error, back, generated = false }: {
  readonly title: string;
  readonly slice?: SourceSlice;
  readonly error?: string;
  readonly back: string;
  readonly generated?: boolean;
}) {
  const readable = slice !== undefined && slice.total_bytes > 0;
  const text = slice?.text ?? "";
  const heading = text.match(/^#{1,6}\s+([^\n]+)\n/);
  const plain = (value: string) => value.toLocaleLowerCase("pl").replace(/[^\p{L}\p{N}]/gu, "");
  const body = slice?.offset === 0 && heading && plain(heading[1] ?? "") === plain(title)
    ? text.slice(heading[0].length).trimStart() : text;
  return (
    <section className="st-material-reader" aria-label={title}>
      <div className="st__section-head">
        <span>{generated ? "Materiał roboczy agenta" : "Materiał projektu"}</span>
        <a href={back}>Wszystkie materiały</a>
      </div>
      <h2>{title}</h2>
      {readable ? <AgentText text={body} /> : (
        <p className="st-note">{error ?? "Treść materiału jest niedostępna. Wpis pozostał, ale nie udało się odczytać pliku."}</p>
      )}
      {readable && (slice.offset > 0 || slice.truncated) ? (
        <nav className="st-material-pages" aria-label="Fragmenty materiału">
          {slice.offset > 0 ? <a href={`?source=${slice.source_id}`}>Początek</a> : null}
          <span>{slice.truncated ? "Fragment materiału" : "Koniec materiału"}</span>
          {slice.truncated ? <a href={`?source=${slice.source_id}&offset=${slice.offset + slice.length}`}>Czytaj dalej →</a> : null}
        </nav>
      ) : null}
    </section>
  );
}
