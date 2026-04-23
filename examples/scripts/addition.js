#!/usr/bin/env node
/**
 * addition.js
 *
 * Demo script for the clade-parallel parallel-flow example.
 * Accepts two numeric arguments and prints their sum.
 *
 * Usage:
 *   node addition.js <a> <b>
 *
 * Example:
 *   node addition.js 3 5
 *   # => 3 + 5 = 8
 */

'use strict';

const args = process.argv.slice(2);

if (args.length !== 2) {
  console.error('Usage: node addition.js <a> <b>');
  process.exit(1);
}

const a = Number(args[0]);
const b = Number(args[1]);

if (Number.isNaN(a) || Number.isNaN(b)) {
  console.error('Error: both arguments must be numbers');
  process.exit(1);
}

const result = a + b;
console.log(`${a} + ${b} = ${result}`);
