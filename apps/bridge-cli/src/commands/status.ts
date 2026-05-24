import { Command } from 'commander';
import chalk from 'chalk';
import { readConfig } from '../config.js';

export const statusCommand = new Command('status')
  .description('Show current qa-bridge configuration')
  .action(async () => {
    const cfg = await readConfig();
    console.log(chalk.bold('qa-bridge status'));
    console.log(`  API URL:    ${cfg.apiUrl}`);
    console.log(`  Token:      ${cfg.token ? '••••' + cfg.token.slice(-4) : chalk.red('(none)')}`);
    console.log(`  Workspace:  ${cfg.workspaceId ?? chalk.dim('(none)')}`);
    console.log(`  Repo path:  ${cfg.repoPath ?? chalk.dim('(unlinked)')}`);
    console.log(`  Repo ref:   ${cfg.repoRef ?? chalk.dim('(unlinked)')}`);
  });
