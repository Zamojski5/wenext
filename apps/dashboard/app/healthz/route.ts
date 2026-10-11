/**
 * Liveness of THIS process, and nothing else.
 *
 * `docker-compose.yml` waits on this route (`node -e fetch("http://127.0.0.1:3000/healthz")`)
 * before calling the dashboard healthy, which is the only reason it exists.
 *
 * WHAT IT MUST NOT DO: report on the control plane. A healthcheck that called `api` would go
 * green or red for reasons that have nothing to do with the process it is supposed to be
 * probing — and, worse, a dashboard that answered 200 here while `api` was unreachable would
 * be claiming more than it measured. The compose file already sequences the two:
 * `depends_on: api: condition: service_healthy`.
 *
 * `force-dynamic` because a prerendered healthcheck is a healthcheck of the build, not of the
 * running server.
 */
export const dynamic = "force-dynamic";

export function GET(): Response {
  return new Response(
    "dashboard: proces odpowiada. Ta trasa nie sprawdza control plane. Patrz depends_on w compose.\n",
    {
      status: 200,
      headers: {
        "content-type": "text/plain; charset=utf-8",
        "cache-control": "no-store",
      },
    },
  );
}
