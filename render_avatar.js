// render_avatar.js
// Usage:
//   node render_avatar.js <apiUserId> <apiKey> <xClient> <output.png> [targetUserId]

const fs = require('fs');
const path = require('path');
const puppeteer = require('puppeteer');
const fetch = (...args) =>
  import('node-fetch').then(({ default: fetch }) => fetch(...args));

async function main() {
  const [
    ,
    ,
    apiUserId,
    apiKey,
    xClient,
    outputPath,
    targetUserId,
  ] = process.argv;

  if (!apiUserId || !apiKey || !xClient || !outputPath) {
    console.error(
      'Usage: node render_avatar.js <apiUserId> <apiKey> <xClient> <output.png> [targetUserId]',
    );
    process.exit(1);
  }

  const apiUrl = targetUserId
    ? `https://habitica.com/api/v3/members/${encodeURIComponent(targetUserId)}`
    : 'https://habitica.com/api/v3/user';

  // 1) Fetch user JSON from Habitica with proper headers
  const res = await fetch(apiUrl, {
    headers: {
      'x-api-user': apiUserId,
      'x-api-key': apiKey,
      'x-client': xClient,
      'Content-Type': 'application/json',
    },
  });

  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`Habitica API error ${res.status}: ${body}`);
  }

  const json = await res.json();
  const user = json.data;

  // 2) Launch Puppeteer and render avatar from local HTML + habitica-avatar.js
  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });

  const page = await browser.newPage();
  await page.setViewport({
    width: 140,
    height: 147,
    deviceScaleFactor: 2, // sharper image
  });

  // Minimal HTML shell — no external API calls here
  const html = `
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Avatar</title>
  <style>
    body {
      margin: 0;
      padding: 0;
      background: transparent;
    }
    #avatar {
      width: 140px;
      height: 147px;
      overflow: hidden;
    }
  </style>
</head>
<body>
  <div id="avatar"></div>
</body>
</html>`;

  await page.setContent(html, { waitUntil: 'load' });

  // Inject habitica-avatar.js from your local file
  await page.addScriptTag({ path: path.join(__dirname, 'habitica-avatar.js') });

  // Pass the user object into the page and build the avatar *locally*
  await page.evaluate((userObj) => {
    // habiticaAvatar is exposed globally by habitica-avatar.js
    window.habiticaAvatar({
      container: '#avatar',
      user: userObj,
      // You can add options here, e.g. forceCostume / forceEquipment / ignore
    });
  }, user);

  // Give the page a moment for images/css to settle (puppeteer >=22 removed waitForTimeout,
  // so just use a plain Promise delay here)
  await new Promise((resolve) => setTimeout(resolve, 2000));

  await page.screenshot({
    path: outputPath,
    omitBackground: true,
  });

  await browser.close();
  console.log(`Avatar saved to ${outputPath}`);
}

main().catch((err) => {
  console.error('Avatar rendering failed:', err);
  process.exit(1);
});
