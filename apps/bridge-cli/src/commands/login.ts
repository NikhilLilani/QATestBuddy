import { Command } from 'commander';
import chalk from 'chalk';
import { readConfig, writeConfig } from '../config.js';

export const loginCommand = new Command('login')
  .description('Authenticate with QAtestbuddy using a workspace bridge token')
  .requiredOption('-t, --token <token>', 'Bridge token from Settings -> qa-bridge')
  .option('--api-url <url>', 'API URL override')
  .action(async (opts: { token: string; apiUrl?: string }) => {
    const cfg = await readConfig();
    cfg.token = opts.token;
    if (opts.apiUrl) cfg.apiUrl = opts.apiUrl;
    await writeConfig(cfg);
    console.log(chalk.green('✓ Saved bridge token.'));
  });
