'use strict';

const args = process.argv.slice(2);

if (args.length < 2) {
  process.stderr.write('Error: two arguments required\n');
  process.exit(1);
}

const a = Number(args[0]);
const b = Number(args[1]);

if (isNaN(a) || isNaN(b)) {
  process.stderr.write('Error: both arguments must be numbers\n');
  process.exit(1);
}

console.log(`${a} + ${b} = ${a + b}`);
process.exit(0);
