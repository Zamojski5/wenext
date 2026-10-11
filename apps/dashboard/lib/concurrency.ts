/**
 * Map over a list with a ceiling on how many calls are in flight at once.
 *
 * WHY A CEILING AT ALL. `app/jobs/page.tsx` makes one detail read per listed job to fill the lease
 * columns, and the list can hold `JOBS_PAGE_LIMIT` (100) rows. `Promise.all` over all of them
 * would open a hundred simultaneous requests against a control plane whose connection pool is
 * `max_size=8` (`control_plane.auth.pool_for`): the requests would not fail, they would queue
 * inside the API and turn one page view into a burst that competes with the dispatcher for
 * connections. Eight matches that pool, so the page never asks for more than the API can serve at
 * once.
 *
 * ORDER IS PRESERVED. The result is indexed by the input position, not by completion time — a
 * table whose rows arrived in whichever order the network chose would reorder itself between two
 * refreshes of the same data.
 *
 * ONE FAILING ELEMENT DOES NOT FAIL THE BATCH, because the callers of this function return a
 * per-element result rather than throwing: a job whose detail read failed renders "nie odczytano"
 * in three cells while the rest of the queue is still shown. A whole page refusing to render
 * because one row's lease could not be read would hide ninety-nine rows to be precise about one.
 */
export async function mapWithConcurrency<Input, Output>(
  inputs: readonly Input[],
  limit: number,
  worker: (input: Input) => Promise<Output>,
): Promise<Output[]> {
  if (limit < 1) {
    throw new Error(`limit równoległości musi być >= 1, dostano ${limit}`);
  }
  const results: Output[] = new Array<Output>(inputs.length);
  let next = 0;

  async function run(): Promise<void> {
    for (;;) {
      const index = next;
      next += 1;
      if (index >= inputs.length) {
        return;
      }
      // Non-null assertion avoided on purpose: `index < inputs.length` is checked above, and a
      // cast here would be the one place this helper could silently hand `undefined` to a worker.
      const input = inputs[index] as Input;
      results[index] = await worker(input);
    }
  }

  await Promise.all(Array.from({ length: Math.min(limit, inputs.length) }, run));
  return results;
}

/** Matches `max_size=8` of the control plane's connection pool. See the module docstring. */
export const CONTROL_PLANE_CONCURRENCY = 8;
