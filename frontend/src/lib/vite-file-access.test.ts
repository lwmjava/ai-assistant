import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { createServer, type ViteDevServer } from 'vite'
import { afterAll, beforeAll, expect, test } from 'vitest'

let root: string
let server: ViteDevServer
let base: string

beforeAll(async () => {
  root = await mkdtemp(join(tmpdir(), 'vite-access-'))
  await writeFile(join(root, 'blocked.crt'), 'synthetic-denied-file-marker')
  await writeFile(join(root, 'public.txt'), 'public-content')
  server = await createServer({
    root,
    configFile: false,
    envFile: false,
    logLevel: 'silent',
    server: {
      host: '127.0.0.1', port: 0, hmr: false,
      fs: { strict: true, allow: [root], deny: ['**/*.crt'] },
    },
    optimizeDeps: { noDiscovery: true, entries: [] },
  })
  await server.listen()
  const address = server.httpServer?.address()
  if (!address || typeof address === 'string') throw new Error('Missing loopback listener')
  base = `http://127.0.0.1:${address.port}`
})

afterAll(async () => {
  await server?.close()
  if (root && dirname(resolve(root)) === resolve(tmpdir())) {
    await rm(root, { recursive: true, force: true })
  }
})

test('allowed static files remain readable', async () => {
  const response = await fetch(`${base}/public.txt`)
  expect(response.status).toBe(200)
  expect(await response.text()).toBe('public-content')
})

test.each(['', '?raw', '?import&raw', '?import&url&inline'])(
  'denied files remain blocked with query %s', async (query) => {
    const response = await fetch(`${base}/blocked.crt${query}`)
    expect(response.status).toBe(403)
    expect(await response.text()).not.toContain('synthetic-denied-file-marker')
  },
)
