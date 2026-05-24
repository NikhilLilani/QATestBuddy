import { Command } from 'commander';
import chalk from 'chalk';
import { execa } from 'execa';
import { readConfig } from '../config.js';

export const codegenCommand = new Command('codegen')
  .description('Wrap `npx playwright codegen` and capture selectors for the agent')
  .argument('<url>', 'URL to record against')
  .action(async (url: string) => {
    const cfg = await readConfig();
    if (!cfg.repoPath) {
      console.error(chalk.red('No repo linked. Run: qa-bridge link'));
      process.exit(1);
    }
    console.log(chalk.cyan(`Launching playwright codegen for ${url}...`));
    await execa('npx', ['playwright', 'codegen', url], {
      cwd: cfg.repoPath,
      stdio: 'inherit',
    });
  });
