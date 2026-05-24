import { promises as fs } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { z } from 'zod';

const ConfigSchema = z.object({
  apiUrl: z.string().url(),
  token: z.string().optional(),
  workspaceId: z.string().optional(),
  repoPath: z.string().optional(),
  repoRef: z.string().optional(),
});

export type BridgeConfig = z.infer<typeof ConfigSchema>;

const CONFIG_DIR = join(homedir(), '.qatb');
const CONFIG_FILE = join(CONFIG_DIR, 'config.json');

export async function readConfig(): Promise<BridgeConfig> {
  try {
    const raw = await fs.readFile(CONFIG_FILE, 'utf8');
    return ConfigSchema.parse(JSON.parse(raw));
  } catch {
    return ConfigSchema.parse({
      apiUrl: process.env.QATB_API_URL ?? 'http://localhost:8000',
    });
  }
}

export async function writeConfig(cfg: BridgeConfig): Promise<void> {
  await fs.mkdir(CONFIG_DIR, { recursive: true });
  await fs.writeFile(CONFIG_FILE, JSON.stringify(cfg, null, 2), { mode: 0o600 });
}
