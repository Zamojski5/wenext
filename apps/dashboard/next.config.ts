import type { NextConfig } from "next";

/**
 * `output: "standalone"` is what makes the two-stage Dockerfile possible at all: the build
 * emits a self-contained `.next/standalone` tree with its own `node_modules`, so the runtime
 * stage copies that instead of installing dependencies a second time — the same shape as
 * services/stream_gateway/Dockerfile, which copies a finished `/opt/venv` out of its builder.
 *
 * `poweredByHeader: false`: the dashboard is the only Next.js surface on the loopback port
 * (D-014) and has no reason to announce its framework.
 */
const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
};

export default nextConfig;
