const backend = process.env.BACKEND_URL ?? "http://localhost:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  poweredByHeader: false,
  experimental: {
    // Transcript uploads and extraction can run for minutes (the old nginx config allowed 600s).
    proxyTimeout: 600_000,
    // With a proxy present, Next buffers proxied request bodies up to this size (default 10MB).
    // Matches the 100 MiB upload limit in the UI and the old nginx client_max_body_size.
    proxyClientMaxBodySize: "110mb",
  },
  async rewrites() {
    // Filesystem routes win over this rewrite, so /api/console/chat is served by Next itself
    // and every other /api/* call is proxied to the backend.
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "DENY" },
        ],
      },
    ];
  },
};

export default nextConfig;
