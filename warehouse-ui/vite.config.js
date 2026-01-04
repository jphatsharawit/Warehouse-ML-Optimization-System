import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: '/ui/',              // ให้เส้นทาง assets ถูกตอนเสิร์ฟใต้ /ui
  plugins: [react()],
})
