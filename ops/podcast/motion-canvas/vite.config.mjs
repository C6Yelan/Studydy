import {defineConfig} from 'vite';
import motionCanvas from '@motion-canvas/vite-plugin';
import ffmpeg from '@motion-canvas/ffmpeg';
export default defineConfig({plugins:[motionCanvas.default({project:'./project.ts',editor:new URL('./runner.js',import.meta.url).pathname,output:'./output'}),ffmpeg.default()],server:{host:'127.0.0.1',port:4194,strictPort:true,watch:{ignored:['**/output/**']}},esbuild:{jsx:'automatic',jsxImportSource:'@motion-canvas/2d/lib'}});
