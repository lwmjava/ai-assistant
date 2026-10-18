const { chromium } = require('C:/Users/123/.agents/skills/gstack/node_modules/playwright-core');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const out = 'data/sse001-acceptance-20261018';
fs.mkdirSync(out, { recursive: true });
(async () => {
  const browser = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true });
  const page = await browser.newPage({ viewport: { width: 1100, height: 850 } });
  const requests = [], errors = [];
  const histories = [];
  page.on('response', async r => {
    if (/\/api\/v1\/chat\/conversations\/[^/]+$/.test(new URL(r.url()).pathname) && r.status() === 200) {
      histories.push(await r.json().catch(() => null));
    }
  });
  page.on('request', r => {
    const u = new URL(r.url());
    if (u.pathname.startsWith('/api/')) requests.push({ method: r.method(), path: u.pathname, cursor: r.headers()['last-event-id'] || '', at: Date.now() });
  });
  page.on('pageerror', e => errors.push(e.message));
  try {
    await page.goto('http://127.0.0.1:18767/login');
    await page.locator('input[name="username"]').fill('ui-fixture');
    await page.locator('input[name="password"]').fill('synthetic-placeholder');
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await page.waitForURL('**/chat');
    fs.writeFileSync(`${out}/initial-dom.txt`, await page.locator('body').innerText());
    console.log(await page.locator('textarea').evaluateAll(es => es.map(e => ({ placeholder: e.placeholder, aria: e.getAttribute('aria-label') }))));
    console.log(await page.getByRole('button').allTextContents());
    await page.locator('textarea').fill('UI_REPLAY_PROBE 请验证流式恢复');
    await page.locator('textarea').press('Enter');
    await page.getByText('隔离界面验证回复：流式事件到达页面，使用模拟模型验证协议，不代表真实模型质量。', { exact: false }).waitFor({ timeout: 90000 });
    await page.waitForTimeout(1500);
    const posts = requests.filter(r => r.method === 'POST' && r.path.endsWith('/chat/stream'));
    const replay = requests.filter(r => r.method === 'GET' && /\/runs\/[^/]+\/events$/.test(r.path));
    assert.equal(posts.length, 1, 'recovery must not submit again');
    assert.equal(replay.length, 1, 'one replay subscription');
    assert.match(replay[0].cursor, /^[a-f0-9-]+:\d+$/i);
    const body = await page.locator('body').innerText();
    assert.equal(body.split('隔离界面验证回复：').length - 1, 1);
    await page.screenshot({ path: `${out}/replay-complete.png`, fullPage: true });
    fs.writeFileSync(`${out}/replay-complete.txt`, body);
    await page.reload();
    await page.getByRole('button', { name: '打开会话列表', exact: true }).click();
    await page.getByRole('button', { name: /^UI_REPLAY_PROBE/ }).click();
    await page.getByText('隔离界面验证回复：流式事件到达页面，使用模拟模型验证协议，不代表真实模型质量。', { exact: false }).waitFor();
    fs.writeFileSync(`${out}/after-refresh.txt`, await page.locator('body').innerText());
    await page.screenshot({ path: `${out}/after-refresh.png`, fullPage: true });
    await page.locator('textarea').fill('CANCEL_PROBE 验证停止后保存部分正文');
    await page.locator('textarea').press('Enter');
    await page.waitForTimeout(4500);
    await page.getByRole('button', { name: '停止生成', exact: true }).click();
    await page.getByText('已停止', { exact: true }).waitFor();
    await page.waitForTimeout(500);
    const cancelled = await page.locator('body').innerText();
    fs.writeFileSync(`${out}/cancelled.txt`, cancelled);
    await page.screenshot({ path: `${out}/cancelled.png`, fullPage: true });
    assert.equal(requests.filter(r => r.method === 'POST' && r.path.endsWith('/cancel')).length, 1);
    await page.reload();
    await page.getByRole('button', { name: '打开会话列表', exact: true }).click();
    await page.getByRole('button', { name: /^UI_REPLAY_PROBE/ }).click();
    await page.getByText('已停止', { exact: true }).waitFor();
    await page.waitForTimeout(500);
    fs.writeFileSync(`${out}/cancelled-refresh.txt`, await page.locator('body').innerText());
    await page.screenshot({ path: `${out}/cancelled-refresh.png`, fullPage: true });
    const latest = histories.at(-1);
    fs.writeFileSync(`${out}/persisted-history.json`, JSON.stringify(latest, null, 2));
    fs.writeFileSync(`${out}/result.json`, JSON.stringify({ replayPosts: posts.length, replay, cancellationPosts: 1, errors, history: latest }, null, 2));
    console.log('REPLAY_OK', JSON.stringify({ posts: posts.length, replay, body }));
  } finally {
    fs.writeFileSync(`${out}/browser-requests.json`, JSON.stringify(requests, null, 2));
    fs.writeFileSync(`${out}/page-errors.json`, JSON.stringify(errors, null, 2));
    await page.screenshot({ path: `${out}/last-page.png`, fullPage: true }).catch(() => {});
    await browser.close();
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
