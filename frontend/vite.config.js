import {defineConfig} from 'vite';
// The API port moves (Windows reserves 8000 on some boots), so let the dev server follow it.
export default defineConfig({server:{proxy:{'/api': process.env.VITE_API_TARGET || 'http://127.0.0.1:8000'}}});
