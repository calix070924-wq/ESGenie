import { chromium } from 'playwright';
import fs from 'fs';
const [url, pid, out, ...codes] = process.argv.slice(2);
const names = { 'E-4-1': '에너지 사용량', 'E-3-1': '온실가스 배출량', 'E-6-1': '폐기물 배출량', 'E-6-2': '재활용(순환이용)률' };
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
await page.addInitScript((id) => localStorage.setItem('esgenie-project', id), pid);
await page.goto(url);
await page.getByPlaceholder('질문·자료 검색').waitFor({ timeout: 30000 });
const report = {};
for (const code of codes) {
  await page.getByPlaceholder('질문·자료 검색').fill(names[code]);
  await page.waitForTimeout(500);
  const row = page.locator('.answer-table-row').filter({ hasText: '수치' }).first();
  report[code] = { row: await row.innerText() };
  await page.screenshot({ path: `${out}/${code}_list.png` });
  await row.locator('button').first().click();
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${out}/${code}_detail.png`, fullPage: true });
  report[code].detail = await page.innerText('body');
  await page.locator('.back-button').first().click();
  await page.getByPlaceholder('질문·자료 검색').waitFor({ timeout: 10000 });
}
fs.writeFileSync(`${out}/detail_text.json`, JSON.stringify(report, null, 1));
await browser.close();
