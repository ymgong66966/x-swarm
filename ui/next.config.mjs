/** @type {import('next').NextConfig} */
const nextConfig = {
  // Every page reads the database, so nothing here can be prerendered at build time.
  experimental: { serverActions: { bodySizeLimit: "1mb" } },
};

export default nextConfig;
