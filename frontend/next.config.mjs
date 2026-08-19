/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    const target = process.env.API_PROXY_TARGET;
    const rewrites = [];
    if (target) {
      rewrites.push({ source: "/api/:path*", destination: `${target}/api/:path*` });
    }
    return rewrites;
  },
};

export default nextConfig;
