import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const environment=loadEnv(mode,".","STUDYDY_E2E_");
  return {
  plugins: [react()],
  server: {
    proxy: {
      "/v2": {
        target: environment.STUDYDY_E2E_API_ORIGIN ?? "http://127.0.0.1:8001",
        changeOrigin: false,
      },
      "/v1": {
        target: environment.STUDYDY_E2E_API_ORIGIN ?? "http://127.0.0.1:8001",
        changeOrigin: false,
      },
    },
  },
  };
});
