import { Command } from 'commander';
import chalk from 'chalk';
import { execa } from 'execa';
import { readConfig, writeConfig } from '../config.js';

export const linkCommand = new Command('link')
  .description('Link the current directory as the working repo')
  .action(async () => {
    const cfg = await readConfig();
    try {
      const { stdout: top } = await execa('git', ['rev-parse', '--show-toplevel']);
      const { stdout: ref } = await execa('git', ['rev-parse', '--abbrev-ref', 'HEAD']);
      cfg.repoPath = top.trim();
      cfg.repoRef = ref.trim();
      await writeConfig(cfg);
      console.log(chalk.green(`✓ Linked ${cfg.repoPath} (${cfg.repoRef})`));
    } catch {
      console.error(chalk.red('Not a git repository.'));
      process.exit(1);
    }
  });
