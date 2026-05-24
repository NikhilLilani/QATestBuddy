#!/usr/bin/env node
import { Command } from 'commander';
import { loginCommand } from './commands/login.js';
import { linkCommand } from './commands/link.js';
import { runCommand } from './commands/run.js';
import { codegenCommand } from './commands/codegen.js';
import { statusCommand } from './commands/status.js';
import { logoutCommand } from './commands/logout.js';
import { listenCommand } from './commands/listen.js';

const program = new Command();

program
  .name('qa-bridge')
  .description('Local Playwright runner bridge for QAtestbuddy')
  .version('0.0.0');

program.addCommand(loginCommand);
program.addCommand(listenCommand);
program.addCommand(linkCommand);
program.addCommand(runCommand);
program.addCommand(codegenCommand);
program.addCommand(statusCommand);
program.addCommand(logoutCommand);

program.parseAsync().catch((err) => {
  console.error(err);
  process.exit(1);
});
