import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv, type Plugin } from "vite";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";

const require = createRequire(import.meta.url);
const vadRoot = dirname(require.resolve("@ricky0123/vad-web/package.json"));
const ortDist = dirname(require.resolve("onnxruntime-web/wasm"));
const vadVersion = JSON.parse(readFileSync(join(vadRoot, "package.json"), "utf8")).version;
const ortVersion = JSON.parse(readFileSync(join(ortDist, "../package.json"), "utf8")).version;
const voiceAssetBase = `/voice-assets/vad-${vadVersion}-ort-${ortVersion}/`;

// 與應用程式一同發布，模型與 WASM 不向 CDN 請求，也不把錄音送到外部辨識服務。
function voiceAssets(): Plugin {
  const files = new Map([
    ["silero_vad_v6.onnx", join(vadRoot, "dist/silero_vad_v6.onnx")],
    ["vad.worklet.bundle.min.js", join(vadRoot, "dist/vad.worklet.bundle.min.js")],
    ["ort-wasm-simd-threaded.wasm", join(ortDist, "ort-wasm-simd-threaded.wasm")],
    ["ort-wasm-simd-threaded.mjs", join(ortDist, "ort-wasm-simd-threaded.mjs")],
  ]);
  return {
    name: "self-hosted-voice-assets",
    generateBundle() {
      for (const [name, path] of files) {
        this.emitFile({type: "asset", fileName: voiceAssetBase.slice(1) + name, source: readFileSync(path)});
      }
    },
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        const path = files.get(request.url?.split("?")[0].replace(voiceAssetBase, "") ?? "");
        if (!request.url?.startsWith(voiceAssetBase) || !path) return next();
        response.setHeader("Content-Type", path.endsWith(".wasm") ? "application/wasm" : path.endsWith(".onnx") ? "application/octet-stream" : "text/javascript");
        response.end(readFileSync(path));
      });
    },
  };
}

export default defineConfig(({ mode }) => {
  const environment = loadEnv(mode, ".", "STUDYDY_E2E_");
  return {
    plugins: [react(), voiceAssets()],
    define: { __VOICE_ASSET_BASE__: JSON.stringify(voiceAssetBase) },
    server: {
      proxy: {
        "/v1": {
          target: environment.STUDYDY_E2E_API_ORIGIN ?? "http://127.0.0.1:8001",
          changeOrigin: false,
        },
      },
    },
  };
});
