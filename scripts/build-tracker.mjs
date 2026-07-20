#!/usr/bin/env node
/**
 * Build the serverkit-analytics tracker artifact.
 *
 *   node scripts/build-tracker.mjs           # build sk.min.js
 *   node scripts/build-tracker.mjs --check    # fail (exit 1) on drift
 *
 * Reads the readable source
 *   backend/tracker/sk.js
 * and writes the served artifact
 *   backend/tracker/sk.min.js
 * (the backend serves it at GET /api/v1/analytics/tracker.js — see
 * backend/analytics.py:_load_tracker_js, which prefers sk.min.js and falls
 * back to sk.js).
 *
 * Dependency-free, conservative minification: strip block comments, drop
 * full-line `//` comments, trim per-line whitespace, drop blank lines. Newlines
 * between statements are KEPT so ASI can't break. The source is authored to make
 * this safe (no block-comment tokens inside strings/regex, no inline `//`).
 */
import { promises as fs } from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');
const DIR = path.join(ROOT, 'backend', 'tracker');
const SRC = path.join(DIR, 'sk.js');
const OUT = path.join(DIR, 'sk.min.js');

const CHECK = process.argv.includes('--check');

function minify(src) {
    // Remove /* ... */ block comments (source avoids these tokens elsewhere).
    let out = src.replace(/\/\*[\s\S]*?\*\//g, '');
    const lines = out.split('\n')
        .map((l) => l.trim())
        .filter((l) => l.length && !l.startsWith('//'));
    return lines.join('\n') + '\n';
}

async function main() {
    const src = await fs.readFile(SRC, 'utf8');
    const built = minify(src);

    if (built.includes('/*') || built.includes('*/')) {
        console.error('Refusing to write: comment tokens survived minification.');
        process.exit(1);
    }
    const bytes = Buffer.byteLength(built, 'utf8');

    if (CHECK) {
        let current = null;
        try { current = await fs.readFile(OUT, 'utf8'); } catch { /* missing */ }
        if (current !== built) {
            console.error('Tracker artifact drift: sk.min.js is out of date.\n'
                + 'Run:  node scripts/build-tracker.mjs   and commit it.');
            process.exit(1);
        }
        console.log(`sk.min.js in sync (${bytes} bytes).`);
        return;
    }

    await fs.writeFile(OUT, built);
    console.log(`Built sk.min.js (${bytes} bytes, ${(bytes / 1024).toFixed(1)} KB).`);
    if (bytes > 4096) {
        console.warn('Warning: tracker exceeds the 4 KB budget.');
    }
}

main().catch((e) => { console.error(e); process.exit(1); });
