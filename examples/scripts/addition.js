#!/usr/bin/env node

'use strict';

const a = process.argv[2];
const b = process.argv[3];

if (a === undefined || b === undefined) {
  process.stderr.write('Error: two arguments required\n');
  process.exit(1);
}

const numA = Number(a);
const numB = Number(b);

if (isNaN(numA) || isNaN(numB)) {
  process.stderr.write('Error: both arguments must be numbers\n');
  process.exit(1);
}

console.log(numA + numB);
