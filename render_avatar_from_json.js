// render_avatar_from_json.js
// Usage:
//   node render_avatar_from_json.js path/to/user.json path/to/output.png

const fs = require('fs');
const path = require('path');
const puppeteer = require('puppeteer'); // you already have this installed

async function main() {
  const [, , userJsonPath, outputPath] = process.argv;

  if (!userJsonPath || !outputPath) {
    console.error('Usage: node render_avatar_from_json.js <user.json> <output.png>');
    process.exit(1);
  }

  // 1. Read user JSON written by Python
  let userData;
  try {
    const raw = fs.readFileSync(userJsonPath, 'utf8');
    userData = JSON.parse(raw);
  } catch (err) {
    console.error('Failed to read or parse user JSON:', err);
    process.exit(1);
  }

  // Support both shapes: {data: {...}} or {...}
  const user = userData.data ? userData.data : userData;

  // 2. Launch headless browser
  const browser = await puppeteer.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });

  try {
    const page = await browser.newPage();

    // A bit of padding around the avatar
    await page.setViewport({
      width: 260,
      height: 260,
      deviceScaleFactor: 2, // nicer resolution
    });

    // Minimal HTML shell with an avatar container
    const html = `
<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>Habitica Avatar Renderer</title>
    <style>
      html, body {
        margin: 0;
        padding: 0;
      }
      body {
        display: flex;
        align-items: flex-start;
        justify-content: flex-start;
        background: transparent;
      }
      /* Outer wrapper – can be bigger than the actual avatar */
      #avatar-container {
        width: 211px;
        height: 216px;
        overflow: hidden;
      }
    </style>
    <script>
      // The library uses this to detect habitica.com; we are not on habitica.com.
      // (The bundled code actually uses its own isHabitica(), but this does no harm.)
      window.isHabitica = function () {
        return false;
      };
    </script>
  </head>
  <body>
    <div id="avatar-container"></div>
  </body>
</html>
    `;

    await page.setContent(html, { waitUntil: 'load' });

    // 3. Inject the *local* browser bundle we built with browserify
    const avatarBundlePath = path.join(__dirname, 'habitica-avatar.bundle.js');
    await page.addScriptTag({ path: avatarBundlePath });

    // 4. Wait until the library is ready
    await page.waitForFunction(
      () => window.habiticaAvatar && typeof window.habiticaAvatar === 'function',
      { timeout: 15000 }
    );

    // 5. Render the avatar into #avatar-container,
    //    and tag the inner 140x147 element so we can screenshot it exactly.
    await page.evaluate((userObj) => {
      const container = document.querySelector('#avatar-container');
      if (!container) {
        throw new Error('avatar container not found');
      }

      // habiticaAvatar returns the inner avatar <div> (140x147)
      const avatarEl = window.habiticaAvatar({
        container,
        user: userObj,
        // Optional flags:
        // forceCostume: true,
        // forceEquipment: true,
        // forceClassMode: false,
      });

      // Mark the inner div so we can select it from Node
      avatarEl.id = 'avatar-inner';
    }, user);

    // 6. Wait until all <img> inside the avatar have actually loaded
    await page.waitForFunction(() => {
      const root =
        document.querySelector('#avatar-inner') ||
        document.querySelector('#avatar-container');
      if (!root) return false;
      const imgs = root.querySelectorAll('img');
      if (!imgs.length) return false;
      return Array.from(imgs).every(
        (img) => img.complete && img.naturalWidth > 0
      );
    }, { timeout: 10000 }).catch(() => {
      // If it times out, we still fall back to whatever is loaded
    });

    // Tiny extra buffer to let layout settle
    await new Promise((resolve) => setTimeout(resolve, 300));

    // 7. Screenshot only the inner avatar (140x147) → no white margins
    const avatarElement =
      (await page.$('#avatar-inner')) || (await page.$('#avatar-container'));
    if (!avatarElement) {
      throw new Error('Avatar element not found after rendering');
    }

    await avatarElement.screenshot({
      path: outputPath,
      omitBackground: true,
    });

    console.log('Avatar rendered to', outputPath);
  } catch (err) {
    console.error('Avatar rendering failed:', err);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error('Unexpected error:', err);
  process.exit(1);
});
