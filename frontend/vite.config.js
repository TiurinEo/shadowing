import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      manifest: {
        name: "Shadowing",
        short_name: "Shadowing",
        display: "standalone",
        background_color: "#f6f3ee",
        theme_color: "#f6f3ee",
        start_url: "/",
      },
      workbox: { navigateFallbackDenylist: [/^\/api/] },
    }),
  ],
  server: { proxy: { "/api": "http://localhost:8000" }, allowedHosts: true },
});
