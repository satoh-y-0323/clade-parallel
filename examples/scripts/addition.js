#!/usr/bin/env node

const args = process.argv.slice(2);

if (args.length !== 2) {
  console.error('Error: exactly 2 arguments required');
  process.exit(1);
}

const a = Number(args[0]);
const b = Number(args[1]);

if (isNaN(a) || isNaN(b)) {
  console.error('Error: both arguments must be numbers');
  process.exit(1);
}

console.log(`${a} + ${b} = ${a + b}`);
