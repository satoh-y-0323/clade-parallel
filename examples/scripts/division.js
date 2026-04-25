#!/usr/bin/env node
/**
 * division.js
 *
 * Demo script for the clade-parallel timing demo example.
 * Accepts two numeric arguments and prints their quotient.
 *
 * Usage:
 *   node division.js <a> <b>
 *
 * Example:
 *   node division.js 10 2
 *   # => 10 / 2 = 5
 */

'use strict';

const args = process.argv.slice(2);

if (args.length !== 2) {
  console.error('Usage: node division.js <a> <b>');
  process.exit(1);
}

const a = Number(args[0]);
const b = Number(args[1]);

if (Number.isNaN(a) || Number.isNaN(b)) {
  console.error('Error: both arguments must be numbers');
  process.exit(1);
}

if (b === 0) {
  console.error('Error: division by zero');
  process.exit(1);
}

const result = a / b;
console.log(`${a} / ${b} = ${result}`);
