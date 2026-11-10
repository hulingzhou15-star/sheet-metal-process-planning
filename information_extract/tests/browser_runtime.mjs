import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
export function browserExecutable() {
  const configPath = path.join(root, '.local/settings.json');
  const local = fs.existsSync(configPath) ? JSON.parse(fs.readFileSync(configPath, 'utf8')) : {};
  const configured = process.env.SHEET_BROWSER_EXECUTABLE || local.browser_executable;
  if (configured) {
    if (!fs.existsSync(configured)) throw new Error('Configured browser does not exist: ' + configured);
    return configured;
  }
  for (const base of [process.env.PROGRAMFILES, process.env['PROGRAMFILES(X86)'], process.env.LOCALAPPDATA].filter(Boolean)) {
    for (const suffix of ['Google/Chrome/Application/chrome.exe', 'Microsoft/Edge/Application/msedge.exe']) {
      const candidate = path.join(base, suffix);
      if (fs.existsSync(candidate)) return candidate;
    }
  }
  return undefined; // Playwright's installed Chromium, if no system browser exists.
}
