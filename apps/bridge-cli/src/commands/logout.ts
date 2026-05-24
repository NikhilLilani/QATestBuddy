import { Command } from 'commander';
import chalk from 'chalk';
import { readConfig, writeConfig } from '../config.js';

export const logoutCommand = new Command('logout')
  .description('Forget the saved bridge token')
  .action(async () => {
    const cfg = await readConfig();
    delete cfg.token;
    delete cfg.workspaceId;
    await writeConfig(cfg);
    console.log(chalk.green('✓ Logged out.'));
  });
