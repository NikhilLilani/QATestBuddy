import { Command } from 'commander';
import chalk from 'chalk';
import { readConfig } from '../config.js';

export const runCommand = new Command('run')
  .description('Execute a server-orchestrated Playwright run locally')
  .argument('<runId>', 'Run ID from QAtestbuddy')
  .action(async (runId: string) => {
    const cfg = await readConfig();
    if (!cfg.token) {
      console.error(chalk.red('Not logged in. Run: qa-bridge login --token <t>'));
      process.exit(1);
    }
    if (!cfg.repoPath) {
      console.error(chalk.red('No repo linked. Run: qa-bridge link'));
      process.exit(1);
    }
    console.log(chalk.dim(`(Phase 4) Would connect WS to ${cfg.apiUrl}/bridge/ws for run ${runId}`));
  });
