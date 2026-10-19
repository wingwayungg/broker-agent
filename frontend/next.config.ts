import type { NextConfig } from "next";

const config: NextConfig = {
    // A self-contained server.js for the Docker image (no node_modules install).
    output: "standalone",
    // Traced from here, not an inferred monorepo root, so server.js sits at the
    // top of .next/standalone.
    outputFileTracingRoot: __dirname,
    // gzip holds back the proxied event streams until a run ends instead of
    // passing tokens through as they arrive.
    compress: false,
    async redirects() {
        // Conversation links from before conversations had their own path.
        return [
            {
                source: "/",
                has: [{ type: "query", key: "thread_id", value: "(?<id>[0-9a-fA-F-]{36})" }],
                destination: "/c/:id",
                permanent: true,
            },
        ];
    },
};

export default config;
